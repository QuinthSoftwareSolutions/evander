from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
from types import MethodType, SimpleNamespace
from typing import Any, Mapping

from evander.admin import workflow_report_helpers
from evander.admin.payloads import AdminRevisionConflict, WorkflowFailureClusterReportPayload
from evander.admin.services import process_helpers, session_helpers, vm_helpers
from evander.admin.services.issue_setup_helpers import EffectiveIssueSetupConfig
from evander.admin.services.workflow_pack_history_service import WorkflowPackChangeActor
import evander.admin.services.teams_service as admin_teams_service
import evander.admin.services.worker_control_service as worker_control_service
import evander.admin.services.workflow_reports_service as workflow_reports_service
import pytest
from evander.admin import operations_helpers, privileged_helper
from evander.capacity_health import HealthState, TelemetryStatus
from evander.admin.composition import AdminRuntimeComponents, build_admin_runtime
from evander.admin.services.vm_agent.common_models import AgentCreateOperation
from evander.config import (
    DiscordIngestWorkerGlobalConfig,
    DiscordWakeWorkerGlobalConfig,
    ScheduledWakeWorkerGlobalConfig,
    TeamsIngestWorkerGlobalConfig,
)
from evander.effects.registry import EffectProviderFactory
from evander.effects.providers.schemas import AgentCreateEffectDefinition
from evander.execution.container_worker_repository import (
    ContainerWorkerExecutionRecord,
    ContainerWorkerExecutionRepository,
)
from evander.execution.container_worker_client import ContainerWorkerClient
from evander.execution.container_worker_client import ContainerWorkerClientError
from evander.communication_artifacts import CommunicationArtifactStore
from evander.messaging.targets import (
    SqliteCommunicationTargetsRepository,
    communication_targets_db_path,
)
from evander.events.internal import InternalEventBus
from evander.events.models import TriggerEvent
from evander.messaging.agent_prompt_queue import (
    AgentPromptQueueStore,
    QueuedAgentPromptRecord,
)
from evander.model_usage.runtime import register_model_usage_correlation_context
from evander.models import (
    ApprovalNotificationDeliveryRecord,
    ApprovalNotificationDeliveryStatus,
    ApprovalNotificationStatus,
    ApprovalRequest,
    ApprovalStatus,
    AgentRecord,
    ContainerSessionLifecycleState,
    DiscordMessageEnvelope,
    SessionManifest,
    ToolName,
    WakeDispatchRecord,
    WakeResumePromptPayload,
    VmActionResult,
    VmSummary,
)
from evander.provider_config import ProviderConfigResolver
from evander.pr_feedback import FeedbackEnvelope, PrFeedbackStore
from evander.messaging.agent_prompt_store import AgentPromptRecord
from evander.triggers.queue import SqliteTriggerEventQueue
from evander.triggers.queue import TriggerEventQueue
from evander.daemons.effect_orchestrator import (
    GateExecutionRecord,
    EffectExecutionRecord,
    JsonExecutionStore,
    JsonGateExecutionStore,
    SqliteExecutionStore,
    SqliteGateExecutionStore,
)
from evander.repositories.chat_assistant_sessions import JsonChatAssistantSessionRepository
from evander.repositories.orchestration import SqliteWorkflowConfigSnapshotRepository
from evander.worker.models import (
    WorkerFileWriteRecord,
    WorkerFileWriteRequest,
    WorkerSessionPrepareRequest,
    WorkerSessionRecord,
    WorkerTelemetryPayload,
)

admin_service = SimpleNamespace(
    _run_virsh=vm_helpers.run_virsh,
    _probe_agent_state=vm_helpers.probe_agent_state,
    _normalize_workflow_failure_signature=workflow_report_helpers._normalize_workflow_failure_signature,
    WorkflowFailureClusterReportPayload=WorkflowFailureClusterReportPayload,
    CONFORMANCE_REPORT_RUN_TIMEOUT_SECONDS=workflow_report_helpers.CONFORMANCE_REPORT_RUN_TIMEOUT_SECONDS,
    subprocess=subprocess,
    os=os,
    sys=__import__("sys"),
    _ssh_options=vm_helpers.ssh_options,
    EffectiveIssueSetupConfig=EffectiveIssueSetupConfig,
    _resolve_host_python_with_pip=process_helpers.resolve_host_python_with_pip,
    _wait_for_vm_ip=vm_helpers.wait_for_vm_ip,
    _require_valid_cron=session_helpers.require_valid_cron,
    EffectProviderFactory=EffectProviderFactory,
)


@pytest.fixture(autouse=True)
def _isolate_managed_git_authorized_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVANDER_GIT_USER", "evander-admin-tests-no-such-user")


UTC = timezone.utc
REPO_ROOT = Path(__file__).resolve().parents[1]


def _active_expires_at() -> datetime:
    return datetime.now(UTC) + timedelta(days=30)


def _discord_message(
    message_id: str,
    *,
    channel_id: str,
    created_at: datetime,
    content: str = "hello",
) -> DiscordMessageEnvelope:
    return DiscordMessageEnvelope(
        message_id=message_id,
        channel_id=channel_id,
        author_id="operator",
        author_name="Operator",
        content=content,
        created_at=created_at,
        raw={"id": message_id, "channel_id": channel_id, "content": content},
    )


def _assert_cursor_seeded_during_operation(
    cursor: str | None,
    *,
    before: datetime,
    after: datetime,
) -> None:
    assert cursor is not None
    parsed = datetime.fromisoformat(cursor.replace("Z", "+00:00")).astimezone(UTC)
    assert before <= parsed <= after


def write_config(tmp_path: Path, *, enable_directory_scoped_config: bool = False) -> Path:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "managed-git-repositories.json").write_text(
        json.dumps(
            {
                "schema": "evander.managed-git-repositories/v1",
                "schema_version": 1,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
                "repositories": {},
                "migrations": {"test-fixture": {"status": "applied"}},
            }
        ),
        encoding="utf-8",
    )
    config_path = tmp_path / "gateway.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
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


def wait_for_agent_create_operation(
    service: AdminRuntimeComponents,
    operation_id: str,
    *,
    statuses: set[str],
    timeout_seconds: float = 3.0,
) -> AgentCreateOperation:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        operation = service.vm_agent_admin.get_admin_agent_create_operation(operation_id)
        if operation.status in statuses:
            return operation
        time.sleep(0.01)
    operation = service.vm_agent_admin.get_admin_agent_create_operation(operation_id)
    raise AssertionError(
        f"Operation {operation_id} did not reach {sorted(statuses)}; "
        f"last status was {operation.status}"
    )


def write_failure_cluster_config(
    tmp_path: Path,
    *,
    orchestration_store_backend: str = "sqlite",
    orchestration_store_path: Path | None = None,
    event_queue_store_path: Path | None = None,
    effect_execution_store_path: Path | None = None,
    gate_execution_store_path: Path | None = None,
) -> Path:
    runtime_dir = tmp_path / "runtime"
    packs_dir = tmp_path / "config" / "workflow-packs"
    packs_dir.mkdir(parents=True, exist_ok=True)
    (packs_dir / "failure-clusters.v1.json").write_text(
        json.dumps(
            {
                "pack_id": "failure-clusters",
                "pack_version": "v1",
                "graphs": [
                    {
                        "id": "graph-ticket",
                        "name": "Ticket intake",
                        "description": "Handles ticket intake and branch updates.",
                        "directory_path": "root",
                        "user_ids": ["alice"],
                        "node_ids": ["trigger-ticket", "effect-push-branch"],
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
                        },
                        {
                            "id": "effect-push-branch",
                            "display_name": "Push branch",
                            "type": "effect:git:push-branch",
                            "managed_repo_id": "mr_0123456789abcdefabcd",
                            "source_ref": "refs/evander/operations/op-1/source",
                            "expected_commit_sha": "a" * 40,
                            "destination_branch": "feature/topic",
                        },
                    ],
                    "edges": [
                        {
                            "source_id": "trigger-ticket",
                            "target_id": "effect-push-branch",
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    config_root = tmp_path / "config" / "trigger-orchestrator"
    config_root.mkdir(parents=True, exist_ok=True)
    config_path = config_root / "config.json"
    normalized_orchestration_store_path = orchestration_store_path or (
        runtime_dir / "orchestration.db"
    )
    normalized_event_queue_store_path = event_queue_store_path or (
        runtime_dir / "trigger-orchestrator-events.json"
    )
    normalized_effect_execution_store_path = effect_execution_store_path or (
        runtime_dir / "effect-orchestrator-executions.json"
    )
    normalized_gate_execution_store_path = gate_execution_store_path or (
        runtime_dir / "gate-orchestrator-executions.json"
    )
    config_path.write_text(
        json.dumps(
            {
                "runtime_dir": str(runtime_dir),
                "orchestration_store_backend": orchestration_store_backend,
                "orchestration_store_path": str(normalized_orchestration_store_path),
                "event_queue_store_path": str(normalized_event_queue_store_path),
                "effect_execution_store_path": str(normalized_effect_execution_store_path),
                "gate_execution_store_path": str(normalized_gate_execution_store_path),
                "workflow_packs": ["failure-clusters.v1.json"],
            }
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


def _create_issue_test_repo(path: Path, *, content: str) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "Test User"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@example.com"],
        check=True,
    )
    (path / "README.md").write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-m", "initial"], check=True)
    return path


def test_create_local_issue_persists_yaml_and_managed_repo_binding(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    assert service.managed_git_admin._workflow_config_admin is service.workflow_config_admin
    repo = _create_issue_test_repo(tmp_path / "source-repo", content="source\n")

    issue = service.issue_git_admin.create_local_issue(
        title="Fix VM dashboard",
        body="Build a desktop dashboard for VM state.",
        repo_source=str(repo),
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
    assert payload[issue.issue_id]["managed_repo_id"] == issue.managed_repo_id
    assert "repo_source" not in payload[issue.issue_id]
    managed = service.managed_git_admin.get_repository(issue.managed_repo_id or "")
    assert managed["bindings"][0]["kind"] == "issues"
    assert managed["bindings"][0]["binding_id"] == issue.issue_id


def test_list_agents_includes_session_only_container_worker_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    created = service.directory_admin.create_child(parent="root", name="team")
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
    monkeypatch.setattr(
        service.vm_agent_admin,
        "list_vms",
        MethodType(lambda self, **kwargs: [], service.vm_agent_admin),
    )

    agents = service.vm_agent_admin.list_agents(
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


def test_list_agents_includes_matching_prompt_queue_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
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
    queue_store = AgentPromptQueueStore(service.config.runtime_dir / "queued-agent-prompts.json")
    queue_store.put(
        QueuedAgentPromptRecord(
            queue_key="pr-feedback:container-session:thread-1",
            target="container-session",
            vm_name="container-target",
            session_id="container-session",
            prompt="First queued feedback",
            effect_id="effect-agent-enqueue-prompt-1",
            created_at=datetime(2026, 6, 12, 4, 30, tzinfo=UTC),
            updated_at=datetime(2026, 6, 12, 4, 31, tzinfo=UTC),
            coalesced_count=3,
        )
    )
    queue_store.put(
        QueuedAgentPromptRecord(
            queue_key="scheduled-wake:container-session",
            target="container-session",
            vm_name="container-target",
            session_id="container-session",
            prompt="Second queued wake prompt",
            effect_id="scheduled-wake:container-session",
            created_at=datetime(2026, 6, 12, 4, 32, tzinfo=UTC),
            updated_at=datetime(2026, 6, 12, 4, 33, tzinfo=UTC),
        )
    )
    queue_store.put(
        QueuedAgentPromptRecord(
            queue_key="pr-feedback:other-session:thread-1",
            target="other-session",
            vm_name="other-target",
            session_id="other-session",
            prompt="Unrelated queued feedback",
            effect_id="effect-agent-enqueue-prompt-1",
        )
    )
    monkeypatch.setattr(
        service.vm_agent_admin,
        "list_vms",
        MethodType(lambda self, **kwargs: [], service.vm_agent_admin),
    )

    list_kwargs = {
        "directory_path": "root",
        "direct_only": True,
        "include_inactive": True,
        "reconcile_containers": False,
    }

    unprivileged_agents = service.vm_agent_admin.list_agents(**list_kwargs)
    assert len(unprivileged_agents) == 1
    assert unprivileged_agents[0].prompt_queue.record_count == 0
    assert unprivileged_agents[0].prompt_queue.records == []

    agents = service.vm_agent_admin.list_agents(**list_kwargs, include_prompt_queue=True)

    assert len(agents) == 1
    queue = agents[0].prompt_queue
    assert queue.record_count == 2
    assert queue.coalesced_count == 4
    assert queue.oldest_created_at == datetime(2026, 6, 12, 4, 30, tzinfo=UTC)
    assert queue.newest_updated_at == datetime(2026, 6, 12, 4, 33, tzinfo=UTC)
    assert queue.truncated_count == 0
    assert [record.queue_key for record in queue.records] == [
        "scheduled-wake:container-session",
        "pr-feedback:container-session:thread-1",
    ]
    assert queue.records[0].prompt_preview == "Second queued wake prompt"
    assert queue.records[1].coalesced_count == 3


@pytest.mark.parametrize(
    "metadata, expected",
    [
        (
            {
                "libvirt_vm_template": {
                    "image_ref": "dotnet-agent.qcow2",
                    "template_alias": "dotnet",
                }
            },
            {
                "backend": "libvirt_vm",
                "kind": "libvirt_vm_template",
                "label": "Libvirt VM template",
                "template_alias": "dotnet",
                "image_ref": "dotnet-agent.qcow2",
                "image_ref_sanitized": False,
                "missing_reason": None,
            },
        ),
        (
            {
                "libvirt_vm_template": {
                    "image_ref": "ubuntu-24.04-server-cloudimg-amd64.img",
                    "template_alias": "default",
                }
            },
            {
                "backend": "libvirt_vm",
                "kind": "libvirt_vm_template",
                "label": "Libvirt VM template",
                "template_alias": "default",
                "image_ref": "ubuntu-24.04-server-cloudimg-amd64.img",
                "image_ref_sanitized": False,
                "missing_reason": None,
            },
        ),
        (
            {
                "libvirt_vm_template": {
                    "image_ref": "/var/lib/libvirt/images/evander/dotnet-agent.qcow2",
                    "template_alias": None,
                }
            },
            {
                "backend": "libvirt_vm",
                "kind": "libvirt_vm_template",
                "label": "Libvirt VM template",
                "template_alias": None,
                "image_ref": "dotnet-agent.qcow2",
                "image_ref_sanitized": True,
                "missing_reason": None,
            },
        ),
        (
            {
                "libvirt_vm_template": {
                    "image_ref": "ubuntu-24.04-server-cloudimg-amd64.img",
                }
            },
            {
                "backend": "libvirt_vm",
                "kind": "libvirt_vm_template",
                "label": "Libvirt VM template",
                "template_alias": None,
                "image_ref": "ubuntu-24.04-server-cloudimg-amd64.img",
                "image_ref_sanitized": False,
                "missing_reason": None,
            },
        ),
    ],
)
def test_session_runtime_image_payload_normalizes_libvirt_vm_templates(
    tmp_path: Path, metadata: dict[str, object], expected: dict[str, object]
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    session = SessionManifest(
        session_id="session-1",
        vm_provider="libvirt_vm",
        allowed_tools=[],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        metadata=metadata,
    )

    payload = service.vm_agent_admin.session_runtime_image_payload(session)

    assert payload == expected
    assert session.metadata == metadata


@pytest.mark.parametrize(
    "metadata",
    [
        {"legacy": True},
        {"libvirt_vm_template": "legacy"},
    ],
)
def test_session_runtime_image_payload_marks_missing_legacy_libvirt_vm_template_metadata(
    tmp_path: Path, metadata: dict[str, object]
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    session = SessionManifest(
        session_id="session-legacy",
        vm_provider="libvirt_vm",
        allowed_tools=[],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        metadata=metadata,
    )

    payload = service.vm_agent_admin.session_runtime_image_payload(session)

    assert payload == {
        "backend": "libvirt_vm",
        "kind": "libvirt_vm_template",
        "label": "Legacy session: VM template metadata unavailable",
        "template_alias": None,
        "image_ref": None,
        "image_ref_sanitized": False,
        "missing_reason": "legacy_missing_libvirt_vm_template",
    }
    assert session.metadata == metadata


def test_session_runtime_image_payload_returns_container_worker_image_details(
    tmp_path: Path,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    metadata = {
        "execution_backend": "container_worker_vm",
        "container_worker": {
            "image": "runner:latest",
            "image_alias": "default",
        },
    }
    session = SessionManifest(
        session_id="container-session",
        vm_provider="container_worker_vm",
        allowed_tools=[],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        metadata=metadata,
    )

    payload = service.vm_agent_admin.session_runtime_image_payload(session)

    assert payload == {
        "backend": "container_worker_vm",
        "kind": "container_worker_image",
        "label": "Container worker image",
        "image_alias": "default",
        "image_ref": "runner:latest",
        "missing_reason": None,
    }
    assert session.metadata == metadata


def test_list_agents_descendant_scope_includes_child_directory_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    team = service.directory_admin.create_child(parent="root", name="team")
    assert team.ok is True and team.data is not None
    team_path = team.data.details.directory.path
    child = service.directory_admin.create_child(parent=team_path, name="child")
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

    monkeypatch.setattr(
        service.vm_agent_admin,
        "list_vms",
        MethodType(lambda self, **kwargs: [], service.vm_agent_admin),
    )

    descendant_agents = service.vm_agent_admin.list_agents(
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

    direct_agents = service.vm_agent_admin.list_agents(
        directory_path=team_path,
        direct_only=True,
        include_inactive=True,
        reconcile_containers=False,
    )
    assert {agent.session_id for agent in direct_agents} == {"vm-team", "container-team"}


def test_read_session_artifact_text_routes_container_sessions_to_worker_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
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
        service.vm_agent_admin,
        "container_worker_backend",
        lambda **kwargs: calls.append({"backend_kwargs": kwargs}) or FakeContainerBackend(),
    )

    result = service.vm_agent_admin.read_session_artifact_text(
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
    service = build_admin_runtime(write_config(tmp_path))
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

    monkeypatch.setattr(service.vm_agent_admin, "vm_exec", fake_vm_exec)

    result = service.vm_agent_admin.read_session_artifact_text(
        session_id="vm-session",
        path="reports/summary.txt",
    )

    assert calls[0]["target"] == "vm-session"
    assert calls[0]["environment"] == {"EVANDER_ARTIFACT_RELATIVE_PATH": "reports/summary.txt"}
    assert "/artifacts" in str(calls[0]["command"])
    assert result["content"] == "vm summary\n"
    assert result["execution_backend"] == "libvirt_vm"


def test_list_agents_groups_container_executions_under_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
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
    monkeypatch.setattr(
        service.vm_agent_admin,
        "list_vms",
        MethodType(lambda self, **kwargs: [], service.vm_agent_admin),
    )

    agents = service.vm_agent_admin.list_agents(
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
    sessions = {
        item.session_id: item for item in service.vm_agent_admin.list_sessions(include_expired=True)
    }
    lifecycle = sessions["container-session"].container_lifecycle
    assert lifecycle is not None
    assert lifecycle.state == ContainerSessionLifecycleState.RUNNING


def test_list_agents_prefers_failed_codex_outcome_for_container_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
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
            execution_id="exec-current",
            session_id="container-session",
            worker_job_id="job-current",
            worker_url="http://worker.test",
            target_name="container-target",
            last_status="removed",
            cleanup_status="cleanup_succeeded",
            metadata={
                "codex_outcome": {
                    "status": "failed",
                    "reason": "codex_no_final_message",
                    "message": "Codex ended without a final agent message.",
                }
            },
        )
    )
    monkeypatch.setattr(
        service.vm_agent_admin,
        "list_vms",
        MethodType(lambda self, **kwargs: [], service.vm_agent_admin),
    )

    agents = service.vm_agent_admin.list_agents(
        directory_path="root",
        direct_only=True,
        include_inactive=True,
        reconcile_containers=False,
    )

    assert agents[0].status == "failed"
    assert agents[0].host["executions"][0]["status"] == "failed"
    assert agents[0].host["executions"][0]["last_status"] == "removed"


def test_list_agents_summary_mode_skips_container_reconciliation_and_omits_vm_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
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
            execution_id="exec-current",
            session_id="container-session",
            worker_job_id="job-current",
            worker_url="http://worker.test",
            target_name="container-target",
            last_status="running",
            metadata={"image": "runner:latest"},
        )
    )
    list_vms_calls: list[dict[str, object]] = []

    def list_vms(self: AdminRuntimeComponents, **kwargs: object) -> list[object]:
        list_vms_calls.append(kwargs)
        return []

    inspect_calls: list[str] = []

    class FakeContainerWorkerBackend:
        def inspect(self, execution_id: str) -> None:
            inspect_calls.append(execution_id)

    def fake_container_worker_backend(
        self: AdminRuntimeComponents, **_kwargs: object
    ) -> FakeContainerWorkerBackend:
        return FakeContainerWorkerBackend()

    monkeypatch.setattr(
        service.vm_agent_admin, "list_vms", MethodType(list_vms, service.vm_agent_admin)
    )
    monkeypatch.setattr(
        service.vm_agent_admin,
        "container_worker_backend",
        MethodType(fake_container_worker_backend, service.vm_agent_admin),
    )

    summary_agents = service.vm_agent_admin.list_agents(
        directory_path="root",
        direct_only=True,
        include_inactive=True,
        reconcile_containers=False,
        omit_vm_state=True,
        backend="all",
    )

    assert [(agent.agent_id, agent.backend, agent.status) for agent in summary_agents] == [
        ("container-session", "container_worker_vm", "running")
    ]
    assert list_vms_calls[-1]["omit_state"] is True
    assert inspect_calls == []

    deep_agents = service.vm_agent_admin.list_agents(
        directory_path="root",
        direct_only=True,
        include_inactive=True,
        reconcile_containers=True,
        omit_vm_state=False,
        backend="all",
    )

    assert [(agent.agent_id, agent.backend, agent.status) for agent in deep_agents] == [
        ("container-session", "container_worker_vm", "running")
    ]
    assert list_vms_calls[-1]["omit_state"] is False
    assert inspect_calls == ["exec-current"]


def test_list_agents_summary_mode_skips_stale_session_effective_policy_for_missing_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="stale-session",
            vm_id="stale-vm",
            vm_provider="libvirt",
            directory_path="root/missing-team",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )

    monkeypatch.setattr(
        service.vm_agent_admin,
        "list_vms",
        MethodType(lambda self, **kwargs: [], service.vm_agent_admin),
    )

    summary_agents = service.vm_agent_admin.list_agents(
        directory_path="root",
        direct_only=False,
        include_inactive=True,
        reconcile_containers=False,
        omit_vm_state=True,
        summary_only=True,
        backend="all",
    )

    assert [
        (agent.agent_id, agent.directory_path, agent.effective_tool_policy)
        for agent in summary_agents
    ] == [("stale-vm", "root/missing-team", None)]

    with pytest.raises(ValueError, match="references missing directory_path 'root/missing-team'"):
        service.vm_agent_admin.list_agents(
            directory_path="root",
            direct_only=False,
            include_inactive=True,
            reconcile_containers=False,
            omit_vm_state=False,
            summary_only=False,
            backend="all",
        )


def test_capacity_session_statuses_use_summary_agent_listing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    list_agents_calls: list[dict[str, object]] = []

    def list_agents(self: AdminRuntimeComponents, **kwargs: object) -> list[AgentRecord]:
        list_agents_calls.append(kwargs)
        return [
            AgentRecord(
                agent_id="container-session",
                backend="container_worker_vm",
                target_name="container-target",
                session_id="container-session",
                directory_path="root",
                status="running",
                updated_at=datetime(2026, 6, 13, tzinfo=UTC),
                host={"kind": "container_worker_session"},
            )
        ]

    monkeypatch.setattr(
        service.vm_agent_admin,
        "list_agents",
        MethodType(list_agents, service.vm_agent_admin),
    )

    statuses = service.capacity_health_admin._capacity_session_statuses()

    assert statuses == {"container-session": "running"}
    assert list_agents_calls == [
        {
            "include_inactive": True,
            "reconcile_containers": False,
            "all_directories": True,
            "omit_vm_state": True,
            "summary_only": True,
        }
    ]


def test_list_agents_ignores_execution_cleanup_success_for_session_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
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
    monkeypatch.setattr(
        service.vm_agent_admin,
        "list_vms",
        MethodType(lambda self, **kwargs: [], service.vm_agent_admin),
    )

    agents = service.vm_agent_admin.list_agents(
        directory_path="root",
        direct_only=True,
        include_inactive=True,
        reconcile_containers=False,
    )

    assert [(agent.agent_id, agent.status) for agent in agents] == [("container-session", "idle")]
    sessions = {
        item.session_id: item for item in service.vm_agent_admin.list_sessions(include_expired=True)
    }
    lifecycle = sessions["container-session"].container_lifecycle
    assert lifecycle is not None
    assert lifecycle.state == ContainerSessionLifecycleState.STOPPED
    assert lifecycle.actions.cleanup.supported is True


def test_list_agents_still_returns_container_sessions_when_vm_inventory_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
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

    def fail_list_vms(self: AdminRuntimeComponents, **_kwargs: object) -> list[object]:
        raise RuntimeError("sudo: a password is required")

    monkeypatch.setattr(
        service.vm_agent_admin, "list_vms", MethodType(fail_list_vms, service.vm_agent_admin)
    )

    agents = service.vm_agent_admin.list_agents(
        directory_path="root",
        direct_only=True,
        include_inactive=True,
        reconcile_containers=False,
    )

    assert [(agent.agent_id, agent.backend, agent.status) for agent in agents] == [
        ("container-session", "container_worker_vm", "prepared")
    ]
    container_agents = service.vm_agent_admin.list_agents(
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
        service.vm_agent_admin.list_agents(
            directory_path="root",
            direct_only=True,
            include_inactive=True,
            reconcile_containers=False,
            backend="libvirt_vm",
        )


def test_list_agents_surfaces_stale_vm_session_when_inventory_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="assistant-shared",
            vm_id="assistant-shared",
            vm_provider="libvirt",
            directory_path="root",
            allowed_tools=[],
            allowed_contact_ids=["ops"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={"labels": {"system": "chat_assistant"}},
        )
    )

    def fail_list_vms(self: AdminRuntimeComponents, **_kwargs: object) -> list[object]:
        raise RuntimeError("virsh: command not found")

    monkeypatch.setattr(
        service.vm_agent_admin, "list_vms", MethodType(fail_list_vms, service.vm_agent_admin)
    )

    agents = service.vm_agent_admin.list_agents(
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
    assert agents[0].host["session_policy"]["allowed_contact_ids"] == ["ops"]


def test_read_capacity_health_aggregates_sessions_workers_queue_and_prompts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    created = service.directory_admin.create_child(parent="root", name="team")
    assert created.ok is True and created.data is not None
    team_path = created.data.details.directory.path
    now = datetime(2026, 5, 21, 12, 0, tzinfo=UTC)
    monkeypatch.setattr("evander.admin.services.capacity_health_service._utc_now", lambda: now)

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
        service.workflow_config_admin,
        "_workflow_run_store_paths",
        MethodType(lambda self: workflow_paths, service.workflow_config_admin),
    )

    class FakeContainerWorkerClient:
        def __init__(
            self,
            *,
            base_url: str,
            token_env: str = "EVANDER_WORKER_TOKEN",
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
                    "EVANDER_WORKER_TOKEN"
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

    monkeypatch.setattr(
        "evander.admin.services.capacity_health_service.ContainerWorkerClient",
        FakeContainerWorkerClient,
    )

    def fake_list_agents(self: AdminRuntimeComponents, **_kwargs: object) -> list[AgentRecord]:
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

    monkeypatch.setattr(
        service.vm_agent_admin,
        "list_agents",
        MethodType(fake_list_agents, service.vm_agent_admin),
    )

    health = service.capacity_health_admin.read_capacity_health()
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
    assert (
        worker_entries["http://worker-missing.test"].telemetry_status
        is TelemetryStatus.PERMISSION_DENIED
    )
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
    service = build_admin_runtime(write_config(tmp_path))
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

    descriptors = service.capacity_health_admin._capacity_worker_descriptors(
        service.runtime.session_repository.list_sessions()
    )

    assert [descriptor["worker_url"] for descriptor in descriptors] == [
        "http://worker-current.test"
    ]
    assert descriptors[0]["discovered_from"] == ["session:container-current"]


def test_list_agents_all_directories_includes_vm_and_container_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    created = service.directory_admin.create_child(parent="root", name="team")
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

    def fake_list_vms(self: AdminRuntimeComponents, **kwargs: Any) -> list[VmSummary]:
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

    monkeypatch.setattr(
        service.vm_agent_admin, "list_vms", MethodType(fake_list_vms, service.vm_agent_admin)
    )

    agents = service.vm_agent_admin.list_agents(
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
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    created = service.directory_admin.create_child(parent="root", name="team")
    assert created.ok is True and created.data is not None
    team_path = created.data.details.directory.path
    write_result = service.operations_admin.write_directory_domain_config(
        team_path,
        "providers",
        {
            "container_worker_base_url": "http://team-worker.test:11888",
            "container_worker_default_image": "runner-base:latest",
            "container_worker_images": {
                "default": "runner-base:latest",
                "dotnet-playwright": "runner-dotnet-playwright:2026-06-05",
            },
            "container_worker_token_env": "TEAM_WORKER_TOKEN",
        },
    )
    assert write_result.ok is True
    service.skill_admin.create_skill({"name": "demo.skill", "content": "# Demo\n"})
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

    result = service.vm_agent_admin.create_admin_agent(
        {
            "backend": "container_worker_vm",
            "session_id": "container-session",
            "target_name": "container-target",
            "directory_path": team_path,
            "container_image": "dotnet-playwright",
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
                "container_worker": {
                    "image": "runner-dotnet-playwright:2026-06-05",
                    "image_alias": "dotnet-playwright",
                },
                "worker_url": "http://team-worker.test:11888",
            },
        )
    ]
    assert result["container_image"] == "runner-dotnet-playwright:2026-06-05"
    assert result["container_image_alias"] == "dotnet-playwright"
    assert "/home/agent/.evander/session.env" in written_paths
    assert "/home/agent/.profile" in written_paths
    assert "/home/agent/.codex/config.toml" in written_paths
    assert "/home/agent/.nuget/NuGet/NuGet.Config" in written_paths
    assert "/home/agent/.config/evander/codex/skills/demo.skill/SKILL.md" in written_paths
    manifest = service.runtime.session_repository.get_session("container-session")
    assert manifest is not None
    assert manifest.metadata["worker_url"] == "http://team-worker.test:11888"
    assert manifest.metadata["container_worker"] == {
        "image": "runner-dotnet-playwright:2026-06-05",
        "image_alias": "dotnet-playwright",
    }
    agents = service.vm_agent_admin.list_agents(
        directory_path=team_path,
        direct_only=True,
        include_inactive=True,
        backend="container_worker_vm",
        reconcile_containers=False,
    )
    assert len(agents) == 1
    assert agents[0].host["image"] == "runner-dotnet-playwright:2026-06-05"
    assert agents[0].host["image_alias"] == "dotnet-playwright"


def test_create_container_agent_fast_forwards_discord_cursor_for_new_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    service.operations_admin.write_directory_domain_config(
        "root",
        "providers",
        {
            "container_worker_base_url": "http://worker.test:11888",
            "container_worker_token_env": "WORKER_TOKEN",
        },
    )
    communication_targets = SqliteCommunicationTargetsRepository(
        communication_targets_db_path(service.config.runtime_dir)
    )
    communication_targets.write_overlay(
        "root",
        {
            "targets": [
                {
                    "id": "discord-ops",
                    "provider": "discord",
                    "kind": "discord",
                    "address": "123456789012345678",
                    "policy": {"capabilities": ["read", "write"]},
                }
            ]
        },
    )
    service.runtime.discord_inbox_service.store.add_messages_if_new(
        [
            _discord_message(
                "msg-1",
                channel_id="123456789012345678",
                created_at=datetime(2026, 3, 21, tzinfo=UTC),
            ),
            _discord_message(
                "msg-2",
                channel_id="123456789012345678",
                created_at=datetime(2026, 6, 23, 18, 53, 43, tzinfo=UTC),
            ),
        ]
    )

    def prepare_session(
        self: ContainerWorkerClient, request: WorkerSessionPrepareRequest
    ) -> WorkerSessionRecord:
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
    monkeypatch.setattr(
        ContainerWorkerClient,
        "write_files",
        lambda self, session_id, request: WorkerFileWriteRecord(
            session_id=session_id,
            copied_count=len(request.files),
            copied_paths=[file.destination_path for file in request.files],
        ),
    )

    before = datetime.now(UTC)
    result = service.vm_agent_admin.create_admin_agent(
        {
            "backend": "container_worker_vm",
            "session_id": "container-session",
            "target_name": "container-target",
            "communication_target_ids": ["discord-ops"],
        }
    )
    after = datetime.now(UTC)

    assert result["success"] is True
    manifest = service.runtime.session_repository.get_session("container-session")
    assert manifest is not None
    assert manifest.discord_channel_allowlist == ["discord-ops"]
    _assert_cursor_seeded_during_operation(
        service.runtime.discord_inbox_service.store.get_session_cursor(
            "container-session", "123456789012345678"
        ),
        before=before,
        after=after,
    )
    assert (
        service.runtime.discord_inbox_service.store.has_unread_messages(
            session_id="container-session",
            channel_ids=["123456789012345678"],
        )
        is False
    )


def test_create_container_agent_infers_claude_provider_from_anthropic_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    write_result = service.operations_admin.write_directory_domain_config(
        "root",
        "providers",
        {
            "container_worker_base_url": "http://worker.test:11888",
            "model_provider_mode": "anthropic",
        },
    )
    assert write_result.ok is True

    prepared_metadata: list[dict[str, object]] = []
    written_files: dict[str, str] = {}

    def prepare_session(
        self: ContainerWorkerClient, request: WorkerSessionPrepareRequest
    ) -> WorkerSessionRecord:
        del self
        prepared_metadata.append(dict(request.metadata))
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
        del self
        assert session_id == "claude-session"
        for file in request.files:
            written_files[file.destination_path] = file.content
        return WorkerFileWriteRecord(
            session_id=session_id,
            copied_count=len(request.files),
            copied_paths=[file.destination_path for file in request.files],
        )

    monkeypatch.setattr(ContainerWorkerClient, "write_files", write_files)

    result = service.vm_agent_admin.create_admin_agent(
        {
            "backend": "container_worker_vm",
            "session_id": "claude-session",
        }
    )

    assert result["success"] is True
    manifest = service.runtime.session_repository.get_session("claude-session")
    assert manifest is not None
    assert manifest.metadata["agent_provider"] == "claude"
    assert result["container_image"] == "evander-runner:latest"
    assert result["container_image_alias"] is None
    assert prepared_metadata == [
        {
            "execution_backend": "container_worker_vm",
            "directory_path": "root",
            "target_name": "claude-session",
            "labels": {},
            "container_worker": {
                "image": "evander-runner:latest",
                "image_alias": None,
            },
            "worker_url": "http://worker.test:11888",
        }
    ]
    assert manifest.metadata["container_worker"] == {
        "image": "evander-runner:latest",
        "image_alias": None,
    }
    assert "AGENT_PROVIDER=claude\n" in written_files["/home/agent/.evander/session.env"]
    assert (
        "ANTHROPIC_BASE_URL=http://192.168.250.1:11434/session/claude-session\n"
        in written_files["/home/agent/.evander/session.env"]
    )
    assert "claude-sonnet-4-6" in written_files["/home/agent/.codex/config.toml"]
    assert '"commit": ""' in written_files["/home/agent/.claude/settings.json"]
    assert (
        "http://192.168.250.1:11434/session/claude-session"
        in written_files["/home/agent/.claude/settings.json"]
    )
    assert "/home/agent/.claude/mcp-config.json" in written_files


def test_list_container_agent_uses_session_image_before_latest_execution_image(
    tmp_path: Path,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
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
                "container_worker": {
                    "image": "runner-base:latest",
                    "image_alias": "default",
                },
            },
        )
    )
    repository = ContainerWorkerExecutionRepository(
        service.config.runtime_dir / "container-worker-executions.json"
    )
    repository.put(
        ContainerWorkerExecutionRecord(
            execution_id="container-execution",
            session_id="container-session",
            worker_job_id="worker-job",
            worker_url="http://worker.test:11888",
            target_name="container-target",
            started_at=datetime.now(UTC),
            last_status="running",
            directory_path="root",
            metadata={"image": "runner-execution:latest"},
        )
    )

    agents = service.vm_agent_admin.list_agents(
        directory_path="root",
        direct_only=True,
        include_inactive=True,
        backend="container_worker_vm",
        reconcile_containers=False,
    )

    assert len(agents) == 1
    assert agents[0].host["image"] == "runner-base:latest"
    assert agents[0].host["image_alias"] == "default"


def test_list_container_agent_falls_back_to_latest_execution_image_when_session_image_missing(
    tmp_path: Path,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
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
            execution_id="container-execution",
            session_id="container-session",
            worker_job_id="worker-job",
            worker_url="http://worker.test:11888",
            target_name="container-target",
            started_at=datetime.now(UTC),
            last_status="running",
            directory_path="root",
            metadata={"image": "runner-execution:latest"},
        )
    )

    agents = service.vm_agent_admin.list_agents(
        directory_path="root",
        direct_only=True,
        include_inactive=True,
        backend="container_worker_vm",
        reconcile_containers=False,
    )

    assert len(agents) == 1
    assert agents[0].host["image"] == "runner-execution:latest"
    assert "image_alias" not in agents[0].host


def test_create_container_agent_accepts_explicit_default_image_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    write_result = service.operations_admin.write_directory_domain_config(
        "root",
        "providers",
        {
            "container_worker_base_url": "http://worker.test:11888",
            "container_worker_default_image": "runner-base:latest",
        },
    )
    assert write_result.ok is True

    prepared_metadata: list[dict[str, object]] = []

    def prepare_session(
        self: ContainerWorkerClient, request: WorkerSessionPrepareRequest
    ) -> WorkerSessionRecord:
        del self
        prepared_metadata.append(dict(request.metadata))
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
        del self, session_id, request
        return WorkerFileWriteRecord(
            session_id="default-image-session",
            copied_count=0,
            copied_paths=[],
        )

    monkeypatch.setattr(ContainerWorkerClient, "write_files", write_files)

    result = service.vm_agent_admin.create_admin_agent(
        {
            "backend": "container_worker_vm",
            "session_id": "default-image-session",
            "container_image": "runner-base:latest",
        }
    )

    assert result["container_image"] == "runner-base:latest"
    assert result["container_image_alias"] is None
    assert prepared_metadata == [
        {
            "execution_backend": "container_worker_vm",
            "directory_path": "root",
            "target_name": "default-image-session",
            "labels": {},
            "container_worker": {
                "image": "runner-base:latest",
                "image_alias": None,
            },
            "worker_url": "http://worker.test:11888",
        }
    ]
    manifest = service.runtime.session_repository.get_session("default-image-session")
    assert manifest is not None
    assert manifest.metadata["container_worker"] == {
        "image": "runner-base:latest",
        "image_alias": None,
    }


def test_create_container_agent_rejects_unknown_container_image_selector_before_prepare(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    write_result = service.operations_admin.write_directory_domain_config(
        "root",
        "providers",
        {
            "container_worker_base_url": "http://worker.test:11888",
            "container_worker_default_image": "runner-base:latest",
            "container_worker_images": {
                "default": "runner-base:latest",
                "dotnet-playwright": "runner-dotnet-playwright:2026-06-05",
            },
        },
    )
    assert write_result.ok is True

    def fail_prepare(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("container session should not be prepared")

    monkeypatch.setattr(ContainerWorkerClient, "prepare_session", fail_prepare)

    with pytest.raises(ValueError, match="unknown container worker image selector"):
        service.vm_agent_admin.create_admin_agent(
            {
                "backend": "container_worker_vm",
                "session_id": "missing-image-session",
                "container_image": "missing-selector",
            }
        )


def test_create_admin_agent_libvirt_rejects_unknown_vm_image_selector_before_create_vm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    write_result = service.operations_admin.write_directory_domain_config(
        "root",
        "vm",
        {
            "libvirt_vm_default_template": "default",
            "libvirt_vm_templates": {
                "default": "ubuntu-24.04-server-cloudimg-amd64.img",
                "gpu": "ubuntu-24.04-gpu.img",
            },
        },
    )
    assert write_result.ok is True

    def fail_create_vm(self: AdminRuntimeComponents, **_kwargs: object) -> object:
        raise AssertionError("create_vm should not be called for an unknown vm_image")

    monkeypatch.setattr(
        service.vm_agent_admin, "create_vm", MethodType(fail_create_vm, service.vm_agent_admin)
    )

    with pytest.raises(ValueError, match="unknown libvirt VM template selector") as excinfo:
        service.vm_agent_admin.create_admin_agent(
            {
                "backend": "libvirt_vm",
                "vm_name": "lab-vm",
                "vm_image": "missing-selector",
            }
        )
    message = str(excinfo.value)
    assert "default" in message
    assert "gpu" in message


def test_create_admin_agent_libvirt_resolves_vm_image_alias_into_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    write_result = service.operations_admin.write_directory_domain_config(
        "root",
        "vm",
        {
            "libvirt_vm_default_template": "default",
            "libvirt_vm_templates": {
                "default": "ubuntu-24.04-server-cloudimg-amd64.img",
                "gpu": "ubuntu-24.04-gpu.img",
            },
        },
    )
    assert write_result.ok is True

    captured: dict[str, object] = {}

    def create_vm(self: AdminRuntimeComponents, **payload: object) -> VmActionResult:
        captured.update(payload)
        return VmActionResult(
            success=True,
            vm_name=payload["vm_name"],
            action="create_vm",
            message="created",
            provider="libvirt",
        )

    monkeypatch.setattr(
        service.vm_agent_admin, "create_vm", MethodType(create_vm, service.vm_agent_admin)
    )

    result = service.vm_agent_admin.create_admin_agent(
        {
            "backend": "libvirt_vm",
            "vm_name": "lab-vm",
            "vm_image": "gpu",
            "model_provider_mode": "local_openai",
        }
    )

    assert result["success"] is True
    assert result["vm_image"] == "ubuntu-24.04-gpu.img"
    assert result["vm_image_alias"] == "gpu"
    assert captured["metadata"] == {
        "model_provider_mode": "local_openai",
        "libvirt_vm_template": {
            "image_ref": "ubuntu-24.04-gpu.img",
            "template_alias": "gpu",
        },
    }


def test_create_admin_agent_libvirt_records_default_template_when_vm_image_omitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))

    captured: dict[str, object] = {}

    def create_vm(self: AdminRuntimeComponents, **payload: object) -> VmActionResult:
        captured.update(payload)
        return VmActionResult(
            success=True,
            vm_name=payload["vm_name"],
            action="create_vm",
            message="created",
            provider="libvirt",
        )

    monkeypatch.setattr(
        service.vm_agent_admin, "create_vm", MethodType(create_vm, service.vm_agent_admin)
    )

    result = service.vm_agent_admin.create_admin_agent(
        {
            "backend": "libvirt_vm",
            "vm_name": "lab-vm",
        }
    )

    assert result["success"] is True
    assert captured["metadata"] == {
        "libvirt_vm_template": {
            "image_ref": "ubuntu-24.04-server-cloudimg-amd64.img",
            "template_alias": "default",
        }
    }


def test_create_admin_agent_libvirt_forwards_ttl_seconds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    captured: dict[str, object] = {}

    def create_vm(self: AdminRuntimeComponents, **payload: object) -> VmActionResult:
        captured.update(payload)
        return VmActionResult(
            success=True,
            vm_name=payload["vm_name"],
            action="create_vm",
            message="created",
            provider="libvirt",
        )

    monkeypatch.setattr(
        service.vm_agent_admin, "create_vm", MethodType(create_vm, service.vm_agent_admin)
    )

    result = service.vm_agent_admin.create_admin_agent(
        {
            "backend": "libvirt_vm",
            "vm_name": "lab-vm",
            "ttl_seconds": 604800,
        }
    )

    assert result["success"] is True
    assert captured["ttl_seconds"] == 604800
    assert captured["ttl_hours"] is None


def test_create_admin_agent_libvirt_never_allows_existing_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))

    captured: dict[str, object] = {}

    def create_vm(self: AdminRuntimeComponents, **payload: object) -> VmActionResult:
        captured.update(payload)
        return VmActionResult(
            success=True,
            vm_name=payload["vm_name"],
            action="create_vm",
            message="created",
            provider="libvirt",
        )

    monkeypatch.setattr(
        service.vm_agent_admin, "create_vm", MethodType(create_vm, service.vm_agent_admin)
    )

    result = service.vm_agent_admin.create_admin_agent(
        {
            "backend": "libvirt_vm",
            "vm_name": "lab-vm",
            "session_id": "lab-session",
        }
    )

    assert result["success"] is True
    # The admin create flow must never request upsert behaviour: omitting the kwarg
    # leaves vm/provider.py's allow_existing_session defaulting to False so a duplicate
    # session fails clearly instead of being silently reused.
    assert not captured.get("allow_existing_session", False)


def test_create_container_agent_duplicate_session_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))

    class FakeContainerBackend:
        def __init__(self) -> None:
            self.config = service.runtime.provider_config.resolve_for_directory("root")

        def prepare_session(
            self, *, session_id: str, metadata: dict[str, object] | None = None
        ) -> WorkerSessionRecord:
            return WorkerSessionRecord(
                session_id=session_id,
                root_path=f"/worker/{session_id}",
                agent_home_path=f"/worker/{session_id}/agent_home",
                repo_root_path=f"/worker/{session_id}/agent_home/repos",
                artifacts_path=f"/worker/{session_id}/artifacts",
                logs_path=f"/worker/{session_id}/logs",
                metadata_path=f"/worker/{session_id}/metadata.json",
                metadata=dict(metadata or {}),
            )

        def write_files(self, *, session_id: str, files: list[dict[str, object]]) -> object:
            return object()

    fake_backend = FakeContainerBackend()
    monkeypatch.setattr(
        service.vm_agent_admin,
        "container_worker_backend",
        MethodType(lambda self, **_kwargs: fake_backend, service.vm_agent_admin),
    )

    payload = {
        "backend": "container_worker_vm",
        "session_id": "container-session",
        "target_name": "container-target",
        "ttl_seconds": 42,
    }
    before = datetime.now(UTC)
    first = service.vm_agent_admin.create_admin_agent(payload)
    after = datetime.now(UTC)
    assert first["success"] is True
    manifest = service.runtime.session_repository.get_session("container-session")
    assert manifest is not None
    assert before + timedelta(seconds=42) <= manifest.expires_at
    assert manifest.expires_at <= after + timedelta(seconds=42)

    with pytest.raises(ValueError, match="Session already exists: container-session"):
        service.vm_agent_admin.create_admin_agent(dict(payload))


def test_submit_admin_agent_create_operations_run_concurrently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    started = threading.Event()
    release = threading.Event()
    lock = threading.Lock()
    started_session_ids: list[str] = []

    def create_admin_agent(
        self: AdminRuntimeComponents, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        del self
        session_id = str(payload["session_id"])
        with lock:
            started_session_ids.append(session_id)
            if len(started_session_ids) == 2:
                started.set()
        assert release.wait(2), "create operation release event was not set"
        return {
            "success": True,
            "agent_id": session_id,
            "backend": "container_worker_vm",
            "target_name": str(payload.get("target_name") or session_id),
            "action": "create",
            "message": f"created {session_id}",
            "session_id": session_id,
        }

    monkeypatch.setattr(
        service.vm_agent_admin,
        "create_admin_agent",
        MethodType(create_admin_agent, service.vm_agent_admin),
    )

    first = service.vm_agent_admin.submit_admin_agent_create(
        {
            "backend": "container_worker_vm",
            "session_id": "session-a",
            "target_name": "target-a",
        }
    )
    second = service.vm_agent_admin.submit_admin_agent_create(
        {
            "backend": "container_worker_vm",
            "session_id": "session-b",
            "target_name": "target-b",
        }
    )

    assert first.operation_id != second.operation_id
    assert started.wait(2), "both create operations should start before either finishes"
    release.set()
    first_done = wait_for_agent_create_operation(
        service, first.operation_id, statuses={"succeeded"}
    )
    second_done = wait_for_agent_create_operation(
        service, second.operation_id, statuses={"succeeded"}
    )
    assert sorted(started_session_ids) == ["session-a", "session-b"]
    assert first_done.result is not None
    assert first_done.result["target_name"] == "target-a"
    assert second_done.result is not None
    assert second_done.result["target_name"] == "target-b"


def test_submit_admin_agent_create_operation_records_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))

    def create_admin_agent(
        self: AdminRuntimeComponents, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        del self, payload
        raise ValueError("Session already exists: container-session")

    monkeypatch.setattr(
        service.vm_agent_admin,
        "create_admin_agent",
        MethodType(create_admin_agent, service.vm_agent_admin),
    )

    operation = service.vm_agent_admin.submit_admin_agent_create(
        {
            "backend": "container_worker_vm",
            "session_id": "container-session",
            "target_name": "container-target",
        }
    )

    failed = wait_for_agent_create_operation(service, operation.operation_id, statuses={"failed"})
    assert failed.error == "Session already exists: container-session"
    assert failed.result is None
    assert failed.finished_at is not None


def test_submit_admin_agent_create_operation_does_not_persist_raw_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    captured_payloads: list[dict[str, Any]] = []

    def create_admin_agent(
        self: AdminRuntimeComponents, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        del self
        captured_payloads.append(dict(payload))
        return {
            "success": True,
            "agent_id": str(payload["session_id"]),
            "backend": "container_worker_vm",
            "target_name": str(payload.get("target_name") or payload["session_id"]),
            "action": "create",
            "message": "created",
            "session_id": str(payload["session_id"]),
        }

    monkeypatch.setattr(
        service.vm_agent_admin,
        "create_admin_agent",
        MethodType(create_admin_agent, service.vm_agent_admin),
    )

    operation = service.vm_agent_admin.submit_admin_agent_create(
        {
            "backend": "container_worker_vm",
            "session_id": "container-session",
            "target_name": "container-target",
            "ignored_provider_token": "super-secret-token",
        }
    )

    succeeded = wait_for_agent_create_operation(
        service, operation.operation_id, statuses={"succeeded"}
    )
    assert captured_payloads == [
        {
            "backend": "container_worker_vm",
            "session_id": "container-session",
            "target_name": "container-target",
            "ignored_provider_token": "super-secret-token",
        }
    ]
    assert "request_payload" not in succeeded.model_dump(mode="json")
    store_text = (tmp_path / "runtime" / "agent-create-operations.json").read_text(encoding="utf-8")
    assert "ignored_provider_token" not in store_text
    assert "super-secret-token" not in store_text


def test_admin_service_constructor_does_not_fail_live_agent_create_operations(
    tmp_path: Path,
) -> None:
    config_path = write_config(tmp_path)
    first_service = build_admin_runtime(config_path)
    first_service.agent_create_operations.put(
        "operation-running",
        AgentCreateOperation(
            operation_id="operation-running",
            status="running",
            backend="container_worker_vm",
            target_name="container-target",
            session_id="container-session",
            directory_path="root",
            owner_id="live-admin-web",
            owner_pid=os.getpid(),
        ),
    )

    unrelated_service = build_admin_runtime(config_path)

    operation = unrelated_service.vm_agent_admin.get_admin_agent_create_operation(
        "operation-running"
    )
    assert operation.status == "running"
    assert operation.error is None
    assert operation.finished_at is None


def test_admin_service_marks_abandoned_agent_create_operations_failed_on_recovery(
    tmp_path: Path,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.agent_create_operations.put(
        "operation-running",
        AgentCreateOperation(
            operation_id="operation-running",
            status="running",
            backend="container_worker_vm",
            target_name="container-target",
            session_id="container-session",
            directory_path="root",
            owner_id="dead-admin-web",
            owner_pid=0,
        ),
    )
    service.agent_create_operations.put(
        "operation-unowned",
        AgentCreateOperation(
            operation_id="operation-unowned",
            status="running",
            backend="container_worker_vm",
            target_name="legacy-container-target",
            session_id="legacy-container-session",
            directory_path="root",
        ),
    )

    service.vm_agent_admin.mark_abandoned_agent_create_operations_failed()

    operation = service.vm_agent_admin.get_admin_agent_create_operation("operation-running")
    assert operation.status == "failed"
    assert operation.error == "Admin Web process exited before this create operation finished."
    assert operation.finished_at is not None
    unowned = service.vm_agent_admin.get_admin_agent_create_operation("operation-unowned")
    assert unowned.status == "running"
    assert unowned.error is None


def test_create_container_agent_rejects_mssql(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))

    with pytest.raises(
        ValueError,
        match="create_admin_agent with container_worker_vm does not support MSSQL yet",
    ):
        service.vm_agent_admin.create_admin_agent(
            {
                "backend": "container_worker_vm",
                "session_id": "container-session",
                "mssql": True,
            }
        )


def test_create_container_agent_rejects_unknown_codex_skill_before_prepare(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = build_admin_runtime(write_config(tmp_path))

    def fail_prepare(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("container session should not be prepared")

    monkeypatch.setattr(ContainerWorkerClient, "prepare_session", fail_prepare)

    with pytest.raises(ValueError, match="Unknown Codex skill: missing.skill"):
        service.vm_agent_admin.create_admin_agent(
            {
                "backend": "container_worker_vm",
                "session_id": "container-session",
                "codex_skills": ["missing.skill"],
            }
        )


def test_create_container_agent_does_not_persist_session_when_write_files_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    created = service.directory_admin.create_child(parent="root", name="team")
    assert created.ok is True and created.data is not None
    team_path = created.data.details.directory.path
    write_result = service.operations_admin.write_directory_domain_config(
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
        service.vm_agent_admin.create_admin_agent(
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
                "container_worker": {
                    "image": "evander-runner:latest",
                    "image_alias": None,
                },
                "worker_url": "http://team-worker.test:11888",
            },
        )
    ]
    assert service.runtime.session_repository.get_session("container-session") is None


def test_install_admin_agent_skills_copies_managed_skill_to_container_session(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.skill_admin.create_skill({"name": "demo.skill", "content": "# Demo\n"})
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
        service.vm_agent_admin,
        "container_worker_backend",
        lambda **_kwargs: FakeContainerBackend(),
    )

    result = service.vm_agent_admin.run_admin_agent_action(
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
                        "/home/agent/.config/evander/codex/skills/demo.skill/SKILL.md"
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
    service = build_admin_runtime(write_config(tmp_path))
    service.skill_admin.create_skill({"name": "demo.skill", "content": "# Demo\n"})
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
        self: AdminRuntimeComponents, target: str, **kwargs: object
    ) -> dict[str, object]:
        assert target == "lab-session"
        assert kwargs["command"] == 'printf "%s" "${CODEX_HOME:-$HOME/.config/evander/codex}"'
        return {"success": True, "stdout": "/custom/codex\n"}

    def fake_vm_copy_files(
        self: AdminRuntimeComponents,
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

    monkeypatch.setattr(
        service.vm_agent_admin, "vm_exec", MethodType(fake_vm_exec, service.vm_agent_admin)
    )
    monkeypatch.setattr(
        service.vm_agent_admin,
        "vm_copy_files",
        MethodType(fake_vm_copy_files, service.vm_agent_admin),
    )

    result = service.vm_agent_admin.run_admin_agent_action(
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
    service = build_admin_runtime(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="receptionist-session",
            vm_id="evander-receptionist-vm",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    monkeypatch.setattr(
        service.vm_agent_admin,
        "list_vms",
        MethodType(
            lambda self, **kwargs: [
                VmSummary(
                    vm_name="evander-receptionist-vm",
                    power_state="running",
                    agent_state="running",
                    session_ids=["receptionist-session"],
                )
            ],
            service.vm_agent_admin,
        ),
    )

    result = service.vm_agent_admin.run_admin_agent_action(
        "evander-receptionist-vm",
        "prompt-agent",
        backend="libvirt_vm",
        payload={
            "prompt": "Summarize the latest changes",
            "resume_thread_id": "thread-keep-1",
        },
    )

    queue_key = "admin-agent-prompt:libvirt_vm:receptionist-session"
    queued = AgentPromptQueueStore(tmp_path / "runtime" / "queued-agent-prompts.json").get(
        queue_key
    )

    assert result["success"] is True
    assert result["queued"] is True
    assert result["backend"] == "libvirt_vm"
    assert result["agent_id"] == "evander-receptionist-vm"
    assert result["target_name"] == "evander-receptionist-vm"
    assert result["session_id"] == "receptionist-session"
    assert result["queue_key"] == queue_key
    assert result["status"] == "queued"
    assert queued is not None
    assert queued.queue_key == queue_key
    assert queued.effect_id == queue_key
    assert queued.target == "receptionist-session"
    assert queued.vm_name == "evander-receptionist-vm"
    assert queued.session_id == "receptionist-session"
    assert queued.prompt == "Summarize the latest changes"
    assert queued.resume_thread_id == "thread-keep-1"


def test_run_admin_agent_action_prompt_agent_dispatches_via_enqueue_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="receptionist-session",
            vm_id="evander-receptionist-vm",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    monkeypatch.setattr(
        service.vm_agent_admin,
        "list_vms",
        MethodType(
            lambda self, **kwargs: [
                VmSummary(
                    vm_name="evander-receptionist-vm",
                    power_state="shut off",
                    agent_state="stopped",
                    session_ids=["receptionist-session"],
                )
            ],
            service.vm_agent_admin,
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
        self: AdminRuntimeComponents,
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
            vm_name="evander-receptionist-vm",
            action="resume_issue_agent",
            message="ok",
            thread_id="thread-new-1",
        )

    monkeypatch.setattr(admin_service.EffectProviderFactory, "build", recording_build)
    monkeypatch.setattr(
        service.vm_agent_admin,
        "resume_agent",
        MethodType(fake_resume_agent, service.vm_agent_admin),
    )

    result = service.vm_agent_admin.run_admin_agent_action(
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
    assert build_calls[0]["effect"]["vm_name"] == "evander-receptionist-vm"
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
    assert result["agent_id"] == "evander-receptionist-vm"
    assert result["target_name"] == "evander-receptionist-vm"
    assert result["session_id"] == "receptionist-session"
    assert result["thread_id"] == "thread-new-1"
    assert result["status"] == "dispatched"


def test_run_admin_agent_action_prompt_agent_queues_when_prompt_lock_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from evander.messaging.agent_prompt_lock import AgentPromptLockStore, agent_prompt_lock_key

    service = build_admin_runtime(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="receptionist-session",
            vm_id="evander-receptionist-vm",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    monkeypatch.setattr(
        service.vm_agent_admin,
        "list_vms",
        MethodType(
            lambda self, **kwargs: [
                VmSummary(
                    vm_name="evander-receptionist-vm",
                    power_state="shut off",
                    agent_state="stopped",
                    session_ids=["receptionist-session"],
                )
            ],
            service.vm_agent_admin,
        ),
    )
    lock_store = AgentPromptLockStore(service.config.runtime_dir / "agent-prompt-locks.json")
    lock_store.claim(
        lock_key=agent_prompt_lock_key(
            target="receptionist-session",
            vm_name="evander-receptionist-vm",
            session_id="receptionist-session",
            execution_backend="libvirt_vm",
        ),
        owner_id="workflow-owner",
        target="receptionist-session",
        vm_name="evander-receptionist-vm",
        session_id="receptionist-session",
        dispatch_id="workflow-dispatch",
        effect_id="workflow-effect",
    )

    resume_calls: list[dict[str, object]] = []

    def fake_resume_agent(
        self: AdminRuntimeComponents,
        target: str,
        prompt: str,
        **kwargs: object,
    ) -> VmActionResult:
        del self, target, prompt, kwargs
        resume_calls.append({})
        return VmActionResult(
            success=True,
            vm_name="evander-receptionist-vm",
            action="resume_issue_agent",
            message="ok",
            thread_id="thread-new-1",
        )

    monkeypatch.setattr(
        service.vm_agent_admin,
        "resume_agent",
        MethodType(fake_resume_agent, service.vm_agent_admin),
    )

    result = service.vm_agent_admin.run_admin_agent_action(
        "receptionist-session",
        "prompt-agent",
        backend="libvirt_vm",
        payload={"prompt": "Continue the investigation"},
    )

    queue_key = "admin-agent-prompt:libvirt_vm:receptionist-session"
    queued = AgentPromptQueueStore(service.config.runtime_dir / "queued-agent-prompts.json").get(
        queue_key
    )

    assert result["success"] is True
    assert result["queued"] is True
    assert result["status"] == "queued"
    assert result["queue_key"] == queue_key
    assert resume_calls == []
    assert queued is not None
    assert queued.target == "receptionist-session"
    assert queued.vm_name == "evander-receptionist-vm"
    assert queued.session_id == "receptionist-session"
    assert queued.prompt == "Continue the investigation"


def test_run_admin_agent_action_prompt_agent_records_libvirt_waiting_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="receptionist-session",
            vm_id="evander-receptionist-vm",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    monkeypatch.setattr(
        service.vm_agent_admin,
        "list_vms",
        MethodType(
            lambda self, **kwargs: [
                VmSummary(
                    vm_name="evander-receptionist-vm",
                    power_state="shut off",
                    agent_state="stopped",
                    session_ids=["receptionist-session"],
                )
            ],
            service.vm_agent_admin,
        ),
    )
    monkeypatch.setattr(
        service.vm_agent_admin,
        "resume_agent",
        MethodType(
            lambda self, *args, **kwargs: VmActionResult(
                success=True,
                vm_name="evander-receptionist-vm",
                action="resume_issue_agent",
                message="ok",
                thread_id="thread-new-1",
            ),
            service.vm_agent_admin,
        ),
    )

    result = service.vm_agent_admin.run_admin_agent_action(
        "receptionist-session",
        "prompt-agent",
        backend="libvirt_vm",
        payload={"prompt": "Continue the investigation"},
    )

    stores = service.workflow_config_admin._workflow_run_store_paths()
    if service.workflow_config_admin._workflow_uses_sqlite_store(stores):
        execution_store = SqliteExecutionStore(Path(stores["orchestration_store_path"]))
    else:
        execution_store = JsonExecutionStore(Path(stores["effect_execution_store_path"]))
    records = list(execution_store.read_all().values())

    assert result["queued"] is False
    assert result["prompt_lock_key"] == "vm:evander-receptionist-vm"
    assert len(records) == 1
    assert records[0].status == "waiting"
    assert records[0].effect_type == "effect:agent:enqueue-prompt"
    assert records[0].resolved_config["execution_backend"] == "libvirt_vm"
    assert records[0].outputs["prompt_lock_key"] == "vm:evander-receptionist-vm"
    assert records[0].outputs["prompt_lock_owner_id"] == result["prompt_lock_owner_id"]


def test_run_admin_agent_action_prompt_agent_returns_queued_when_dispatch_fails_after_queueing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="receptionist-session",
            vm_id="evander-receptionist-vm",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    monkeypatch.setattr(
        service.vm_agent_admin,
        "list_vms",
        MethodType(
            lambda self, **kwargs: [
                VmSummary(
                    vm_name="evander-receptionist-vm",
                    power_state="shut off",
                    agent_state="stopped",
                    session_ids=["receptionist-session"],
                )
            ],
            service.vm_agent_admin,
        ),
    )

    def failing_resume_agent(
        self: AdminRuntimeComponents,
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
            vm_name="evander-receptionist-vm",
            action="resume_issue_agent",
            message="resume failed",
        )

    monkeypatch.setattr(
        service.vm_agent_admin,
        "resume_agent",
        MethodType(failing_resume_agent, service.vm_agent_admin),
    )

    result = service.vm_agent_admin.run_admin_agent_action(
        "receptionist-session",
        "prompt-agent",
        backend="libvirt_vm",
        payload={"prompt": "Continue the investigation"},
    )

    queue_key = "admin-agent-prompt:libvirt_vm:receptionist-session"
    queued = AgentPromptQueueStore(tmp_path / "runtime" / "queued-agent-prompts.json").get(
        queue_key
    )

    assert result["success"] is True
    assert result["queued"] is True
    assert result["status"] == "queued"
    assert result["queue_key"] == queue_key
    assert result["target_name"] == "evander-receptionist-vm"
    assert result["session_id"] == "receptionist-session"
    assert "queued prompt for retry" in result["dispatch_error"]
    assert queued is not None
    assert queued.prompt == "Continue the investigation"


def test_run_admin_agent_action_prompt_agent_records_direct_container_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from evander.effects.providers.agent_enqueue_prompt import (
        AgentEnqueuePromptEffectProvider,
    )
    from evander.execution import ExecutionStatusKind

    service = build_admin_runtime(write_config(tmp_path))
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
                "container_worker": {"image": "runner:admin-image"},
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
        "evander.effects.providers.agent_enqueue_prompt.container_backend",
        lambda context, config: backend,
    )

    result = service.vm_agent_admin.run_admin_agent_action(
        "container-session",
        "prompt-agent",
        backend="container_worker_vm",
        payload={"prompt": "Continue the container workflow"},
    )

    stores = service.workflow_config_admin._workflow_run_store_paths()
    if service.workflow_config_admin._workflow_uses_sqlite_store(stores):
        execution_store = SqliteExecutionStore(Path(stores["orchestration_store_path"]))
    else:
        execution_store = JsonExecutionStore(Path(stores["effect_execution_store_path"]))
    records = list(execution_store.read_all().values())

    assert result["success"] is True
    assert result["queued"] is False
    assert result["status"] == "dispatched"
    assert result["execution_id"] == backend.started_specs[0].target_name
    assert backend.started_specs[0].image == "runner:admin-image"
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
        "evander.effects.providers.agent_prompt.build_execution_backend",
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


def test_resume_agent_routes_container_session_through_container_prompt_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
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
                "container_worker": {"image": "runner:team-image"},
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
        "evander.effects.providers.agent_enqueue_prompt.container_backend",
        lambda context, config: backend,
    )

    result = service.vm_agent_admin.resume_agent(
        "container-session",
        "Resume Team work",
        dispatch_id="team-dispatch-1",
        resume_mode="same_context",
    )

    stores = service.workflow_config_admin._workflow_run_store_paths()
    if service.workflow_config_admin._workflow_uses_sqlite_store(stores):
        execution_store = SqliteExecutionStore(Path(stores["orchestration_store_path"]))
    else:
        execution_store = JsonExecutionStore(Path(stores["effect_execution_store_path"]))
    records = list(execution_store.read_all().values())

    assert result.success is True
    assert result.provider == "container_worker_vm"
    assert result.thread_id == "team-dispatch-1"
    assert backend.started_specs[0].session_id == "container-session"
    assert backend.started_specs[0].image == "runner:team-image"
    assert "codex exec resume --last" in str(backend.started_specs[0].command)
    assert len(records) == 1
    assert records[0].status == "waiting"
    assert records[0].effect_type == "effect:agent:enqueue-prompt"
    assert records[0].outputs["dispatch_id"] == "team-dispatch-1"
    assert records[0].outputs["execution_backend"] == "container_worker_vm"


def test_submit_operator_steering_interrupts_container_waiting_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from evander.effects.providers.agent_prompt import (
        CONTAINER_INTERRUPT_IN_PROGRESS_OUTPUT_KEY,
    )
    from evander.operator_steering import operator_steering_note_store

    service = build_admin_runtime(write_config(tmp_path))
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
                "container_worker": {"image": "runner:steer-image"},
            },
        )
    )
    legacy_note = operator_steering_note_store(service.config.runtime_dir).create_note(
        session_id="container-session",
        dispatch_id="dispatch-steer",
        worker_execution_id="agent-old-1",
        message="Old relay-delivered note.",
    )
    register_model_usage_correlation_context(
        runtime_dir=service.config.runtime_dir,
        session_id="container-session",
        dispatch_id="dispatch-steer",
        workflow_run_id="workflow-steer",
        effect_id="effect-steer",
        directory_path="root",
        model="gpt-5.2-codex",
        execution_backend="container_worker_vm",
    )

    execution_store = service.operator_steering_admin._operator_steering_execution_store()
    now = datetime.now(UTC)
    execution_store.record_execution(
        EffectExecutionRecord(
            execution_id="workflow-exec-steer",
            idempotency_key="event-steer::effect-steer",
            event_id="event-steer",
            trace_id="workflow-steer",
            source_node_id="effect-steer",
            source_trigger_type="manual",
            effect_id="effect-steer",
            effect_type="effect:agent:prompt",
            status="waiting",
            started_at=now,
            finished_at=now,
            resolved_config_hash="hash-steer",
            resolved_config={
                "target": "container-session",
                "session_id": "container-session",
                "prompt": "Original workflow prompt",
                "model": "gpt-5.2-codex",
                "directory_path": "root",
                "execution_backend": "container_worker_vm",
            },
            input_payload={"prompt": "Original workflow prompt"},
            outputs={
                "dispatch_id": "dispatch-steer",
                "thread_id": "dispatch-steer",
                "success": True,
                "queued": False,
                "output_mode": "on_completion",
                "execution_backend": "container_worker_vm",
                "execution_id": "agent-old-1",
                "session_id": "container-session",
            },
        )
    )

    class InterruptBackend:
        def __init__(self) -> None:
            self.config = service.runtime.provider_config.resolve_for_directory("root")
            self.stopped: list[str] = []
            self.started_specs: list[object] = []
            self.annotated_records: list[ContainerWorkerExecutionRecord] = []
            old_record = ContainerWorkerExecutionRecord(
                execution_id="agent-old-1",
                session_id="container-session",
                worker_job_id="worker-job-old",
                worker_url="http://worker.invalid",
                target_name="agent-old-1",
                directory_path="root",
                last_status="running",
            )
            self.repository = SimpleNamespace(
                get=lambda execution_id: old_record if execution_id == "agent-old-1" else None,
                put=self.annotated_records.append,
            )

        def get_log_snapshot(self, execution_id: str, *, lines: int = 120) -> str:
            assert execution_id == "agent-old-1"
            assert lines == 120
            return "working\nsession id: thread-steer-real\n"

        def stop(self, execution_id: str):
            self.stopped.append(execution_id)
            return SimpleNamespace(execution_id=execution_id)

        def prepare(self, spec):
            return spec

        def start(self, spec):
            self.started_specs.append(spec)
            return SimpleNamespace(execution_id=spec.target_name)

    backend = InterruptBackend()
    monkeypatch.setattr(
        service.vm_agent_admin,
        "container_worker_backend",
        MethodType(lambda self, **kwargs: backend, service.vm_agent_admin),
    )

    result = service.model_usage_admin.submit_operator_steering_note(
        session_id="container-session",
        dispatch_id="dispatch-steer",
        worker_execution_id="agent-old-1",
        message="Switch to implementation now.",
        created_by="operator@example.com",
    )

    refreshed = execution_store.get_execution("workflow-exec-steer")

    assert result["mode"] == "interrupt_and_resume"
    assert result["note"].status == "delivered"
    assert result["note"].provider == "container_worker_vm"
    stored_notes = operator_steering_note_store(service.config.runtime_dir).list_notes(
        session_id="container-session",
        dispatch_id="dispatch-steer",
    )
    stored_note_ids = [note.note_id for note in stored_notes]
    assert result["note"].note_id in stored_note_ids
    stored_delivered = operator_steering_note_store(service.config.runtime_dir).get_note(
        result["note"].note_id
    )
    assert stored_delivered is not None
    assert stored_delivered.status == "delivered"
    assert stored_delivered.worker_execution_id == result["interrupt"]["replacement_execution_id"]
    assert result["interrupt"]["interrupted_execution_id"] == "agent-old-1"
    assert result["interrupt"]["replacement_execution_id"] == backend.started_specs[0].target_name
    assert backend.stopped == ["agent-old-1"]
    assert len(backend.started_specs) == 1
    started_spec = backend.started_specs[0]
    assert started_spec.session_id == "container-session"
    assert started_spec.directory_path == "root"
    assert started_spec.image == "runner:steer-image"
    assert started_spec.metadata["dispatch_id"] == "dispatch-steer"
    assert started_spec.metadata["operator_steering"] == "interrupt_and_resume"
    assert started_spec.metadata["interrupted_execution_id"] == "agent-old-1"
    assert "Switch to implementation now." in str(started_spec.command)
    assert "thread-steer-real" in str(started_spec.command)
    assert refreshed is not None
    assert refreshed.status == "waiting"
    assert CONTAINER_INTERRUPT_IN_PROGRESS_OUTPUT_KEY not in refreshed.outputs
    assert refreshed.outputs["execution_id"] == started_spec.target_name
    assert refreshed.outputs["worker_execution_id"] == started_spec.target_name
    assert refreshed.outputs["thread_id"] == "thread-steer-real"
    assert refreshed.outputs["interrupted_execution_id"] == "agent-old-1"
    assert refreshed.outputs["interrupted_execution_ids"] == ["agent-old-1"]
    assert refreshed.outputs["operator_interrupt_count"] == 1
    assert refreshed.outputs["cancelled_legacy_operator_steering_note_ids"] == [legacy_note.note_id]
    cancelled_legacy = operator_steering_note_store(service.config.runtime_dir).get_note(
        legacy_note.note_id
    )
    assert cancelled_legacy is not None
    assert cancelled_legacy.status == "cancelled"
    assert backend.annotated_records
    assert backend.annotated_records[0].metadata["interrupted_by_operator"] is True
    assert (
        backend.annotated_records[0].metadata["interrupt_replaced_by_execution_id"]
        == started_spec.target_name
    )


def test_submit_operator_steering_failure_after_stop_fails_waiting_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from evander.effects.providers.agent_prompt import (
        CONTAINER_INTERRUPT_IN_PROGRESS_OUTPUT_KEY,
    )

    service = build_admin_runtime(write_config(tmp_path))
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
                "container_worker": {"image": "runner:steer-image"},
            },
        )
    )
    register_model_usage_correlation_context(
        runtime_dir=service.config.runtime_dir,
        session_id="container-session",
        dispatch_id="dispatch-steer",
        workflow_run_id="workflow-steer",
        effect_id="effect-steer",
        directory_path="root",
        model="gpt-5.2-codex",
        execution_backend="container_worker_vm",
    )
    execution_store = service.operator_steering_admin._operator_steering_execution_store()
    now = datetime.now(UTC)
    execution_store.record_execution(
        EffectExecutionRecord(
            execution_id="workflow-exec-steer",
            idempotency_key="event-steer::effect-steer",
            event_id="event-steer",
            trace_id="workflow-steer",
            source_node_id="effect-steer",
            source_trigger_type="manual",
            effect_id="effect-steer",
            effect_type="effect:agent:prompt",
            status="waiting",
            started_at=now,
            finished_at=now,
            resolved_config_hash="hash-steer",
            resolved_config={
                "target": "container-session",
                "session_id": "container-session",
                "prompt": "Original workflow prompt",
                "model": "gpt-5.2-codex",
                "directory_path": "root",
                "execution_backend": "container_worker_vm",
            },
            input_payload={"prompt": "Original workflow prompt"},
            outputs={
                "dispatch_id": "dispatch-steer",
                "thread_id": "thread-existing",
                "success": True,
                "queued": False,
                "output_mode": "on_completion",
                "execution_backend": "container_worker_vm",
                "execution_id": "agent-old-1",
                "session_id": "container-session",
            },
        )
    )

    class FailingReplacementBackend:
        def __init__(self) -> None:
            self.config = service.runtime.provider_config.resolve_for_directory("root")
            self.repository = SimpleNamespace(
                get=lambda execution_id: None, put=lambda record: None
            )
            self.stopped: list[str] = []

        def get_log_snapshot(self, execution_id: str, *, lines: int = 120) -> str:
            return ""

        def stop(self, execution_id: str):
            self.stopped.append(execution_id)
            return SimpleNamespace(execution_id=execution_id)

        def prepare(self, spec):
            return spec

        def start(self, spec):
            del spec
            raise RuntimeError("replacement unavailable")

    backend = FailingReplacementBackend()
    monkeypatch.setattr(
        service.vm_agent_admin,
        "container_worker_backend",
        MethodType(lambda self, **kwargs: backend, service.vm_agent_admin),
    )

    with pytest.raises(RuntimeError, match="could not start the replacement"):
        service.model_usage_admin.submit_operator_steering_note(
            session_id="container-session",
            dispatch_id="dispatch-steer",
            worker_execution_id="agent-old-1",
            message="Switch to implementation now.",
        )

    refreshed = execution_store.get_execution("workflow-exec-steer")

    assert backend.stopped == ["agent-old-1"]
    assert refreshed is not None
    assert refreshed.status == "failed"
    assert CONTAINER_INTERRUPT_IN_PROGRESS_OUTPUT_KEY not in refreshed.outputs
    assert refreshed.outputs["success"] is False
    assert refreshed.outputs["operator_steering_failed"] is True
    assert refreshed.outputs["execution_id"] == "agent-old-1"
    assert "replacement unavailable" in refreshed.error


def test_container_waiting_reconciliation_skips_operator_interrupt_in_progress() -> None:
    from evander.effects.providers.agent_prompt import (
        CONTAINER_INTERRUPT_IN_PROGRESS_OUTPUT_KEY,
        AgentPromptEffectProvider,
    )

    record = SimpleNamespace(
        outputs={
            "execution_backend": "container_worker_vm",
            "execution_id": "agent-old-1",
            "session_id": "container-session",
            CONTAINER_INTERRUPT_IN_PROGRESS_OUTPUT_KEY: True,
        }
    )
    worker = SimpleNamespace()

    assert (
        AgentPromptEffectProvider._reconcile_container_waiting_record(
            worker=worker,
            record=record,
        )
        == 0
    )


def test_run_admin_agent_action_prompt_agent_returns_queued_when_container_dispatch_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
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
        "evander.effects.providers.agent_enqueue_prompt.container_backend",
        lambda context, config: FailingContainerBackend(),
    )

    result = service.vm_agent_admin.run_admin_agent_action(
        "container-session",
        "prompt-agent",
        backend="container_worker_vm",
        payload={"prompt": "Continue the container workflow"},
    )

    queue_key = "admin-agent-prompt:container_worker_vm:container-session"
    queued = AgentPromptQueueStore(tmp_path / "runtime" / "queued-agent-prompts.json").get(
        queue_key
    )

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
    service = build_admin_runtime(write_config(tmp_path))
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
        "evander.effects.providers.agent_enqueue_prompt.container_backend",
        lambda context, config: BusyContainerBackend(),
    )

    result = service.vm_agent_admin.run_admin_agent_action(
        "container-session",
        "prompt-agent",
        backend="container_worker_vm",
        payload={
            "prompt": "Re-run the container workflow",
            "resume_thread_id": "thread-container-1",
        },
    )

    queue_key = "admin-agent-prompt:container_worker_vm:container-session"
    queued = AgentPromptQueueStore(tmp_path / "runtime" / "queued-agent-prompts.json").get(
        queue_key
    )

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
    service = build_admin_runtime(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="receptionist-session",
            vm_id="evander-receptionist-vm",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )

    with pytest.raises(ValueError, match="prompt-agent requires a non-empty prompt"):
        service.vm_agent_admin.run_admin_agent_action(
            "receptionist-session",
            "prompt-agent",
            backend="libvirt_vm",
            payload={"prompt": "   "},
        )


def test_run_admin_agent_action_prompt_agent_supports_vm_without_linked_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    monkeypatch.setattr(
        service.vm_agent_admin,
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
            service.vm_agent_admin,
        ),
    )

    result = service.vm_agent_admin.run_admin_agent_action(
        "standalone-vm",
        "prompt-agent",
        backend="libvirt_vm",
        payload={"prompt": "Check whether you are idle"},
    )

    queue_key = "admin-agent-prompt:libvirt_vm:standalone-vm"
    queued = AgentPromptQueueStore(tmp_path / "runtime" / "queued-agent-prompts.json").get(
        queue_key
    )

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
    service = build_admin_runtime(write_config(tmp_path))

    with pytest.raises(ValueError, match="Unknown container worker agent target: missing-session"):
        service.vm_agent_admin.run_admin_agent_action(
            "missing-session",
            "prompt-agent",
            backend="container_worker_vm",
            payload={"prompt": "hello"},
        )


def test_run_admin_agent_action_prompt_agent_rejects_ambiguous_container_session(
    tmp_path: Path,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
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
        service.vm_agent_admin.run_admin_agent_action(
            "shared-target",
            "prompt-agent",
            backend="container_worker_vm",
            payload={"prompt": "hello"},
        )


def test_teams_auth_list_channels_resolves_exact_channel_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    created = service.directory_admin.create_child(parent="root", name="example-industry")
    assert created.ok is True and created.data is not None
    directory_path = created.data.details.directory.path
    write_result = service.operations_admin.write_directory_domain_config(
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

    requested_team_ids: list[str] = []

    class FakeGraphClient:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

        def list_channels(self, *, team_id: str) -> list[dict[str, object]]:
            requested_team_ids.append(team_id)
            return [
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

    monkeypatch.setattr(admin_teams_service, "TeamsGraphClient", FakeGraphClient)
    monkeypatch.setattr(
        admin_teams_service.TeamsDeviceCodeTokenProvider,
        "get_bearer_token",
        lambda self, force_refresh=False: "access-token",
    )

    result = service.operations_admin.teams_auth_list_channels(
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
    assert requested_team_ids == ["c85e4de5-e0d7-4240-8302-8bc944f2360d"]


def test_teams_auth_list_channels_can_list_all_channels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    created = service.directory_admin.create_child(parent="root", name="example-industry")
    assert created.ok is True and created.data is not None
    directory_path = created.data.details.directory.path
    write_result = service.operations_admin.write_directory_domain_config(
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

    class FakeGraphClient:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

        def list_channels(self, *, team_id: str) -> list[dict[str, object]]:
            assert team_id == "team-123"
            return [
                {"id": "19:a@thread.skype", "displayName": "General"},
                {"id": "19:b@thread.skype", "displayName": "Truman"},
            ]

    monkeypatch.setattr(admin_teams_service, "TeamsGraphClient", FakeGraphClient)
    monkeypatch.setattr(
        admin_teams_service.TeamsDeviceCodeTokenProvider,
        "get_bearer_token",
        lambda self, force_refresh=False: "access-token",
    )

    result = service.operations_admin.teams_auth_list_channels(
        directory=directory_path,
        team_id="team-123",
        include_all=True,
    )

    assert result["channel_name"] is None
    assert result["match_count"] == 2
    assert [item["display_name"] for item in result["channels"]] == ["General", "Truman"]


def test_create_local_issue_reuses_scoped_id_within_directory(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    created = service.directory_admin.create_child(parent="root", name="team")
    assert created.ok is True and created.data is not None
    team_path = created.data.details.directory.path

    source_path = tmp_path / "team-issue.yaml"
    source_path.write_text(
        "issue_number: 7\ntitle: Team issue\nbody: First body\n", encoding="utf-8"
    )
    first = service.issue_git_admin.create_local_issue(
        yaml_path=str(source_path), directory_path=team_path
    )

    source_path.write_text(
        "issue_number: 7\ntitle: Team issue\nbody: Updated body\n", encoding="utf-8"
    )
    second = service.issue_git_admin.create_local_issue(
        yaml_path=str(source_path), directory_path=team_path
    )

    assert first.issue_id == "root/team::local::7"
    assert second.issue_id == first.issue_id
    assert second.body == "Updated body"

    records = service.local_issue_store.read_all()
    assert list(records) == ["root/team::local::7"]


def test_create_local_issue_serializes_concurrent_issue_number_allocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = write_config(tmp_path)

    def slow_next_issue_number(self: Any) -> str:
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

    monkeypatch.setattr(
        "evander.admin.services.issue_git_service.IssueGitAdminSupport.next_local_issue_number",
        slow_next_issue_number,
    )
    services = [build_admin_runtime(config_path) for _ in range(6)]

    def create_issue(args: tuple[AdminRuntimeComponents, int]) -> str:
        service, index = args
        issue = service.issue_git_admin.create_local_issue(title=f"Issue {index}", body="parallel")
        return issue.issue_number

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        issue_numbers = list(pool.map(create_issue, zip(services, range(6), strict=True)))

    assert sorted(issue_numbers) == ["1", "2", "3", "4", "5", "6"]
    payload = json.loads((tmp_path / "runtime" / "local-issues.json").read_text(encoding="utf-8"))
    assert sorted(payload) == [f"root::local::{number}" for number in range(1, 7)]
    for number in range(1, 7):
        assert (tmp_path / "runtime" / "issues" / f"{number}.yaml").exists()


def test_create_local_issue_imports_block_scalar_body(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    source_path = tmp_path / "block-issue.yaml"
    source_path.write_text(
        "issue_number: 7\ntitle: Block body\nbody: |\n  line one\n  line two\n",
        encoding="utf-8",
    )

    issue = service.issue_git_admin.create_local_issue(yaml_path=str(source_path))

    assert issue.body == "line one\nline two"


def test_create_local_issue_imports_block_scalar_title(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    source_path = tmp_path / "block-title-issue.yaml"
    source_path.write_text(
        "issue_number: 8\ntitle: |\n  line one\n  line two\nbody: ok\n",
        encoding="utf-8",
    )

    issue = service.issue_git_admin.create_local_issue(yaml_path=str(source_path))

    assert issue.title == "line one\nline two"


def test_update_local_issue_updates_store_and_yaml(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    old_repo = _create_issue_test_repo(tmp_path / "old-repo", content="old\n")
    new_repo = _create_issue_test_repo(tmp_path / "new-repo", content="new\n")
    issue = service.issue_git_admin.create_local_issue(
        title="Old title", body="Old body", repo_source=str(old_repo)
    )
    old_managed_repo_id = issue.managed_repo_id

    updated = service.issue_git_admin.update_local_issue(
        issue.issue_number,
        title="New title",
        body="New body",
        state="In Progress",
        repo_source=str(new_repo),
    )

    assert updated.title == "New title"
    assert updated.body == "New body"
    assert updated.state == "In Progress"
    assert updated.managed_repo_id is not None
    assert updated.managed_repo_id != old_managed_repo_id
    assert service.managed_git_admin.get_repository(old_managed_repo_id or "")["bindings"] == []
    assert (
        service.managed_git_admin.get_repository(updated.managed_repo_id)["bindings"][0][
            "binding_id"
        ]
        == issue.issue_id
    )
    yaml_text = Path(updated.yaml_path).read_text(encoding="utf-8")
    assert "title: New title" in yaml_text
    assert "body: New body" in yaml_text
    assert "state: In Progress" in yaml_text


def test_update_local_issue_rejects_proxy_issue(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    issue = service.issue_git_admin.create_local_issue(
        yaml_path=str(_write_proxy_issue_yaml(tmp_path))
    )

    with pytest.raises(ValueError, match="Only local manual issues"):
        service.issue_git_admin.update_local_issue(
            issue.issue_number,
            title="Ignore",
            body="Ignore",
            state="New",
            repo_source=None,
        )


def test_list_vms_collects_session_and_agent_state(tmp_path: Path, monkeypatch) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="issue-123-session",
            vm_id="vm-123",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.virsh_lines", lambda args: ["vm-123", "vm-999"]
    )
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.dominfo_field",
        lambda vm_name, field_name: "running" if vm_name == "vm-123" else "shut off",
    )
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.resolve_vm_ip",
        lambda vm_name: "192.168.122.10" if vm_name == "vm-123" else None,
    )
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.probe_agent_state",
        lambda vm_name, vm_ip, vm_user: ("running", ["repo-one"], datetime.now(UTC), None, 4321),
    )

    summaries = service.vm_agent_admin.list_vms(include_inactive=True)

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
    service = build_admin_runtime(write_config(tmp_path))
    service.agent_control_store.set_status(
        vm_name="vm-999", status="disabled", phase="operator-disable"
    )

    monkeypatch.setattr("evander.admin.services.vm_helpers.virsh_lines", lambda args: ["vm-999"])
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.dominfo_field",
        lambda vm_name, field_name: "shut off",
    )

    summaries = service.vm_agent_admin.list_vms(include_inactive=True)

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

    monkeypatch.setattr(
        "evander.admin.services.process_helpers.run_subprocess",
        fake_run_subprocess,
    )

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

    monkeypatch.setattr(
        "evander.admin.services.process_helpers.run_subprocess",
        fake_run_subprocess,
    )

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
    service = build_admin_runtime(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="evander-receptionist-vm-20260330T112127Z-1353036",
            vm_id="evander-receptionist-vm",
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

    monkeypatch.setattr(
        "evander.admin.services.process_helpers.run_subprocess", fake_run_subprocess
    )
    monkeypatch.setattr("evander.admin.services.vm_agent.vm_resume.subprocess.Popen", _PopenStub)

    result = service.vm_agent_admin.resume_agent("evander-receptionist-vm", "Resume now")

    assert result.success is True
    assert result.thread_id == "thread-abc-123"
    assert captured["cmd"] == [
        str(Path(__file__).resolve().parents[1] / "scripts" / "resume_issue_agent.sh"),
        "--prompt",
        "Resume now",
        "--vm-name",
        "evander-receptionist-vm",
        "--vm-user",
        "agent",
        "--host-control-state-path",
        str(service.config.host_agent_control_state_path),
        "--resume-mode",
        "new_context",
    ]
    assert captured["env"]["AGENT_MODEL"] == "gpt-5.4"
    assert captured["env"]["AGENT_WEB_SEARCH_MODE"] == "disabled"
    assert captured["timeout"] == 120.0
    assert watcher == {}


def test_resume_agent_delegates_to_vm_admin_without_session_repository() -> None:
    service = object.__new__(AdminRuntimeComponents)
    service.runtime = SimpleNamespace()
    calls: list[dict[str, object]] = []

    class _VmAgentAdminStub:
        def resume_agent(
            self,
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
            calls.append(
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
                vm_name=target,
                action="resume_issue_agent",
                message="ok",
                thread_id="thread-1",
            )

    service.vm_agent_admin = _VmAgentAdminStub()

    result = service.vm_agent_admin.resume_agent(
        "vm-libvirt",
        "Resume now",
        model="gpt-5.4",
        dispatch_id="dispatch-vm-1",
        resume_thread_id="thread-keep",
        mcp_gateway_url="http://gateway.test/mcp",
    )

    assert result.success is True
    assert calls == [
        {
            "target": "vm-libvirt",
            "prompt": "Resume now",
            "model": "gpt-5.4",
            "vm_user": "agent",
            "dispatch_id": "dispatch-vm-1",
            "resume_thread_id": "thread-keep",
            "resume_mode": "new_context",
            "persist_thread_id": True,
            "mcp_gateway_url": "http://gateway.test/mcp",
        }
    ]


def test_resume_agent_passes_session_agent_provider(tmp_path: Path, monkeypatch) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="claude-session",
            vm_id="evander-claude-vm",
            model="claude-sonnet-4-6",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={"agent_provider": "claude"},
        ),
    )

    captured: dict[str, object] = {}

    def fake_run_subprocess(cmd: list[str], **kwargs: object):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout="thread_id=claude-thread-1\nok\n",
            stderr="",
        )

    monkeypatch.setattr(
        "evander.admin.services.process_helpers.run_subprocess", fake_run_subprocess
    )

    result = service.vm_agent_admin.resume_agent("evander-claude-vm", "Resume now")

    assert result.success is True
    assert captured["env"]["AGENT_MODEL"] == "claude-sonnet-4-6"
    assert captured["env"]["AGENT_PROVIDER"] == "claude"
    assert "--resume-mode" in captured["cmd"]


def test_resume_agent_passes_explicit_resume_thread_id(tmp_path: Path, monkeypatch) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="receptionist-session",
            vm_id="evander-receptionist-vm",
            expires_at=_active_expires_at(),
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

    monkeypatch.setattr(
        "evander.admin.services.process_helpers.run_subprocess", fake_run_subprocess
    )
    monkeypatch.setattr("evander.admin.services.vm_agent.vm_resume.subprocess.Popen", _PopenStub)

    result = service.vm_agent_admin.resume_agent(
        "evander-receptionist-vm",
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
        "evander.daemons.agent_completion_watcher",
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
    assert watcher["cmd"][store_index + 1] == str(service.config.runtime_dir / "orchestration.db")
    assert "--session-id" in watcher["cmd"]
    assert "receptionist-session" in watcher["cmd"]


def test_resume_agent_watcher_uses_configured_effect_lock_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_dir = tmp_path / "config" / "trigger-orchestrator"
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text(
        json.dumps(
            {
                "runtime_dir": "custom-workflow-runtime",
                "effect_lock_path": "locks/custom-effect-orchestrator.lock",
            }
        ),
        encoding="utf-8",
    )
    service = build_admin_runtime(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="receptionist-session",
            vm_id="evander-receptionist-vm",
            expires_at=_active_expires_at(),
            allowed_tools=[],
            model="gpt-5.4",
        ),
    )

    watcher: dict[str, object] = {}

    def fake_run_subprocess(cmd: list[str], **kwargs: object):
        return subprocess.CompletedProcess(cmd, 0, stdout="thread_id=thread-1\nok\n", stderr="")

    class _PopenStub:
        def __init__(self, cmd: list[str], **kwargs: object) -> None:
            watcher["cmd"] = cmd

    monkeypatch.setattr(
        "evander.admin.services.process_helpers.run_subprocess", fake_run_subprocess
    )
    monkeypatch.setattr("evander.admin.services.vm_agent.vm_resume.subprocess.Popen", _PopenStub)

    result = service.vm_agent_admin.resume_agent(
        "evander-receptionist-vm",
        "Resume now",
        dispatch_id="dispatch-custom-lock",
    )

    assert result.success is True
    assert "--effect-lock-path" in watcher["cmd"]
    lock_index = watcher["cmd"].index("--effect-lock-path")
    assert watcher["cmd"][lock_index + 1] == str(
        tmp_path / "locks" / "custom-effect-orchestrator.lock"
    )


def test_resume_agent_passes_mcp_gateway_url(tmp_path: Path, monkeypatch) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="receptionist-session",
            vm_id="evander-receptionist-vm",
            expires_at=_active_expires_at(),
            allowed_tools=[],
            model="gpt-5.4",
        ),
    )
    captured: dict[str, object] = {}

    def fake_run_subprocess(cmd: list[str], **kwargs: object):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="thread_id=thread-mcp-1\nok\n", stderr="")

    monkeypatch.setattr(
        "evander.admin.services.process_helpers.run_subprocess", fake_run_subprocess
    )

    result = service.vm_agent_admin.resume_agent(
        "evander-receptionist-vm",
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
        "http://gateway.test/mcp?session_id=receptionist-session&mcp_tool_approval_lease_id=lease-1"
    )


def test_resume_agent_does_not_reuse_stored_session_thread_id(tmp_path: Path, monkeypatch) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="receptionist-session",
            vm_id="evander-receptionist-vm",
            expires_at=_active_expires_at(),
            allowed_tools=[],
            model="gpt-5.4",
            metadata={"agent_thread_id": "thread-stored-1"},
        ),
    )

    captured: dict[str, object] = {}

    def fake_run_subprocess(cmd: list[str], **kwargs: object):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr(
        "evander.admin.services.process_helpers.run_subprocess", fake_run_subprocess
    )

    result = service.vm_agent_admin.resume_agent("evander-receptionist-vm", "Resume now")

    assert result.success is True
    assert result.thread_id is None
    assert "--resume-thread-id" not in captured["cmd"]
    assert "--resume-mode" in captured["cmd"]
    assert "new_context" in captured["cmd"]


def test_resume_agent_ignores_synthetic_chat_thread_id(tmp_path: Path, monkeypatch) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="assistant-session",
            vm_id="assistant-shared",
            expires_at=_active_expires_at(),
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

    monkeypatch.setattr(
        "evander.admin.services.process_helpers.run_subprocess", fake_run_subprocess
    )

    result = service.vm_agent_admin.resume_agent("assistant-shared", "Resume now")

    assert result.success is True
    assert result.thread_id == "thread-real-1"
    assert "--resume-thread-id" not in captured["cmd"]
    assert "--resume-mode" in captured["cmd"]
    assert "new_context" in captured["cmd"]


def test_resume_agent_persists_returned_thread_id_to_session_metadata(
    tmp_path: Path, monkeypatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="receptionist-session",
            vm_id="evander-receptionist-vm",
            expires_at=_active_expires_at(),
            allowed_tools=[],
            model="gpt-5.4",
        ),
    )

    def fake_run_subprocess(cmd: list[str], **kwargs: object):
        return subprocess.CompletedProcess(
            cmd, 0, stdout="thread_id=thread-fresh-1\nok\n", stderr=""
        )

    monkeypatch.setattr(
        "evander.admin.services.process_helpers.run_subprocess", fake_run_subprocess
    )

    result = service.vm_agent_admin.resume_agent("evander-receptionist-vm", "Resume now")

    assert result.success is True
    assert result.thread_id == "thread-fresh-1"
    stored = service.runtime.session_repository.get_session("receptionist-session")
    assert stored is not None
    assert stored.metadata["agent_thread_id"] == "thread-fresh-1"


def test_resume_agent_supports_same_context_mode(tmp_path: Path, monkeypatch) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="receptionist-session",
            vm_id="evander-receptionist-vm",
            expires_at=_active_expires_at(),
            allowed_tools=[],
            model="gpt-5.4",
        ),
    )

    captured: dict[str, object] = {}

    def fake_run_subprocess(cmd: list[str], **kwargs: object):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr(
        "evander.admin.services.process_helpers.run_subprocess", fake_run_subprocess
    )

    result = service.vm_agent_admin.resume_agent(
        "evander-receptionist-vm",
        "Resume now",
        resume_mode="same_context",
    )

    assert result.success is True
    assert "--resume-mode" in captured["cmd"]
    assert "same_context" in captured["cmd"]


def test_resume_agent_uses_explicit_model_override(tmp_path: Path, monkeypatch) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="receptionist-session",
            vm_id="evander-receptionist-vm",
            expires_at=_active_expires_at(),
            allowed_tools=[],
            model="gpt-5.2-codex",
            web_search_enabled=True,
        ),
    )

    captured: dict[str, object] = {}

    def fake_run_subprocess(cmd: list[str], **kwargs: object):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(
            cmd, 0, stdout="thread_id=thread-model-1\nok\n", stderr=""
        )

    monkeypatch.setattr(
        "evander.admin.services.process_helpers.run_subprocess", fake_run_subprocess
    )

    result = service.vm_agent_admin.resume_agent(
        "evander-receptionist-vm",
        "Resume now",
        model="gpt-5.4",
    )

    assert result.success is True
    assert result.thread_id == "thread-model-1"
    assert captured["env"]["AGENT_MODEL"] == "gpt-5.4"
    assert captured["env"]["AGENT_WEB_SEARCH_MODE"] == "live"


def test_resume_agent_times_out_cleanly(tmp_path: Path, monkeypatch) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="receptionist-session",
            vm_id="evander-receptionist-vm",
            expires_at=_active_expires_at(),
            allowed_tools=[],
            model="gpt-5.4",
        ),
    )
    monkeypatch.setenv("EVANDER_ADMIN_RESUME_TIMEOUT_SECONDS", "7")

    def fake_run_subprocess(cmd: list[str], **kwargs: object):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))

    monkeypatch.setattr(
        "evander.admin.services.process_helpers.run_subprocess", fake_run_subprocess
    )

    result = service.vm_agent_admin.resume_agent("evander-receptionist-vm", "Resume now")

    assert result.success is False
    assert result.action == "resume_issue_agent"
    assert result.message == "resume_issue_agent.sh timed out after 7s"


def test_probe_agent_state_accepts_codex_resume_state(tmp_path: Path, monkeypatch) -> None:
    write_config(tmp_path)
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.resolve_private_key_path",
        lambda: Path("/tmp/test-key"),
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

    monkeypatch.setattr(
        "evander.admin.services.process_helpers.run_subprocess",
        _fake_run_subprocess,
    )

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
    service = build_admin_runtime(write_config(tmp_path))
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.dominfo_field",
        lambda vm_name, field_name: "running",
    )
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.resolve_vm_ip", lambda vm_name: "192.168.122.11"
    )
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.resolve_private_key_path",
        lambda: Path("/tmp/test-key"),
    )

    captured: dict[str, object] = {}

    def _fake_run_subprocess(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = cmd
        captured["input"] = kwargs.get("input")
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout="Repo: codex-heartbeat\nLog: /var/log/evander/agent-runs/codex-heartbeat-run.log\n",
            stderr="",
        )

    monkeypatch.setattr(
        "evander.admin.services.process_helpers.run_subprocess", _fake_run_subprocess
    )

    output = service.vm_agent_admin.get_agent_log_snapshot("bug-finder-vm")

    assert "Repo: codex-heartbeat" in output
    assert "codex-heartbeat-run.log" in output
    remote_script = str(captured["input"])
    assert 'candidates+=("codex-heartbeat")' in remote_script
    assert 'LOG_PATH="${RUN_LOG_DIR}/codex-heartbeat-run.log"' in remote_script
    assert 'LEGACY_LOG_PATH="${LOG_DIR}/codex-heartbeat-run.log"' in remote_script
    assert 'agent_home / ".config" / "evander" / "codex" / "sessions"' in remote_script
    assert 'matches = re.findall(r"(?im)^session id:' in remote_script
    assert 'payload_type == "task_complete"' in remote_script
    assert 'payload.get("last_agent_message")' in remote_script
    assert "agent-admin@192.168.122.11" in " ".join(str(part) for part in captured["cmd"])


def test_get_agent_log_snapshot_falls_back_to_legacy_agent_user(
    tmp_path: Path, monkeypatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.dominfo_field",
        lambda vm_name, field_name: "running",
    )
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.resolve_vm_ip", lambda vm_name: "192.168.122.11"
    )
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.resolve_private_key_path",
        lambda: Path("/tmp/test-key"),
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
            stdout="Repo: ExampleIndustryAPP\nLog: /home/agent/.evander/agent-ExampleIndustryAPP.log\nlegacy line\n",
            stderr="",
        )

    monkeypatch.setattr(
        "evander.admin.services.process_helpers.run_subprocess", _fake_run_subprocess
    )

    output = service.vm_agent_admin.get_agent_log_snapshot(
        "bug-finder-vm", repo_name="ExampleIndustryAPP"
    )

    assert "legacy line" in output
    assert any("agent-admin@192.168.122.11" in cmd for cmd in calls)
    assert any("agent@192.168.122.11" in cmd for cmd in calls)


def test_get_agent_log_snapshot_returns_unavailable_when_ssh_times_out(
    tmp_path: Path, monkeypatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.dominfo_field",
        lambda vm_name, field_name: "running",
    )
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.resolve_vm_ip", lambda vm_name: "192.168.122.11"
    )
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.resolve_private_key_path",
        lambda: Path("/tmp/test-key"),
    )

    calls: list[list[str]] = []

    def _fake_run_subprocess(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        raise subprocess.TimeoutExpired(cmd, timeout=20.0)

    monkeypatch.setattr(
        "evander.admin.services.process_helpers.run_subprocess", _fake_run_subprocess
    )

    output = service.vm_agent_admin.get_agent_log_snapshot(
        "bug-finder-vm", repo_name="codex-heartbeat"
    )

    assert output == (
        "Agent log unavailable: agent@192.168.122.11: log read timed out after 20 seconds"
    )
    assert any("agent-admin@192.168.122.11" in cmd for cmd in calls)
    assert any("agent@192.168.122.11" in cmd for cmd in calls)


def test_get_admin_agent_log_page_pages_libvirt_tail(tmp_path: Path, monkeypatch) -> None:
    service = build_admin_runtime(write_config(tmp_path))

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

    monkeypatch.setattr(service.vm_agent_admin, "get_agent_log_snapshot", fake_snapshot)

    page = service.vm_agent_admin.get_admin_agent_log_page(
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
    service = build_admin_runtime(write_config(tmp_path))
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
        service.vm_agent_admin,
        "container_worker_backend",
        MethodType(lambda self, **_kwargs: fake_backend, service.vm_agent_admin),
    )

    latest_page = service.vm_agent_admin.get_admin_agent_log_page(
        "session-1",
        backend="container_worker_vm",
        limit=50,
    )

    assert "execution=exec-new" in latest_page["text"]
    assert "new line 2" in latest_page["text"]
    assert "exec-old" not in latest_page["text"]
    assert "session transcript" not in latest_page["text"]
    assert "augmented" not in latest_page["text"]
    assert latest_page["has_more_before"] is True
    assert latest_page["total_lines"] is None
    assert latest_page["view"] == "executions"
    assert fake_backend.calls == [("exec-new", 0, "execution")]

    older_page = service.vm_agent_admin.get_admin_agent_log_page(
        "session-1",
        backend="container_worker_vm",
        limit=10,
        offset=int(latest_page["returned_lines"]),
    )

    assert "execution=exec-old" in older_page["text"]
    assert "old line 2" in older_page["text"]
    assert "exec-new" not in older_page["text"]
    assert "session transcript" not in older_page["text"]
    assert "augmented" not in older_page["text"]
    assert older_page["has_more_before"] is False
    assert older_page["view"] == "executions"
    assert fake_backend.calls == [
        ("exec-new", 0, "execution"),
        ("exec-new", 0, "execution"),
        ("exec-old", 0, "execution"),
    ]


def test_get_admin_agent_log_snapshot_and_page_container_session_use_execution_logs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-raw-default",
            vm_provider="container_worker_vm",
            vm_provider_id="target-raw-default",
            directory_path="root",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={
                "execution_backend": "container_worker_vm",
                "target_name": "target-raw-default",
            },
        )
    )
    repository = ContainerWorkerExecutionRepository(
        service.config.runtime_dir / "container-worker-executions.json"
    )
    repository.put(
        ContainerWorkerExecutionRecord(
            execution_id="exec-raw-default",
            session_id="session-raw-default",
            worker_job_id="job-raw-default",
            worker_url="http://worker.test",
            target_name="target-raw-default",
            last_status="running",
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
            if mode == "execution":
                return "execution transcript for exec-raw-default\n"
            if mode == "raw":
                return "raw stdout for exec-raw-default\n"
            if mode == "session":
                return "session transcript for exec-raw-default\n"
            if mode == "augmented":
                return "augmented transcript for exec-raw-default\n"
            raise AssertionError(f"unexpected mode: {mode}")

    fake_backend = FakeContainerBackend()
    monkeypatch.setattr(
        service.vm_agent_admin,
        "container_worker_backend",
        MethodType(lambda self, **_kwargs: fake_backend, service.vm_agent_admin),
    )

    snapshot_text = service.vm_agent_admin.get_admin_agent_log_snapshot(
        "session-raw-default",
        backend="container_worker_vm",
    )
    page = service.vm_agent_admin.get_admin_agent_log_page(
        "session-raw-default",
        backend="container_worker_vm",
        limit=20,
    )

    assert "execution transcript for exec-raw-default" in snapshot_text
    assert "raw stdout for exec-raw-default" not in snapshot_text
    assert "session transcript for exec-raw-default" not in snapshot_text
    assert "augmented transcript for exec-raw-default" not in snapshot_text
    assert "log=execution" in snapshot_text
    assert "execution transcript for exec-raw-default" in page["text"]
    assert "raw stdout for exec-raw-default" not in page["text"]
    assert "session transcript for exec-raw-default" not in page["text"]
    assert "augmented transcript for exec-raw-default" not in page["text"]
    assert page["view"] == "executions"
    assert all(mode == "execution" for _, _, mode in fake_backend.calls)


def test_get_admin_agent_log_page_container_session_threads_group_by_thread_id(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-threads",
            vm_provider="container_worker_vm",
            vm_provider_id="target-threads",
            directory_path="root",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={
                "execution_backend": "container_worker_vm",
                "target_name": "target-threads",
            },
        )
    )
    repository = ContainerWorkerExecutionRepository(
        service.config.runtime_dir / "container-worker-executions.json"
    )
    repository.put(
        ContainerWorkerExecutionRecord(
            execution_id="exec-a2",
            session_id="session-threads",
            worker_job_id="job-a2",
            worker_url="http://worker.test",
            target_name="target-threads",
            started_at=datetime(2026, 5, 18, 8, 30, tzinfo=UTC),
            last_status="running",
            directory_path="root",
        )
    )
    repository.put(
        ContainerWorkerExecutionRecord(
            execution_id="exec-b1",
            session_id="session-threads",
            worker_job_id="job-b1",
            worker_url="http://worker.test",
            target_name="target-threads",
            started_at=datetime(2026, 5, 18, 7, 0, tzinfo=UTC),
            last_status="running",
            directory_path="root",
        )
    )
    repository.put(
        ContainerWorkerExecutionRecord(
            execution_id="exec-a1",
            session_id="session-threads",
            worker_job_id="job-a1",
            worker_url="http://worker.test",
            target_name="target-threads",
            started_at=datetime(2026, 5, 18, 8, 0, tzinfo=UTC),
            last_status="running",
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
            if mode == "raw":
                if target == "exec-b1":
                    return "session id: thread-b\nraw log for b1\n"
                if target == "exec-a1":
                    return "session id: thread-a\nraw log for a1\n"
                if target == "exec-a2":
                    return "session id: thread-a\nraw log for a2\n"
            if mode == "session":
                if target == "exec-b1":
                    return "thread-b transcript\n"
                if target == "exec-a1":
                    return "thread-a transcript\n"
                raise AssertionError(f"unexpected session fetch for {target}")
            raise AssertionError(f"unexpected log request: {target} {mode}")

    fake_backend = FakeContainerBackend()
    monkeypatch.setattr(
        service.vm_agent_admin,
        "container_worker_backend",
        MethodType(lambda self, **_kwargs: fake_backend, service.vm_agent_admin),
    )

    page = service.vm_agent_admin.get_admin_agent_log_page(
        "session-threads",
        backend="container_worker_vm",
        view="threads",
        limit=200,
    )

    assert page["view"] == "threads"
    assert page["text"].count("thread=thread-b") == 1
    assert page["text"].count("thread=thread-a") == 1
    assert page["text"].index("thread=thread-b") < page["text"].index("thread=thread-a")
    assert "first_execution=exec-b1" in page["text"]
    assert "first_execution=exec-a1" in page["text"]
    assert "executions=1" in page["text"]
    assert "executions=2" in page["text"]
    assert "thread-b transcript" in page["text"]
    assert "thread-a transcript" in page["text"]
    assert fake_backend.calls == [
        ("exec-b1", 0, "raw"),
        ("exec-a1", 0, "raw"),
        ("exec-a2", 0, "raw"),
        ("exec-b1", 0, "session"),
        ("exec-a1", 0, "session"),
    ]


def test_get_admin_agent_log_page_container_session_threads_use_stored_thread_ids(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-stored-threads",
            vm_provider="container_worker_vm",
            vm_provider_id="target-stored-threads",
            directory_path="root",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={
                "execution_backend": "container_worker_vm",
                "target_name": "target-stored-threads",
            },
        )
    )
    repository = ContainerWorkerExecutionRepository(
        service.config.runtime_dir / "container-worker-executions.json"
    )
    repository.put(
        ContainerWorkerExecutionRecord(
            execution_id="exec-stored-1",
            session_id="session-stored-threads",
            worker_job_id="job-stored-1",
            worker_url="http://worker.test",
            target_name="target-stored-threads",
            started_at=datetime(2026, 5, 18, 8, 0, tzinfo=UTC),
            last_status="removed",
            directory_path="root",
            metadata={"thread_id": "thread-stored"},
        )
    )
    repository.put(
        ContainerWorkerExecutionRecord(
            execution_id="exec-stored-2",
            session_id="session-stored-threads",
            worker_job_id="job-stored-2",
            worker_url="http://worker.test",
            target_name="target-stored-threads",
            started_at=datetime(2026, 5, 18, 8, 30, tzinfo=UTC),
            last_status="removed",
            directory_path="root",
            metadata={"resume_thread_id": "thread-stored"},
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
            if mode == "raw":
                raise AssertionError("stored thread ids should avoid raw log probes")
            if mode == "session" and target == "exec-stored-1":
                return "stored thread transcript\n"
            raise AssertionError(f"unexpected log request: {target} {mode}")

    fake_backend = FakeContainerBackend()
    monkeypatch.setattr(
        service.vm_agent_admin,
        "container_worker_backend",
        MethodType(lambda self, **_kwargs: fake_backend, service.vm_agent_admin),
    )

    page = service.vm_agent_admin.get_admin_agent_log_page(
        "session-stored-threads",
        backend="container_worker_vm",
        view="threads",
        limit=200,
    )

    assert page["view"] == "threads"
    assert page["text"].count("thread=thread-stored") == 1
    assert "executions=2" in page["text"]
    assert "stored thread transcript" in page["text"]
    assert fake_backend.calls == [("exec-stored-1", 0, "session")]


def test_get_admin_agent_log_page_container_session_threads_falls_back_to_execution_logs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-claude-threads",
            vm_provider="container_worker_vm",
            vm_provider_id="target-claude-threads",
            directory_path="root",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={
                "execution_backend": "container_worker_vm",
                "target_name": "target-claude-threads",
                "agent_provider": "claude",
            },
        )
    )
    repository = ContainerWorkerExecutionRepository(
        service.config.runtime_dir / "container-worker-executions.json"
    )
    repository.put(
        ContainerWorkerExecutionRecord(
            execution_id="exec-claude-1",
            session_id="session-claude-threads",
            worker_job_id="job-claude-1",
            worker_url="http://worker.test",
            target_name="target-claude-threads",
            started_at=datetime(2026, 6, 8, 8, 0, tzinfo=UTC),
            last_status="removed",
            directory_path="root",
        )
    )
    repository.put(
        ContainerWorkerExecutionRecord(
            execution_id="exec-claude-2",
            session_id="session-claude-threads",
            worker_job_id="job-claude-2",
            worker_url="http://worker.test",
            target_name="target-claude-threads",
            started_at=datetime(2026, 6, 8, 8, 30, tzinfo=UTC),
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
            if mode == "raw":
                return "session id: claude-thread\nraw metadata only\n"
            if mode == "session":
                return ""
            if mode == "execution":
                if target == "exec-claude-1":
                    return "session id: claude-thread\nClaude response from first execution.\n"
                if target == "exec-claude-2":
                    return "session id: claude-thread\nClaude response from second execution.\n"
            raise AssertionError(f"unexpected log request: {target} {mode}")

    fake_backend = FakeContainerBackend()
    monkeypatch.setattr(
        service.vm_agent_admin,
        "container_worker_backend",
        MethodType(lambda self, **_kwargs: fake_backend, service.vm_agent_admin),
    )

    page = service.vm_agent_admin.get_admin_agent_log_page(
        "session-claude-threads",
        backend="container_worker_vm",
        view="threads",
        limit=200,
    )

    assert page["view"] == "threads"
    assert page["text"].count("thread=claude-thread") == 1
    assert "first_execution=exec-claude-1" in page["text"]
    assert "executions=2" in page["text"]
    assert "Claude response from first execution." in page["text"]
    assert "Claude response from second execution." in page["text"]
    assert "\nsession id: claude-thread\n" not in page["text"]
    assert fake_backend.calls == [
        ("exec-claude-1", 0, "raw"),
        ("exec-claude-2", 0, "raw"),
        ("exec-claude-1", 0, "session"),
        ("exec-claude-1", 0, "execution"),
        ("exec-claude-2", 0, "execution"),
    ]


def test_get_admin_agent_log_page_container_session_threads_fetches_offset_window(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-thread-window",
            vm_provider="container_worker_vm",
            vm_provider_id="target-thread-window",
            directory_path="root",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={
                "execution_backend": "container_worker_vm",
                "target_name": "target-thread-window",
            },
        )
    )
    repository = ContainerWorkerExecutionRepository(
        service.config.runtime_dir / "container-worker-executions.json"
    )
    repository.put(
        ContainerWorkerExecutionRecord(
            execution_id="exec-thread-window",
            session_id="session-thread-window",
            worker_job_id="job-thread-window",
            worker_url="http://worker.test",
            target_name="target-thread-window",
            started_at=datetime(2026, 5, 18, 8, 0, tzinfo=UTC),
            last_status="running",
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
            if mode == "raw" and target == "exec-thread-window":
                return "session id: thread-window\nraw log\n"
            if mode == "session" and target == "exec-thread-window":
                transcript_lines = [f"thread line {index}" for index in range(1, 11)]
                selected_lines = transcript_lines if lines <= 0 else transcript_lines[-lines:]
                return "\n".join(selected_lines) + "\n"
            raise AssertionError(f"unexpected log request: {target} {mode}")

    fake_backend = FakeContainerBackend()
    monkeypatch.setattr(
        service.vm_agent_admin,
        "container_worker_backend",
        MethodType(lambda self, **_kwargs: fake_backend, service.vm_agent_admin),
    )

    page = service.vm_agent_admin.get_admin_agent_log_page(
        "session-thread-window",
        backend="container_worker_vm",
        view="threads",
        limit=3,
        offset=3,
    )

    assert page["view"] == "threads"
    assert page["text"] == "thread line 3\nthread line 4\nthread line 5\n"
    assert page["has_more_before"] is True
    assert fake_backend.calls == [
        ("exec-thread-window", 0, "raw"),
        ("exec-thread-window", 0, "session"),
    ]


def test_get_admin_agent_log_page_container_session_threads_lazily_reads_large_history(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-thread-large-history",
            vm_provider="container_worker_vm",
            vm_provider_id="target-thread-large-history",
            directory_path="root",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={
                "execution_backend": "container_worker_vm",
                "target_name": "target-thread-large-history",
            },
        )
    )
    repository = ContainerWorkerExecutionRepository(
        service.config.runtime_dir / "container-worker-executions.json"
    )
    for index in range(150):
        repository.put(
            ContainerWorkerExecutionRecord(
                execution_id=f"exec-large-{index:03d}",
                session_id="session-thread-large-history",
                worker_job_id=f"job-large-{index:03d}",
                worker_url="http://worker.test",
                target_name="target-thread-large-history",
                started_at=datetime(2026, 5, 18, 8, 0, tzinfo=UTC) + timedelta(minutes=index),
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
            if target != "exec-large-000":
                raise AssertionError(f"large thread page should not probe {target}")
            if mode == "raw":
                return "session id: thread-large-000\nraw log\n"
            if mode == "session":
                return "\n".join(f"large thread line {line}" for line in range(1, 360)) + "\n"
            raise AssertionError(f"unexpected log request: {target} {mode}")

    fake_backend = FakeContainerBackend()
    monkeypatch.setattr(
        service.vm_agent_admin,
        "container_worker_backend",
        MethodType(lambda self, **_kwargs: fake_backend, service.vm_agent_admin),
    )

    page = service.vm_agent_admin.get_admin_agent_log_page(
        "session-thread-large-history",
        backend="container_worker_vm",
        view="threads",
        limit=300,
    )

    assert page["view"] == "threads"
    assert page["has_more_before"] is True
    assert page["total_lines"] is None
    assert "thread=thread-large-000" in page["text"]
    assert "large thread line 1" in page["text"]
    assert fake_backend.calls == [
        ("exec-large-000", 0, "raw"),
        ("exec-large-000", 0, "session"),
    ]


def test_get_admin_agent_log_page_container_session_threads_large_history_rebuilds_grouped_fallback(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-thread-large-fallback",
            vm_provider="container_worker_vm",
            vm_provider_id="target-thread-large-fallback",
            directory_path="root",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={
                "execution_backend": "container_worker_vm",
                "target_name": "target-thread-large-fallback",
            },
        )
    )
    repository = ContainerWorkerExecutionRepository(
        service.config.runtime_dir / "container-worker-executions.json"
    )
    for index in range(150):
        thread_metadata_key = "thread_id" if index == 0 else "resume_thread_id"
        repository.put(
            ContainerWorkerExecutionRecord(
                execution_id=f"exec-large-fallback-{index:03d}",
                session_id="session-thread-large-fallback",
                worker_job_id=f"job-large-fallback-{index:03d}",
                worker_url="http://worker.test",
                target_name="target-thread-large-fallback",
                started_at=datetime(2026, 5, 18, 8, 0, tzinfo=UTC) + timedelta(minutes=index),
                last_status="removed",
                directory_path="root",
                metadata={thread_metadata_key: "thread-large-fallback"},
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
            if mode == "raw":
                raise AssertionError("stored thread ids should avoid raw log probes")
            if mode == "session":
                return ""
            if mode == "execution":
                return f"fallback body for {target}\n"
            raise AssertionError(f"unexpected log request: {target} {mode}")

    fake_backend = FakeContainerBackend()
    monkeypatch.setattr(
        service.vm_agent_admin,
        "container_worker_backend",
        MethodType(lambda self, **_kwargs: fake_backend, service.vm_agent_admin),
    )

    page = service.vm_agent_admin.get_admin_agent_log_page(
        "session-thread-large-fallback",
        backend="container_worker_vm",
        view="threads",
        limit=400,
    )

    assert page["view"] == "threads"
    assert "thread=thread-large-fallback" in page["text"]
    assert "executions=150" in page["text"]
    assert "fallback body for exec-large-fallback-000" in page["text"]
    assert "fallback body for exec-large-fallback-149" in page["text"]
    assert ("exec-large-fallback-149", 0, "execution") in fake_backend.calls
    assert all(mode != "raw" for _target, _lines, mode in fake_backend.calls)


def test_get_admin_agent_log_page_container_session_threads_searches_full_transcript(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-thread-search",
            vm_provider="container_worker_vm",
            vm_provider_id="target-thread-search",
            directory_path="root",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={
                "execution_backend": "container_worker_vm",
                "target_name": "target-thread-search",
            },
        )
    )
    repository = ContainerWorkerExecutionRepository(
        service.config.runtime_dir / "container-worker-executions.json"
    )
    repository.put(
        ContainerWorkerExecutionRecord(
            execution_id="exec-thread-search",
            session_id="session-thread-search",
            worker_job_id="job-thread-search",
            worker_url="http://worker.test",
            target_name="target-thread-search",
            started_at=datetime(2026, 5, 18, 8, 0, tzinfo=UTC),
            last_status="running",
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
            if mode == "raw" and target == "exec-thread-search":
                return "session id: thread-search\nraw log\n"
            if mode == "session" and target == "exec-thread-search":
                transcript_lines = [
                    "older needle match",
                    "middle line",
                    "recent line",
                ]
                selected_lines = transcript_lines if lines <= 0 else transcript_lines[-lines:]
                return "\n".join(selected_lines) + "\n"
            raise AssertionError(f"unexpected log request: {target} {mode}")

    fake_backend = FakeContainerBackend()
    monkeypatch.setattr(
        service.vm_agent_admin,
        "container_worker_backend",
        MethodType(lambda self, **_kwargs: fake_backend, service.vm_agent_admin),
    )

    page = service.vm_agent_admin.get_admin_agent_log_page(
        "session-thread-search",
        backend="container_worker_vm",
        view="threads",
        limit=1,
        query="needle",
    )

    assert page["view"] == "threads"
    assert page["text"] == "older needle match\n"
    assert page["total_lines"] == 1
    assert fake_backend.calls == [
        ("exec-thread-search", 0, "raw"),
        ("exec-thread-search", 0, "session"),
    ]


def test_get_admin_agent_log_page_container_session_threads_render_unthreaded_execution(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-unthreaded",
            vm_provider="container_worker_vm",
            vm_provider_id="target-unthreaded",
            directory_path="root",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={
                "execution_backend": "container_worker_vm",
                "target_name": "target-unthreaded",
            },
        )
    )
    repository = ContainerWorkerExecutionRepository(
        service.config.runtime_dir / "container-worker-executions.json"
    )
    repository.put(
        ContainerWorkerExecutionRecord(
            execution_id="exec-alone",
            session_id="session-unthreaded",
            worker_job_id="job-alone",
            worker_url="http://worker.test",
            target_name="target-unthreaded",
            started_at=datetime(2026, 5, 18, 9, 0, tzinfo=UTC),
            last_status="running",
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
            if mode == "raw" and target == "exec-alone":
                return "raw log without thread id\n"
            raise AssertionError(f"unexpected log request: {target} {mode}")

    fake_backend = FakeContainerBackend()
    monkeypatch.setattr(
        service.vm_agent_admin,
        "container_worker_backend",
        MethodType(lambda self, **_kwargs: fake_backend, service.vm_agent_admin),
    )

    page = service.vm_agent_admin.get_admin_agent_log_page(
        "session-unthreaded",
        backend="container_worker_vm",
        view="threads",
        limit=200,
    )

    assert page["view"] == "threads"
    assert "thread=unthreaded" in page["text"]
    assert "first_execution=exec-alone" in page["text"]
    assert "executions=1" in page["text"]
    assert "raw log without thread id" in page["text"]
    assert fake_backend.calls == [("exec-alone", 0, "raw")]


def test_list_sessions_filters_active_and_expired(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
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

    active = service.vm_agent_admin.list_sessions()
    expired = service.vm_agent_admin.list_sessions(expired_only=True)
    all_sessions = service.vm_agent_admin.list_sessions(include_expired=True)

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
    service = build_admin_runtime(write_config(tmp_path))
    now = datetime(2026, 5, 21, 12, 0, tzinfo=UTC)
    monkeypatch.setattr("evander.admin.services.vm_agent_service._utc_now", lambda: now)
    monkeypatch.setattr(
        "evander.execution.container_worker_repository.now_utc",
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

    summaries = {
        item.session_id: item for item in service.vm_agent_admin.list_sessions(include_expired=True)
    }
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
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    root_dir = service.directory_admin.create_child(parent="root", name="team")
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
    service.issue_git_admin.create_local_issue(title="Root issue", body="a", directory_path="root")
    service.issue_git_admin.create_local_issue(
        title="Team issue", body="b", directory_path=team_path
    )

    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.virsh_lines",
        lambda args: ["vm-101", "vm-root", "vm-free"],
    )
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.dominfo_field",
        lambda vm_name, field_name: "shut off",
    )

    team_sessions = service.vm_agent_admin.list_sessions(
        include_expired=True, directory_path=team_path, direct_only=True
    )
    team_issues = service.issue_git_admin.list_local_issues(
        directory_path=team_path, direct_only=True
    )
    team_vms = service.vm_agent_admin.list_vms(
        include_inactive=True, directory_path=team_path, direct_only=True
    )
    root_vms = service.vm_agent_admin.list_vms(
        include_inactive=True, directory_path="root", direct_only=True
    )

    assert [summary.session_id for summary in team_sessions] == ["team-session"]
    assert [issue.title for issue in team_issues] == ["Team issue"]
    assert [summary.vm_name for summary in team_vms] == ["vm-101"]
    assert {summary.vm_name for summary in root_vms} == {"vm-root", "vm-free"}


def test_list_issues_sorts_issue_numbers_numerically(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    low_path = tmp_path / "issue-2.yaml"
    low_path.write_text("issue_number: 2\ntitle: Low issue\nbody: body\n", encoding="utf-8")
    high_path = tmp_path / "issue-10.yaml"
    high_path.write_text("issue_number: 10\ntitle: High issue\nbody: body\n", encoding="utf-8")

    service.issue_git_admin.create_local_issue(yaml_path=str(high_path))
    service.issue_git_admin.create_local_issue(yaml_path=str(low_path))

    issues = service.issue_git_admin.list_issues()

    assert [issue.issue_number for issue in issues] == ["2", "10"]


def test_list_local_issues_supports_state_filters(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    active_issue = service.issue_git_admin.create_local_issue(title="Active", body="body")
    completed_issue = service.issue_git_admin.create_local_issue(title="Completed", body="body")
    closed_issue = service.issue_git_admin.create_local_issue(title="Closed", body="body")

    service.issue_git_admin.update_issue(active_issue.issue_number, state="In Progress")
    service.issue_git_admin.update_issue(completed_issue.issue_number, state="Completed")
    service.issue_git_admin.update_issue(closed_issue.issue_number, state="Closed")

    active_only = service.issue_git_admin.list_local_issues(exclude_states={"Completed", "Closed"})
    completed_only = service.issue_git_admin.list_local_issues(states={"Completed"})

    assert [issue.title for issue in active_only] == ["Active"]
    assert [issue.title for issue in completed_only] == ["Completed"]


def test_list_approval_requests_filters_health_and_redacts_payload(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    created = service.directory_admin.create_child(parent="root", name="team")
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

    pending = service.approval_audit_admin.list_approval_requests(
        statuses={ApprovalStatus.PENDING},
        directory_path=team_path,
        direct_only=True,
        tool_name="email_send_approved",
    )
    health = service.approval_audit_admin.approval_queue_health(
        directory_path=team_path, direct_only=True
    )
    root_descendants = service.approval_audit_admin.list_approval_requests(
        directory_path="root",
        direct_only=False,
    )
    root_direct = service.approval_audit_admin.list_approval_requests(
        directory_path="root",
        direct_only=True,
    )
    root_descendant_health = service.approval_audit_admin.approval_queue_health(
        directory_path="root",
        direct_only=False,
    )
    review = service.approval_audit_admin.get_approval_review("approval-pending")

    assert [request.request_id for request in pending] == ["approval-pending"]
    assert health.total == 2
    assert health.pending == 1
    assert health.denied == 1
    assert {request.request_id for request in root_descendants} == {
        "approval-pending",
        "approval-denied",
    }
    assert root_direct == []
    assert root_descendant_health.total == 2
    assert (
        review["request"]["intended_effect"] == "Send the approved email payload to the recipient."
    )
    assert review["redacted_payload"]["token"] == "<redacted>"
    assert review["request"]["payload"]["token"] == "<redacted>"
    assert review["request"]["notification_status"] == "partial"
    assert review["request"]["notification_target_ids"] == ["ops-approvals"]
    assert review["request"]["notification_error"] == "notification relay unavailable"
    assert review["request"]["notification_deliveries"][0]["target_id"] == "ops-approvals"
    assert review["request"]["notification_deliveries"][0]["status"] == "failed"
    assert review["request"]["notification_deliveries"][0]["message_id"] == "msg-123"


def test_decide_approval_request_records_reason_history_and_audit(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
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

    decided = service.approval_audit_admin.decide_approval_request(
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
    service = build_admin_runtime(write_config(tmp_path))
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
                json.dumps(
                    {
                        "event_type": "tool_call",
                        "session_id": "session-1",
                        "vm_id": "vm-1",
                        "tool_name": "email_send_approved",
                        "approval_request_id": "approval-1",
                        "decision": "denied",
                        "reason": "payload mismatch",
                        "args": {
                            "session_id": "session-1",
                            "request_id": "approval-1",
                            "token": "blocked-secret",
                        },
                        "result": {
                            "status": "denied",
                            "request_id": "approval-1",
                            "approval_diagnostic": {
                                "approval_request_id": "approval-1",
                                "rejection_kind": "payload_mismatch",
                                "approved_payload_digest": "digest-1",
                                "attempted_payload_digest": "attempted-digest",
                                "approved_payload": {
                                    "body": "hello",
                                    "token": "approved-secret",
                                },
                                "attempted_payload": {
                                    "body": "hello",
                                    "token": "attempted-secret",
                                },
                                "differences": {
                                    "changed": [
                                        {
                                            "path": "payload.token",
                                            "approved_value": "<redacted>",
                                            "attempted_value": "<redacted>",
                                        }
                                    ]
                                },
                                "secret": "blocked-diagnostic-secret",
                            },
                        },
                        "created_at": "2026-05-18T10:03:00+00:00",
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
                        "decision": "denied",
                        "reason": "latest payload mismatch",
                        "args": {
                            "session_id": "session-1",
                            "request_id": "approval-1",
                            "token": "latest-blocked-secret",
                        },
                        "result": {
                            "status": "denied",
                            "request_id": "approval-1",
                            "approval_diagnostic": {
                                "approval_request_id": "approval-1",
                                "rejection_kind": "payload_mismatch",
                                "reason": "latest payload mismatch",
                                "approved_payload_digest": "digest-1",
                                "attempted_payload_digest": "latest-attempted-digest",
                                "approved_payload": {
                                    "body": "hello",
                                    "token": "approved-secret",
                                },
                                "attempted_payload": {
                                    "body": "hello",
                                    "token": "latest-attempted-secret",
                                },
                                "differences": {
                                    "changed": [
                                        {
                                            "path": "payload.token",
                                            "approved_value": "<redacted>",
                                            "attempted_value": "<redacted>",
                                        }
                                    ]
                                },
                                "secret": "latest-diagnostic-secret",
                            },
                        },
                        "created_at": "2026-05-18T10:04:00+00:00",
                    },
                    sort_keys=True,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    review = service.approval_audit_admin.get_approval_review("approval-1")

    assert [entry["event_type"] for entry in review["audit_context"]] == [
        "tool_call",
        "approval_decision",
        "tool_call",
        "tool_call",
        "tool_call",
    ]
    assert {entry["approval_request_id"] for entry in review["audit_context"]} == {"approval-1"}
    decision_entry = review["audit_context"][1]
    blocked_entry = review["audit_context"][4]
    assert decision_entry["args"]["request_id"] == "approval-1"
    assert decision_entry["result"]["status"] == "denied"
    assert decision_entry["result"]["tool_name"] == "email_send_approved"
    assert decision_entry["result"]["payload_digest"] == "digest-1"
    assert review["payload_summary"] == '{"body":"hello"}'
    assert review["approval_diagnostics"]["request_id"] == "approval-1"
    assert review["approval_diagnostics"]["status"] == "pending"
    assert review["approval_diagnostics"]["tool_name"] == "email_send_approved"
    assert review["approval_diagnostics"]["approved_payload"] == {"body": "hello"}
    assert [
        diagnostic["attempted_payload_digest"]
        for diagnostic in review["approval_diagnostics"]["blocked_tool_call_diagnostics"]
    ] == ["latest-attempted-digest", "attempted-digest"]
    assert (
        review["approval_diagnostics"]["blocked_tool_call_diagnostics"][0]["approved_payload"][
            "token"
        ]
        == "<redacted>"
    )
    assert (
        review["approval_diagnostics"]["blocked_tool_call_diagnostics"][0]["attempted_payload"][
            "token"
        ]
        == "<redacted>"
    )
    assert (
        review["approval_diagnostics"]["blocked_tool_call_diagnostics"][0]["differences"][
            "changed"
        ][0]["attempted_value"]
        == "<redacted>"
    )
    assert blocked_entry["result"]["approval_diagnostic"]["secret"] == "<redacted>"
    assert (
        blocked_entry["result"]["approval_diagnostic"]["attempted_payload"]["token"] == "<redacted>"
    )
    serialized_review = json.dumps(review, default=str)
    assert "approved-secret" not in serialized_review
    assert "attempted-secret" not in serialized_review
    assert "latest-attempted-secret" not in serialized_review
    assert "latest-diagnostic-secret" not in serialized_review


def test_admin_service_exposes_directory_config_for_selected_directory(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    created = service.directory_admin.create_child(parent="root", name="team")
    assert created.ok is True and created.data is not None
    team_path = created.data.details.directory.path

    write_result = service.operations_admin.write_directory_domain_config(
        team_path,
        "policy",
        {"default_session_ttl_seconds": 120},
    )
    assert write_result.ok is True

    read_result = service.operations_admin.read_directory_domain_config(team_path, "policy")
    assert read_result.ok is True
    assert read_result.data is not None
    assert read_result.data.config == {"default_session_ttl_seconds": 120}

    effective_result = service.operations_admin.compute_directory_effective_config(
        team_path, "policy"
    )
    assert effective_result.ok is True
    assert effective_result.data is not None
    assert effective_result.data.resolution.effective_config["default_session_ttl_seconds"] == 120


def test_admin_service_reads_and_writes_ado_worker_global_config(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))

    read_result = service.workflow_config_admin.read_worker_global_config("ado")
    assert read_result.exists is False
    assert read_result.path == str(tmp_path / "config" / "ado-worker" / "config.json")
    assert (
        read_result.config["ado_work_items_azure_devops_pat_env"]
        == "ADO_WORK_ITEMS_AZURE_DEVOPS_PAT"
    )
    assert read_result.config["directories"] == []

    write_result = service.workflow_config_admin.write_worker_global_config(
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

    reread = service.workflow_config_admin.read_worker_global_config("ado")
    assert reread.config["include_comments"] is False


def test_admin_service_reads_and_writes_discord_wake_worker_global_config(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))

    read_result = service.workflow_config_admin.read_worker_global_config("discord-wake")
    assert read_result.exists is False
    assert read_result.path == str(tmp_path / "config" / "discord-wake" / "config.json")
    assert (
        read_result.config["poll_interval_seconds"]
        == DiscordWakeWorkerGlobalConfig().poll_interval_seconds
    )
    assert read_result.config["directories"] == []
    assert read_result.config["backoff_seconds"] == DiscordWakeWorkerGlobalConfig().backoff_seconds

    write_result = service.workflow_config_admin.write_worker_global_config(
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

    reread = service.workflow_config_admin.read_worker_global_config("discord-wake")
    assert reread.config["directories"] == ["root/team-a", "root/team-b"]
    assert reread.config["backoff_seconds"] == 45.0
    assert reread.config["vm_user"] == "worker"


def test_admin_service_reads_and_writes_scheduled_wake_worker_global_config(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))

    read_result = service.workflow_config_admin.read_worker_global_config("scheduled-wake")
    assert read_result.exists is False
    assert read_result.path == str(tmp_path / "config" / "scheduled-wake" / "config.json")
    assert (
        read_result.config["poll_interval_seconds"]
        == ScheduledWakeWorkerGlobalConfig().poll_interval_seconds
    )
    assert read_result.config["directories"] == []
    assert (
        read_result.config["backoff_seconds"] == ScheduledWakeWorkerGlobalConfig().backoff_seconds
    )
    assert read_result.config["scheduled_wake_prefix_text"] == ""

    write_result = service.workflow_config_admin.write_worker_global_config(
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

    reread = service.workflow_config_admin.read_worker_global_config("scheduled-wake")
    assert reread.config["directories"] == ["root/team-a"]
    assert reread.config["backoff_seconds"] == 90.0

    legacy_service = build_admin_runtime(write_config(tmp_path / "legacy"))
    legacy_write_result = legacy_service.workflow_config_admin.write_worker_global_config(
        "scheduled-wake",
        {
            "runtime_dir": str(tmp_path / "legacy" / "runtime"),
            "directories": ["root/team-b"],
            "poll_interval_seconds": 25.0,
            "jitter_seconds": 3.0,
            "backoff_seconds": 95.0,
            "cooldown_seconds": 95.0,
            "lock_path": None,
            "session_cooldown_path": None,
            "busy_check_script_path": "scripts/check_session_vm_busy.sh",
            "resume_script_path": "scripts/resume_issue_agent.sh",
            "wake_state_path": None,
            "vm_user": "worker",
            "resume_timeout_seconds": 600.0,
            "wake_scheduled_prefix_text": "Legacy scheduled wake prefix.",
        },
    )
    assert legacy_write_result.config["scheduled_wake_prefix_text"] == (
        "Legacy scheduled wake prefix."
    )

    legacy_payload = json.loads(
        (tmp_path / "legacy" / "config" / "scheduled-wake" / "config.json").read_text(
            encoding="utf-8"
        )
    )
    assert legacy_payload["scheduled_wake_prefix_text"] == "Legacy scheduled wake prefix."
    assert "wake_scheduled_prefix_text" not in legacy_payload


def test_admin_service_reads_and_writes_discord_ingest_worker_global_config(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))

    read_result = service.workflow_config_admin.read_worker_global_config("discord-ingest")
    assert read_result.exists is False
    assert read_result.path == str(tmp_path / "config" / "discord-ingest" / "config.json")
    assert (
        read_result.config["poll_interval_seconds"]
        == DiscordIngestWorkerGlobalConfig().poll_interval_seconds
    )
    assert read_result.config["directories"] == []
    assert read_result.config["backoff_seconds"] == 60.0

    write_result = service.workflow_config_admin.write_worker_global_config(
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

    reread = service.workflow_config_admin.read_worker_global_config("discord-ingest")
    assert reread.config["directories"] == ["root/team-a", "root/team-b"]
    assert reread.config["backoff_seconds"] == 45.0


def test_admin_service_reads_and_writes_teams_ingest_worker_global_config(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))

    read_result = service.workflow_config_admin.read_worker_global_config("teams-ingest")
    assert read_result.exists is False
    assert read_result.path == str(tmp_path / "config" / "teams-ingest" / "config.json")
    assert (
        read_result.config["poll_interval_seconds"]
        == TeamsIngestWorkerGlobalConfig().poll_interval_seconds
    )
    assert read_result.config["directories"] == []
    assert read_result.config["backoff_seconds"] == 60.0

    write_result = service.workflow_config_admin.write_worker_global_config(
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

    reread = service.workflow_config_admin.read_worker_global_config("teams-ingest")
    assert reread.config["directories"] == ["root/team-a", "root/team-b"]
    assert reread.config["backoff_seconds"] == 90.0


def test_admin_service_can_read_and_mutate_trigger_orchestrator_triggers_by_id(
    tmp_path: Path,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))

    read_result = service.workflow_config_admin.read_worker_global_config("trigger-orchestrator")
    assert read_result.exists is False
    assert read_result.path == str(tmp_path / "config" / "trigger-orchestrator" / "config.json")
    assert read_result.config["triggers"] == []
    assert read_result.config["effects"] == []

    added = service.workflow_config_admin.upsert_trigger_config(
        "cron-15m",
        {
            "display_name": "Every 15 minutes",
            "type": "trigger:cron",
            "expression": "*/15 * * * *",
        },
    )
    assert added.operation == "added"
    assert added.config["triggers"][0]["id"] == "cron-15m"

    updated = service.workflow_config_admin.upsert_trigger_config(
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

    removed = service.workflow_config_admin.delete_trigger_config("cron-15m")
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

    service = build_admin_runtime(write_config(tmp_path))
    lock = NonReentrantLock()
    service.operations_admin._admin_config_mutation_lock = lock

    service.workflow_config_admin.upsert_trigger_config(
        "cron-15m",
        {
            "display_name": "Every 15 minutes",
            "type": "trigger:cron",
            "expression": "*/15 * * * *",
        },
    )
    service.workflow_config_admin.delete_trigger_config("cron-15m")
    service.operations_admin.ensure_chat_assistant_workflow()

    assert lock.enter_count == 2


def test_admin_service_can_read_and_mutate_trigger_orchestrator_effects_by_id(
    tmp_path: Path,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.workflow_config_admin.upsert_trigger_config(
        "cron-15m",
        {
            "display_name": "Every 15 minutes",
            "type": "trigger:cron",
            "expression": "*/15 * * * *",
        },
    )

    added = service.workflow_config_admin.upsert_effect_config(
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

    updated = service.workflow_config_admin.upsert_effect_config(
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

    removed = service.workflow_config_admin.delete_effect_config("prompt-agent")
    assert removed.operation == "deleted"
    assert removed.config["effects"] == []
    assert removed.config["edges"] == []


def test_admin_service_rejects_duplicate_trigger_ids_with_safe_error_message(
    tmp_path: Path,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))

    with pytest.raises(
        ValueError,
        match="Node IDs must be globally unique across triggers, gates, effects, and queries: dup",
    ):
        service.workflow_config_admin.write_worker_global_config(
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
        service.workflow_config_admin.upsert_trigger_config(
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
    service = build_admin_runtime(write_config(tmp_path))

    with pytest.raises(ValueError, match="system-managed"):
        service.workflow_config_admin.upsert_trigger_config(
            "system.chat_assistant",
            {
                "display_name": "System Chat Assistant",
                "type": "trigger:manual",
            },
        )

    with pytest.raises(ValueError, match="system-managed"):
        service.workflow_config_admin.delete_trigger_config("system.chat_assistant")

    with pytest.raises(ValueError, match="system-managed"):
        service.workflow_config_admin.upsert_effect_config(
            "system.chat_assistant.enqueue_prompt",
            {
                "display_name": "System enqueue",
                "type": "effect:agent:enqueue-prompt",
                "prompt": "hello",
                "target": "assistant",
            },
        )

    with pytest.raises(ValueError, match="system-managed"):
        service.workflow_config_admin.delete_effect_config("system.chat_assistant.enqueue_prompt")


def test_admin_service_chat_post_publishes_hidden_trigger_event(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    config_path = tmp_path / "config" / "trigger-orchestrator" / "config.json"
    assert not config_path.exists()

    record = service.operations_admin.publish_chat_assistant_message(
        user_id="alice",
        thread_id="thread-1",
        user_message_id="msg-1",
        content="hello",
        backend="container_worker_vm",
    )

    assert record["event_id"] == "chat:msg-1"
    assert not config_path.exists()
    queued = (
        SqliteTriggerEventQueue(tmp_path / "runtime" / "orchestration.db")
        .read_all()["chat:msg-1"]
        .model_dump(mode="json")
    )
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
    service = build_admin_runtime(write_config(tmp_path))
    config_path = tmp_path / "config" / "trigger-orchestrator" / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    original_config = {
        "runtime_dir": str(tmp_path / "runtime"),
        "poll_interval_seconds": 7,
        "workflow_packs": ["agent-container-worker.v1.json"],
    }
    config_path.write_text(json.dumps(original_config, indent=2), encoding="utf-8")

    service.operations_admin.publish_chat_assistant_message(
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
    service = build_admin_runtime(write_config(tmp_path))
    config_path = tmp_path / "config" / "trigger-orchestrator" / "config.json"

    first = service.workflow_config_admin.publish_external_workflow_event(
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
    second = service.workflow_config_admin.publish_external_workflow_event(
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
    service = build_admin_runtime(write_config(tmp_path))

    with pytest.raises(ValueError, match="reserved 'external\\.' prefix"):
        service.workflow_config_admin.publish_external_workflow_event(
            event_type="external.deploy_requested",
            payload={},
        )


def test_workflow_editor_hides_and_does_not_save_system_managed_workflow_nodes(
    tmp_path: Path,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
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

    document = service.workflow_config_admin.read_workflow_editor_document()
    assert [trigger["id"] for trigger in document.config["triggers"]] == ["cron-15m"]
    assert [effect["id"] for effect in document.config["effects"]] == ["prompt-agent"]
    assert document.config["edges"] == []

    save_result = service.workflow_config_admin.save_workflow_editor_document(
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
    service = build_admin_runtime(write_config(tmp_path))

    save_result = service.workflow_config_admin.save_workflow_editor_document(
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

    reread = service.workflow_config_admin.read_workflow_editor_document()
    assert reread.config["edges"] == [{"source_id": "cron-15m", "target_id": "prompt-agent"}]
    assert reread.layout == persisted_layout
    assert reread.revision == save_result.revision
    assert reread.saved_at is not None

    revision_config = dict(reread.config)
    if save_result.graphs:
        revision_config.pop("workflow_graphs", None)
    expected_revision = hashlib.sha256(
        json.dumps(
            {
                "config": revision_config,
                "layout": save_result.layout,
                "graphs": [graph.model_dump(mode="json") for graph in save_result.graphs],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert save_result.revision == expected_revision


def test_workflow_editor_revision_changes_for_layout_only_edit(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
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

    first = service.workflow_config_admin.save_workflow_editor_document(
        config=config,
        layout={"nodes": {"cron-15m": {"x": 100, "y": 50}}},
    )
    second = service.workflow_config_admin.save_workflow_editor_document(
        config=config,
        layout={"nodes": {"cron-15m": {"x": 200, "y": 50}}},
    )

    assert second.revision != first.revision


def test_workflow_editor_revision_changes_for_graph_metadata_only_edit(
    tmp_path: Path,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
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
    first = service.workflow_config_admin.save_workflow_editor_document(
        config=config, layout={"nodes": {}}
    )
    edited_graphs = [graph.model_dump(mode="json") for graph in first.graphs]
    edited_graphs[0]["name"] = "Renamed graph"

    second = service.workflow_config_admin.save_workflow_editor_document(
        config=config,
        layout={"nodes": {}},
        graphs=edited_graphs,
    )

    assert second.revision != first.revision


def test_workflow_editor_inline_save_refreshes_pack_list_revision_for_pack_create(
    tmp_path: Path,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    document = service.workflow_config_admin.read_workflow_editor_document()

    save_result = service.workflow_config_admin.save_workflow_editor_document(
        config={
            "runtime_dir": str(tmp_path / "runtime-after-inline-save"),
            "triggers": [],
            "gates": [],
            "effects": [],
            "queries": [],
            "edges": [],
        },
        layout={"nodes": {}},
        expected_revision=document.revision,
    )
    assert save_result.pack_list_revision is not None
    assert save_result.pack_list_revision != document.pack_list_revision

    create_result = service.workflow_config_admin.create_workflow_pack(
        pack_id="post-save-pack",
        expected_revision=save_result.pack_list_revision,
    )

    assert create_result.selected_workflow_pack is not None
    assert create_result.selected_workflow_pack.reference == "post-save-pack.v1.json"


def test_worker_global_config_revision_changes_for_config_edit(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    first = service.workflow_config_admin.read_worker_global_config("trigger-orchestrator")
    config = dict(first.config)
    config["poll_interval_seconds"] = int(config.get("poll_interval_seconds") or 5) + 1

    second = service.workflow_config_admin.write_worker_global_config(
        "trigger-orchestrator", config
    )

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

    service = build_admin_runtime(runtime_dir)
    document = service.workflow_config_admin.read_workflow_editor_document()

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

    document = build_admin_runtime(
        write_config(tmp_path)
    ).workflow_config_admin.read_workflow_editor_document()

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
    service = build_admin_runtime(write_config(tmp_path))
    document = service.workflow_config_admin.read_workflow_editor_document()
    edited_config = dict(document.config)
    effects = [dict(effect) for effect in edited_config["effects"]]
    effects[0]["display_name"] = "Prompt agent with context"
    edited_config["effects"] = effects

    save_result = service.workflow_config_admin.save_workflow_editor_document(
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

    reread = service.workflow_config_admin.read_workflow_editor_document()
    assert reread.config["effects"][0]["display_name"] == "Prompt agent with context"
    assert reread.layout_path == str(layout_path)
    assert reread.layout["nodes"]["prompt-agent"] == {"x": 300.0, "y": 50.0}


def test_workflow_editor_reads_selected_pack_from_multi_pack_config(tmp_path: Path) -> None:
    packs_dir = tmp_path / "config" / "workflow-packs"
    packs_dir.mkdir(parents=True, exist_ok=True)
    (packs_dir / "pack-a.v1.json").write_text(
        json.dumps(
            {
                "pack_id": "pack-a",
                "pack_version": "v1",
                "graphs": [
                    {
                        "id": "graph-a",
                        "name": "Graph A",
                        "directory_path": "root",
                        "node_ids": ["trigger-a"],
                    }
                ],
                "ir": {
                    "version": "v1",
                    "nodes": [
                        {
                            "id": "trigger-a",
                            "display_name": "Every minute",
                            "type": "trigger:cron",
                            "expression": "* * * * *",
                        }
                    ],
                    "edges": [],
                },
            }
        ),
        encoding="utf-8",
    )
    (packs_dir / "pack-b.v1.json").write_text(
        json.dumps(
            {
                "pack_id": "pack-b",
                "pack_version": "v1",
                "graphs": [
                    {
                        "id": "graph-b",
                        "name": "Graph B",
                        "directory_path": "root/team-a",
                        "node_ids": ["effect-b"],
                    }
                ],
                "ir": {
                    "version": "v1",
                    "nodes": [
                        {
                            "id": "effect-b",
                            "display_name": "Emit event",
                            "type": "effect:event:emit",
                            "event_type": "workflow.synthetic",
                            "payload": {"message": "pack-b"},
                        }
                    ],
                    "edges": [],
                },
            }
        ),
        encoding="utf-8",
    )
    (packs_dir / "pack-b.v1.layout.json").write_text(
        json.dumps({"nodes": {"effect-b": {"x": 220, "y": 160}}}),
        encoding="utf-8",
    )
    config_root = tmp_path / "config" / "trigger-orchestrator"
    config_root.mkdir(parents=True, exist_ok=True)
    (config_root / "config.json").write_text(
        json.dumps(
            {
                "runtime_dir": str(tmp_path / "runtime"),
                "workflow_packs": ["pack-a.v1.json", "pack-b.v1.json"],
            }
        ),
        encoding="utf-8",
    )
    service = build_admin_runtime(write_config(tmp_path))

    default_document = service.workflow_config_admin.read_workflow_editor_document()
    pack_a_document = service.workflow_config_admin.read_workflow_editor_document(
        workflow_pack="pack-a.v1"
    )
    pack_b_document = service.workflow_config_admin.read_workflow_editor_document(
        workflow_pack="pack-b.v1"
    )
    composed_document = service.workflow_config_admin.read_composed_workflow_editor_document()

    assert default_document.document_scope == "composed"
    assert default_document.selected_workflow_pack is None
    assert [descriptor.reference for descriptor in default_document.workflow_packs] == [
        "pack-a.v1.json",
        "pack-b.v1.json",
    ]
    assert [trigger["id"] for trigger in default_document.config["triggers"]] == ["trigger-a"]
    assert [effect["id"] for effect in default_document.config["effects"]] == ["effect-b"]
    assert [graph.id for graph in default_document.graphs] == ["graph-a", "graph-b"]

    assert pack_a_document.selected_workflow_pack is not None
    assert pack_a_document.selected_workflow_pack.reference == "pack-a.v1.json"
    assert [trigger["id"] for trigger in pack_a_document.config["triggers"]] == ["trigger-a"]
    assert pack_a_document.config["effects"] == []
    assert [graph.id for graph in pack_a_document.graphs] == ["graph-a"]

    assert pack_b_document.selected_workflow_pack is not None
    assert pack_b_document.selected_workflow_pack.reference == "pack-b.v1.json"
    assert pack_b_document.config["triggers"] == []
    assert [effect["id"] for effect in pack_b_document.config["effects"]] == ["effect-b"]
    assert [graph.id for graph in pack_b_document.graphs] == ["graph-b"]
    assert pack_b_document.layout_path == str(packs_dir / "pack-b.v1.layout.json")
    assert pack_b_document.layout == {"nodes": {"effect-b": {"x": 220.0, "y": 160.0}}}

    assert composed_document.document_scope == "composed"
    assert [trigger["id"] for trigger in composed_document.config["triggers"]] == ["trigger-a"]
    assert [effect["id"] for effect in composed_document.config["effects"]] == ["effect-b"]
    assert [graph.id for graph in composed_document.graphs] == ["graph-a", "graph-b"]


def test_workflow_editor_save_updates_selected_pack_in_multi_pack_config(tmp_path: Path) -> None:
    packs_dir = tmp_path / "config" / "workflow-packs"
    packs_dir.mkdir(parents=True, exist_ok=True)
    pack_a_path = packs_dir / "pack-a.v1.json"
    pack_b_path = packs_dir / "pack-b.v1.json"
    pack_a_payload = {
        "pack_id": "pack-a",
        "pack_version": "v1",
        "ir": {
            "version": "v1",
            "nodes": [
                {
                    "id": "trigger-a",
                    "display_name": "Every minute",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "edges": [],
        },
    }
    pack_a_path.write_text(json.dumps(pack_a_payload), encoding="utf-8")
    pack_b_path.write_text(
        json.dumps(
            {
                "pack_id": "pack-b",
                "pack_version": "v1",
                "description": "Second pack",
                "graphs": [
                    {
                        "id": "graph-b",
                        "name": "Graph B",
                        "directory_path": "root/team-a",
                        "node_ids": ["effect-b"],
                    }
                ],
                "ir": {
                    "version": "v1",
                    "nodes": [
                        {
                            "id": "effect-b",
                            "display_name": "Emit event",
                            "type": "effect:event:emit",
                            "event_type": "workflow.synthetic",
                            "payload": {"message": "pack-b"},
                        }
                    ],
                    "edges": [],
                },
            }
        ),
        encoding="utf-8",
    )
    config_root = tmp_path / "config" / "trigger-orchestrator"
    config_root.mkdir(parents=True, exist_ok=True)
    config_path = config_root / "config.json"
    wrapper_payload = {
        "runtime_dir": str(tmp_path / "runtime"),
        "poll_interval_seconds": 9,
        "workflow_packs": ["pack-a.v1.json", "pack-b.v1.json"],
    }
    config_path.write_text(json.dumps(wrapper_payload), encoding="utf-8")
    service = build_admin_runtime(write_config(tmp_path))
    document = service.workflow_config_admin.read_workflow_editor_document(
        workflow_pack="pack-b.v1.json"
    )
    edited_config = dict(document.config)
    edited_effects = [dict(effect) for effect in edited_config["effects"]]
    edited_effects[0]["display_name"] = "Emit updated event"
    edited_config["effects"] = edited_effects
    edited_graphs = [graph.model_dump(mode="json") for graph in document.graphs]
    edited_graphs[0]["name"] = "Updated Graph B"

    save_result = service.workflow_config_admin.save_workflow_editor_document(
        config=edited_config,
        layout={"nodes": {"effect-b": {"x": 320, "y": 180}}},
        graphs=edited_graphs,
        workflow_pack="pack-b.v1",
        expected_revision=document.revision,
    )

    assert save_result.document_scope == "pack"
    assert save_result.selected_workflow_pack is not None
    assert save_result.selected_workflow_pack.reference == "pack-b.v1.json"
    assert json.loads(config_path.read_text(encoding="utf-8")) == wrapper_payload
    assert json.loads(pack_a_path.read_text(encoding="utf-8")) == pack_a_payload
    pack_b_payload = json.loads(pack_b_path.read_text(encoding="utf-8"))
    assert pack_b_payload["description"] == "Second pack"
    assert pack_b_payload["graphs"][0]["name"] == "Updated Graph B"
    assert pack_b_payload["ir"]["nodes"][0]["display_name"] == "Emit updated event"
    assert json.loads((packs_dir / "pack-b.v1.layout.json").read_text(encoding="utf-8")) == {
        "nodes": {"effect-b": {"x": 320.0, "y": 180.0}}
    }


def test_workflow_editor_create_workflow_pack_appends_blank_pack(tmp_path: Path) -> None:
    packs_dir = tmp_path / "config" / "workflow-packs"
    packs_dir.mkdir(parents=True, exist_ok=True)
    (packs_dir / "pack-a.v1.json").write_text(
        json.dumps(
            {
                "pack_id": "pack-a",
                "pack_version": "v1",
                "ir": {
                    "version": "v1",
                    "nodes": [
                        {
                            "id": "trigger-a",
                            "display_name": "Every minute",
                            "type": "trigger:cron",
                            "expression": "* * * * *",
                        }
                    ],
                    "edges": [],
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
            {"runtime_dir": str(tmp_path / "runtime"), "workflow_packs": ["pack-a.v1.json"]}
        ),
        encoding="utf-8",
    )
    service = build_admin_runtime(write_config(tmp_path)).workflow_config_admin
    revision = service.read_composed_workflow_editor_document().pack_list_revision

    result = service.create_workflow_pack(
        pack_id="custom-pack",
        pack_version="v1",
        description="Operator-owned workflow pack.",
        expected_revision=revision,
    )

    assert json.loads(config_path.read_text(encoding="utf-8"))["workflow_packs"] == [
        "pack-a.v1.json",
        "custom-pack.v1.json",
    ]
    pack_payload = json.loads((packs_dir / "custom-pack.v1.json").read_text(encoding="utf-8"))
    assert pack_payload == {
        "description": "Operator-owned workflow pack.",
        "graphs": [],
        "ir": {"edges": [], "nodes": [], "version": "v1"},
        "pack_id": "custom-pack",
        "pack_version": "v1",
    }
    assert result.selected_workflow_pack is not None
    assert result.selected_workflow_pack.reference == "custom-pack.v1.json"
    assert result.document.config["workflow_packs"] == [
        "pack-a.v1.json",
        "custom-pack.v1.json",
    ]


def test_workflow_editor_create_workflow_pack_rejects_duplicate_or_inline_config(
    tmp_path: Path,
) -> None:
    packs_dir = tmp_path / "config" / "workflow-packs"
    packs_dir.mkdir(parents=True, exist_ok=True)
    (packs_dir / "pack-a.v1.json").write_text(
        json.dumps(
            {
                "pack_id": "pack-a",
                "pack_version": "v1",
                "ir": {"version": "v1", "nodes": [], "edges": []},
            }
        ),
        encoding="utf-8",
    )
    config_root = tmp_path / "config" / "trigger-orchestrator"
    config_root.mkdir(parents=True, exist_ok=True)
    config_path = config_root / "config.json"
    config_path.write_text(
        json.dumps(
            {"runtime_dir": str(tmp_path / "runtime"), "workflow_packs": ["pack-a.v1.json"]}
        ),
        encoding="utf-8",
    )
    service = build_admin_runtime(write_config(tmp_path)).workflow_config_admin
    revision = service.read_composed_workflow_editor_document().pack_list_revision

    with pytest.raises(ValueError, match="already configured"):
        service.create_workflow_pack(
            pack_id="pack-a",
            pack_version="v1",
            expected_revision=revision,
        )

    config_path.write_text(
        json.dumps(
            {
                "runtime_dir": str(tmp_path / "runtime"),
                "triggers": [
                    {
                        "id": "trigger-inline",
                        "display_name": "Inline",
                        "type": "trigger:cron",
                        "expression": "* * * * *",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    service = build_admin_runtime(write_config(tmp_path)).workflow_config_admin
    revision = service.read_composed_workflow_editor_document().pack_list_revision
    with pytest.raises(ValueError, match="cannot convert inline workflow configs"):
        service.create_workflow_pack(
            pack_id="new-pack",
            pack_version="v1",
            expected_revision=revision,
        )


def test_workflow_editor_update_workflow_pack_metadata_preserves_reference_and_graph(
    tmp_path: Path,
) -> None:
    packs_dir = tmp_path / "config" / "workflow-packs"
    packs_dir.mkdir(parents=True, exist_ok=True)
    pack_path = packs_dir / "pack-a.v1.json"
    pack_path.write_text(
        json.dumps(
            {
                "pack_id": "pack-a",
                "pack_version": "v1",
                "description": "Original description.",
                "graphs": [
                    {
                        "id": "graph-a",
                        "name": "Graph A",
                        "directory_path": "root",
                        "node_ids": ["trigger-a"],
                    }
                ],
                "ir": {
                    "version": "v1",
                    "nodes": [
                        {
                            "id": "trigger-a",
                            "display_name": "Every minute",
                            "type": "trigger:cron",
                            "expression": "* * * * *",
                        }
                    ],
                    "edges": [],
                },
            }
        ),
        encoding="utf-8",
    )
    config_root = tmp_path / "config" / "trigger-orchestrator"
    config_root.mkdir(parents=True, exist_ok=True)
    config_path = config_root / "config.json"
    wrapper_payload = {
        "runtime_dir": str(tmp_path / "runtime"),
        "workflow_packs": ["pack-a.v1.json"],
    }
    config_path.write_text(json.dumps(wrapper_payload), encoding="utf-8")
    service = build_admin_runtime(write_config(tmp_path)).workflow_config_admin
    document = service.read_workflow_editor_document(workflow_pack="pack-a.v1.json")
    assert document.selected_workflow_pack is not None
    expected_revision = document.selected_workflow_pack.revision

    result = service.update_workflow_pack_metadata(
        workflow_pack="pack-a.v1.json",
        pack_id="renamed-pack",
        pack_version="v2",
        description="Updated description.",
        expected_revision=expected_revision,
    )

    assert json.loads(config_path.read_text(encoding="utf-8")) == wrapper_payload
    pack_payload = json.loads(pack_path.read_text(encoding="utf-8"))
    assert pack_payload["pack_id"] == "renamed-pack"
    assert pack_payload["pack_version"] == "v2"
    assert pack_payload["description"] == "Updated description."
    assert pack_payload["graphs"][0]["id"] == "graph-a"
    assert pack_payload["ir"]["nodes"][0]["id"] == "trigger-a"
    assert result.selected_workflow_pack is not None
    assert result.selected_workflow_pack.reference == "pack-a.v1.json"
    assert result.selected_workflow_pack.display_name == "renamed-pack v2"

    with pytest.raises(AdminRevisionConflict):
        service.update_workflow_pack_metadata(
            workflow_pack="pack-a.v1.json",
            pack_id="stale",
            pack_version="v3",
            expected_revision=expected_revision,
        )


def test_workflow_editor_remove_workflow_pack_unlists_without_deleting_files(
    tmp_path: Path,
) -> None:
    packs_dir = tmp_path / "config" / "workflow-packs"
    packs_dir.mkdir(parents=True, exist_ok=True)
    for pack_name in ("pack-a.v1.json", "pack-b.v1.json"):
        (packs_dir / pack_name).write_text(
            json.dumps(
                {
                    "pack_id": pack_name.removesuffix(".json"),
                    "pack_version": "v1",
                    "ir": {"version": "v1", "nodes": [], "edges": []},
                }
            ),
            encoding="utf-8",
        )
        (packs_dir / pack_name.replace(".json", ".layout.json")).write_text(
            json.dumps({"nodes": {}}),
            encoding="utf-8",
        )
    config_root = tmp_path / "config" / "trigger-orchestrator"
    config_root.mkdir(parents=True, exist_ok=True)
    config_path = config_root / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "runtime_dir": str(tmp_path / "runtime"),
                "workflow_packs": ["pack-a.v1.json", "pack-b.v1.json"],
            }
        ),
        encoding="utf-8",
    )
    service = build_admin_runtime(write_config(tmp_path)).workflow_config_admin
    revision = service.read_composed_workflow_editor_document().pack_list_revision

    result = service.remove_workflow_pack(
        workflow_pack="pack-a.v1.json",
        expected_revision=revision,
    )

    assert json.loads(config_path.read_text(encoding="utf-8"))["workflow_packs"] == [
        "pack-b.v1.json"
    ]
    assert (packs_dir / "pack-a.v1.json").exists()
    assert (packs_dir / "pack-a.v1.layout.json").exists()
    assert result.selected_workflow_pack is not None
    assert result.selected_workflow_pack.reference == "pack-b.v1.json"

    next_revision = result.document.pack_list_revision
    with pytest.raises(ValueError, match="last configured workflow pack"):
        service.remove_workflow_pack(
            workflow_pack="pack-b.v1.json",
            expected_revision=next_revision,
        )


def test_workflow_editor_remove_workflow_pack_tolerates_malformed_snapshot_files(
    tmp_path: Path,
) -> None:
    packs_dir = tmp_path / "config" / "workflow-packs"
    packs_dir.mkdir(parents=True, exist_ok=True)
    bad_pack_path = packs_dir / "bad-pack.v1.json"
    bad_layout_path = packs_dir / "bad-pack.v1.layout.json"
    bad_pack_path.write_text("{not-json", encoding="utf-8")
    bad_layout_path.write_text("{also-not-json", encoding="utf-8")
    (packs_dir / "good-pack.v1.json").write_text(
        json.dumps(
            {
                "pack_id": "good-pack",
                "pack_version": "v1",
                "ir": {"version": "v1", "nodes": [], "edges": []},
            }
        ),
        encoding="utf-8",
    )
    (packs_dir / "good-pack.v1.layout.json").write_text(
        json.dumps({"nodes": {}}),
        encoding="utf-8",
    )
    config_root = tmp_path / "config" / "trigger-orchestrator"
    config_root.mkdir(parents=True, exist_ok=True)
    config_path = config_root / "config.json"
    wrapper_payload = {
        "runtime_dir": str(tmp_path / "runtime"),
        "workflow_packs": ["bad-pack.v1.json", "good-pack.v1.json"],
    }
    config_path.write_text(json.dumps(wrapper_payload), encoding="utf-8")
    service = build_admin_runtime(write_config(tmp_path)).workflow_config_admin
    revision = service.stable_json_revision(wrapper_payload)

    result = service.remove_workflow_pack(
        workflow_pack="bad-pack.v1.json",
        expected_revision=revision,
    )

    assert json.loads(config_path.read_text(encoding="utf-8"))["workflow_packs"] == [
        "good-pack.v1.json"
    ]
    assert bad_pack_path.exists()
    assert bad_layout_path.exists()
    assert result.selected_workflow_pack is not None
    assert result.selected_workflow_pack.reference == "good-pack.v1.json"
    history = service.list_workflow_pack_history(workflow_pack="bad-pack.v1.json")
    assert [entry.operation for entry in history.entries] == ["remove"]
    detail = service.get_workflow_pack_history_detail(history.entries[0].change_id)
    before_pack = next(file for file in detail.before_files if file.label == "pack")
    before_layout = next(file for file in detail.before_files if file.label == "layout")
    assert before_pack.exists is True
    assert before_pack.content is None
    assert before_pack.error
    assert before_layout.exists is True
    assert before_layout.content is None
    assert before_layout.error


def test_workflow_pack_history_records_pack_management_changes(tmp_path: Path) -> None:
    packs_dir = tmp_path / "config" / "workflow-packs"
    packs_dir.mkdir(parents=True, exist_ok=True)
    pack_a_path = packs_dir / "pack-a.v1.json"
    pack_a_path.write_text(
        json.dumps(
            {
                "pack_id": "pack-a",
                "pack_version": "v1",
                "ir": {"version": "v1", "nodes": [], "edges": []},
            }
        ),
        encoding="utf-8",
    )
    (packs_dir / "pack-a.v1.layout.json").write_text(json.dumps({"nodes": {}}), encoding="utf-8")
    config_root = tmp_path / "config" / "trigger-orchestrator"
    config_root.mkdir(parents=True, exist_ok=True)
    config_path = config_root / "config.json"
    config_path.write_text(
        json.dumps(
            {"runtime_dir": str(tmp_path / "runtime"), "workflow_packs": ["pack-a.v1.json"]}
        ),
        encoding="utf-8",
    )
    service = build_admin_runtime(write_config(tmp_path)).workflow_config_admin
    actor = WorkflowPackChangeActor.from_values(
        login="Operator@Example.com",
        display_name="Operator",
    )

    create_revision = service.read_composed_workflow_editor_document().pack_list_revision
    create_result = service.create_workflow_pack(
        pack_id="custom-pack",
        pack_version="v1",
        expected_revision=create_revision,
        actor=actor,
    )
    created_pack_revision = create_result.selected_workflow_pack.revision
    update_result = service.update_workflow_pack_metadata(
        workflow_pack="custom-pack.v1.json",
        pack_id="renamed-pack",
        pack_version="v2",
        description="Updated",
        expected_revision=created_pack_revision,
        actor=actor,
    )
    remove_revision = update_result.document.pack_list_revision
    service.remove_workflow_pack(
        workflow_pack="pack-a.v1.json",
        expected_revision=remove_revision,
        actor=actor,
    )

    history = service.list_workflow_pack_history()
    assert [entry.operation for entry in history.entries] == [
        "remove",
        "update_metadata",
        "create",
    ]
    create_entry = history.entries[2]
    assert create_entry.workflow_pack == "custom-pack.v1.json"
    assert create_entry.actor.login == "operator@example.com"
    assert create_entry.actor.display_name == "Operator"
    assert create_entry.before_revision is None
    assert create_entry.after_revision == created_pack_revision
    assert create_entry.summary["workflow_packs_before"] == 1
    assert create_entry.summary["workflow_packs_after"] == 2

    detail = service.get_workflow_pack_history_detail(create_entry.change_id)
    before_pack = next(file for file in detail.before_files if file.label == "pack")
    after_pack = next(file for file in detail.after_files if file.label == "pack")
    assert before_pack.exists is False
    assert after_pack.exists is True
    assert after_pack.content is not None
    assert after_pack.content["pack_id"] == "custom-pack"
    pack_diff = next(diff for diff in detail.diffs if diff.label == "pack")
    assert pack_diff.changes[0]["change_type"] == "added"


def test_workflow_pack_history_records_pack_document_saves_and_skips_conflicts(
    tmp_path: Path,
) -> None:
    packs_dir = tmp_path / "config" / "workflow-packs"
    packs_dir.mkdir(parents=True, exist_ok=True)
    (packs_dir / "demo.v1.json").write_text(
        json.dumps(
            {
                "pack_id": "demo",
                "pack_version": "v1",
                "graphs": [],
                "ir": {"version": "v1", "nodes": [], "edges": []},
            }
        ),
        encoding="utf-8",
    )
    (packs_dir / "demo.v1.layout.json").write_text(json.dumps({"nodes": {}}), encoding="utf-8")
    config_root = tmp_path / "config" / "trigger-orchestrator"
    config_root.mkdir(parents=True, exist_ok=True)
    (config_root / "config.json").write_text(
        json.dumps({"runtime_dir": str(tmp_path / "runtime"), "workflow_packs": ["demo.v1.json"]}),
        encoding="utf-8",
    )
    service = build_admin_runtime(write_config(tmp_path)).workflow_config_admin
    document = service.read_workflow_editor_document(workflow_pack="demo.v1.json")
    actor = WorkflowPackChangeActor.from_values(login="operator", display_name="Operator")
    next_config = dict(document.config)
    next_config["effects"] = [
        {
            "id": "effect-a",
            "display_name": "Emit event",
            "type": "effect:event:emit",
            "event_type": "workflow.synthetic",
            "payload": {"message": "hello"},
        }
    ]

    save_result = service.save_workflow_editor_document(
        config=next_config,
        layout={"nodes": {"effect-a": {"x": 120, "y": 80}}},
        graphs=[
            {
                "id": "graph-a",
                "name": "Graph A",
                "directory_path": "root",
                "node_ids": ["effect-a"],
            }
        ],
        workflow_pack="demo.v1.json",
        expected_revision=document.revision,
        actor=actor,
    )

    history = service.list_workflow_pack_history(workflow_pack="demo.v1.json")
    assert len(history.entries) == 1
    entry = history.entries[0]
    assert entry.operation == "save"
    assert entry.before_revision == document.selected_workflow_pack.revision
    assert entry.after_revision == save_result.selected_workflow_pack.revision
    assert entry.summary["nodes_before"] == 0
    assert entry.summary["nodes_after"] == 1
    detail = service.get_workflow_pack_history_detail(entry.change_id)
    after_layout = next(file for file in detail.after_files if file.label == "layout")
    assert after_layout.content == {"nodes": {"effect-a": {"x": 120, "y": 80}}}

    with pytest.raises(AdminRevisionConflict):
        service.save_workflow_editor_document(
            config=next_config,
            layout={"nodes": {}},
            workflow_pack="demo.v1.json",
            expected_revision=document.revision,
            actor=actor,
        )
    assert len(service.list_workflow_pack_history(workflow_pack="demo.v1.json").entries) == 1


def test_workflow_pack_migration_raw_cas_rejects_stale_write_and_restores_exact_bytes(
    tmp_path: Path,
) -> None:
    packs_dir = tmp_path / "config" / "workflow-packs"
    packs_dir.mkdir(parents=True, exist_ok=True)
    pack_path = packs_dir / "legacy.v1.json"
    before = (
        b'{\n  "pack_id": "legacy", "pack_version": "v1",\n'
        b'  "ir": {"version": "v1", "nodes": [{"id": "clone", '
        b'"type": "effect:agent:clone-repo", "repo_source": "/tmp/source"}], '
        b'"edges": []}\n}\n'
    )
    pack_path.write_bytes(before)
    config_root = tmp_path / "config" / "trigger-orchestrator"
    config_root.mkdir(parents=True, exist_ok=True)
    (config_root / "config.json").write_text(
        json.dumps(
            {
                "runtime_dir": str(tmp_path / "runtime"),
                "workflow_packs": ["legacy.v1.json"],
            }
        ),
        encoding="utf-8",
    )
    service = build_admin_runtime(write_config(tmp_path)).workflow_config_admin
    document = service.read_workflow_pack_for_migration(workflow_pack="legacy.v1.json")
    after = json.dumps(
        {
            "pack_id": "legacy",
            "pack_version": "v1",
            "ir": {
                "version": "v1",
                "nodes": [
                    {
                        "id": "clone",
                        "type": "effect:agent:clone-repo",
                        "managed_repo_id": "mr_0123456789abcdef0123",
                    }
                ],
                "edges": [],
            },
        },
        indent=2,
        sort_keys=True,
    ).encode("utf-8")
    actor = WorkflowPackChangeActor(
        login="system",
        display_name="Managed Git migration",
    )

    applied = service.rewrite_workflow_pack_for_migration(
        workflow_pack="legacy.v1.json",
        content=after,
        expected_revision=document.revision,
        operation="managed_git_migration_apply",
        actor=actor,
    )
    assert pack_path.read_bytes() == after

    with pytest.raises(AdminRevisionConflict) as exc_info:
        service.rewrite_workflow_pack_for_migration(
            workflow_pack="legacy.v1.json",
            content=b"{}",
            expected_revision=document.revision,
            operation="managed_git_migration_apply",
            actor=actor,
        )
    assert exc_info.value.details["current_revision"] == applied.revision
    assert pack_path.read_bytes() == after

    rolled_back = service.rewrite_workflow_pack_for_migration(
        workflow_pack="legacy.v1.json",
        content=before,
        expected_revision=applied.revision,
        operation="managed_git_migration_rollback",
        actor=actor,
    )
    assert rolled_back.revision == document.revision
    assert pack_path.read_bytes() == before
    history = service.list_workflow_pack_history(workflow_pack="legacy.v1.json")
    assert [entry.operation for entry in history.entries] == [
        "managed_git_migration_rollback",
        "managed_git_migration_apply",
    ]


def test_workflow_editor_rejects_omitted_pack_save_in_multi_pack_config(
    tmp_path: Path,
) -> None:
    packs_dir = tmp_path / "config" / "workflow-packs"
    packs_dir.mkdir(parents=True, exist_ok=True)
    for pack_name, node_id, node_type in (
        ("pack-a.v1.json", "trigger-a", "trigger:cron"),
        ("pack-b.v1.json", "effect-b", "effect:event:emit"),
    ):
        node: dict[str, object] = {
            "id": node_id,
            "display_name": node_id,
            "type": node_type,
        }
        if node_type == "trigger:cron":
            node["expression"] = "* * * * *"
        else:
            node["event_type"] = "workflow.synthetic"
            node["payload"] = {"message": node_id}
        (packs_dir / pack_name).write_text(
            json.dumps(
                {
                    "pack_id": pack_name.removesuffix(".json"),
                    "pack_version": "v1",
                    "ir": {"version": "v1", "nodes": [node], "edges": []},
                }
            ),
            encoding="utf-8",
        )
    config_root = tmp_path / "config" / "trigger-orchestrator"
    config_root.mkdir(parents=True, exist_ok=True)
    (config_root / "config.json").write_text(
        json.dumps(
            {
                "runtime_dir": str(tmp_path / "runtime"),
                "workflow_packs": ["pack-a.v1.json", "pack-b.v1.json"],
            }
        ),
        encoding="utf-8",
    )
    service = build_admin_runtime(write_config(tmp_path))
    document = service.workflow_config_admin.read_workflow_editor_document()

    with pytest.raises(ValueError, match="requires workflow_pack"):
        service.workflow_config_admin.save_workflow_editor_document(
            config=document.config,
            layout=document.layout,
            graphs=[graph.model_dump(mode="json") for graph in document.graphs],
        )


def test_workflow_editor_validate_selected_pack_checks_sibling_pack_ids(
    tmp_path: Path,
) -> None:
    packs_dir = tmp_path / "config" / "workflow-packs"
    packs_dir.mkdir(parents=True, exist_ok=True)
    (packs_dir / "pack-a.v1.json").write_text(
        json.dumps(
            {
                "pack_id": "pack-a",
                "pack_version": "v1",
                "ir": {
                    "version": "v1",
                    "nodes": [
                        {
                            "id": "shared-id",
                            "display_name": "Every minute",
                            "type": "trigger:cron",
                            "expression": "* * * * *",
                        }
                    ],
                    "edges": [],
                },
            }
        ),
        encoding="utf-8",
    )
    (packs_dir / "pack-b.v1.json").write_text(
        json.dumps(
            {
                "pack_id": "pack-b",
                "pack_version": "v1",
                "ir": {
                    "version": "v1",
                    "nodes": [
                        {
                            "id": "effect-b",
                            "display_name": "Emit event",
                            "type": "effect:event:emit",
                            "event_type": "workflow.synthetic",
                            "payload": {"message": "pack-b"},
                        }
                    ],
                    "edges": [],
                },
            }
        ),
        encoding="utf-8",
    )
    config_root = tmp_path / "config" / "trigger-orchestrator"
    config_root.mkdir(parents=True, exist_ok=True)
    (config_root / "config.json").write_text(
        json.dumps(
            {
                "runtime_dir": str(tmp_path / "runtime"),
                "workflow_packs": ["pack-a.v1.json", "pack-b.v1.json"],
            }
        ),
        encoding="utf-8",
    )
    service = build_admin_runtime(write_config(tmp_path))
    document = service.workflow_config_admin.read_workflow_editor_document(
        workflow_pack="pack-b.v1.json"
    )
    edited_config = dict(document.config)
    edited_effects = [dict(effect) for effect in edited_config["effects"]]
    edited_effects[0]["id"] = "shared-id"
    edited_config["effects"] = edited_effects
    edited_graphs = [graph.model_dump(mode="json") for graph in document.graphs]
    edited_graphs[0]["node_ids"] = ["shared-id"]

    with pytest.raises(ValueError, match="Node IDs must be globally unique"):
        service.workflow_config_admin.validate_workflow_editor_document(
            config=edited_config,
            layout=document.layout,
            graphs=edited_graphs,
            workflow_pack="pack-b.v1.json",
        )


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
                        "directory_path": "root",
                        "user_ids": ["alice"],
                        "node_ids": ["trigger-ticket", "effect-ticket"],
                    },
                    {
                        "id": "graph-cron",
                        "name": "Scheduled cleanup",
                        "directory_path": "root/team-a",
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
                            "type": "effect:event:emit",
                            "event_type": "workflow.synthetic",
                            "payload": {"message": "cleanup"},
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
    service = build_admin_runtime(write_config(tmp_path))

    document = service.workflow_config_admin.read_workflow_editor_document()

    assert [
        (graph.id, graph.name, graph.directory_path, graph.user_ids, graph.node_ids)
        for graph in document.graphs
    ] == [
        ("graph-ticket", "Ticket intake", "root", ["alice"], ["trigger-ticket", "effect-ticket"]),
        (
            "graph-cron",
            "Scheduled cleanup",
            "root/team-a",
            ["bob"],
            ["trigger-cron", "effect-cron"],
        ),
    ]

    edited_graphs = [graph.model_dump(mode="json") for graph in document.graphs]
    edited_graphs[0]["name"] = "Root ticket intake"
    edited_graphs[0]["description"] = "Visible root-directory graph edit."
    save_result = service.workflow_config_admin.save_workflow_editor_document(
        config=document.config,
        layout=document.layout,
        graphs=edited_graphs,
    )

    assert save_result.graphs[0].name == "Root ticket intake"
    wrapper_config = json.loads(config_path.read_text(encoding="utf-8"))
    assert wrapper_config["workflow_packs"] == ["demo.v1.json"]
    assert "workflow_graphs" not in wrapper_config
    pack_payload = json.loads(pack_path.read_text(encoding="utf-8"))
    assert pack_payload["graphs"][0]["id"] == "graph-ticket"
    assert pack_payload["graphs"][0]["name"] == "Root ticket intake"
    assert pack_payload["graphs"][0]["description"] == "Visible root-directory graph edit."
    assert pack_payload["graphs"][0]["directory_path"] == "root"
    assert pack_payload["graphs"][0]["directory_paths"] == ["root"]
    assert pack_payload["graphs"][0]["user_ids"] == ["alice"]
    assert pack_payload["graphs"][0]["node_ids"] == ["trigger-ticket", "effect-ticket"]
    assert pack_payload["graphs"][1]["id"] == "graph-cron"
    assert pack_payload["graphs"][1]["name"] == "Scheduled cleanup"
    assert pack_payload["graphs"][1]["directory_path"] == "root/team-a"
    assert pack_payload["graphs"][1]["user_ids"] == ["bob"]
    assert pack_payload["graphs"][1]["node_ids"] == ["trigger-cron", "effect-cron"]


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
                        "directory_path": "root",
                        "user_ids": ["alice"],
                        "node_ids": ["trigger-ticket", "gate-filter", "effect-ticket"],
                    },
                    {
                        "id": "graph-cron",
                        "name": "Scheduled cleanup",
                        "directory_path": "root",
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
                            "type": "effect:event:emit",
                            "event_type": "workflow.synthetic",
                            "payload": {"message": "cleanup"},
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
    service = build_admin_runtime(write_config(tmp_path))

    report = service.workflow_reports_admin.read_workflow_graph_activity_report(
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
                        "directory_path": "root",
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
    service = build_admin_runtime(write_config(tmp_path))

    report = service.workflow_reports_admin.read_workflow_graph_activity_report(
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
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert {"trigger_events", "effect_executions", "gate_executions"} <= table_names


def test_normalize_workflow_failure_signature_canonicalizes_volatile_identifiers() -> None:
    outputs = {
        "error_class": "RuntimeError",
        "source_kind": "effect",
        "node_type": "effect:git:push-branch",
    }
    error_a = (
        "Rejected branch update for refs/heads/agents/session-123/update-branch-host-a "
        "at 2026-05-18T11:00:00Z; session_id=11111111-1111-1111-1111-111111111111; "
        "trace_id=22222222-2222-2222-2222-222222222222; thread_id=33333333-3333-3333-3333-333333333333; "
        "execution_id=44444444-4444-4444-4444-444444444444; "
        "request_id=55555555-5555-5555-5555-555555555555; "
        "artifact_url=https://example.com/api/run?session=11111111-1111-1111-1111-111111111111&trace=22222222-2222-2222-2222-222222222222; "
        "hash=abcdef1234567890abcdef1234567890; path=/tmp/workflow-run-a?token=secret"
    )
    error_b = (
        "Rejected branch update for refs/heads/agents/session-987/update-branch-host-b "
        "at 2026-06-12T05:06:07Z; session_id=66666666-6666-6666-6666-666666666666; "
        "trace_id=77777777-7777-7777-7777-777777777777; thread_id=88888888-8888-8888-8888-888888888888; "
        "execution_id=99999999-9999-9999-9999-999999999999; "
        "request_id=aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa; "
        "artifact_url=https://example.com/api/run?session=66666666-6666-6666-6666-666666666666&trace=77777777-7777-7777-7777-777777777777; "
        "hash=fedcba0987654321fedcba0987654321; path=/tmp/workflow-run-b?token=secret"
    )

    summary_a, key_a = admin_service._normalize_workflow_failure_signature(error_a, outputs)
    summary_b, key_b = admin_service._normalize_workflow_failure_signature(error_b, outputs)

    assert key_a == key_b
    assert summary_a == summary_b
    assert "<agent_ref>" in summary_a
    assert "<timestamp>" in summary_a
    assert "<session_id>" in summary_a
    assert "<trace_id>" in summary_a
    assert "<thread_id>" in summary_a
    assert "<execution_id>" in summary_a
    assert "<request_id>" in summary_a
    assert "<temp_path>" in summary_a
    assert "<hex>" in summary_a
    assert "https://example.com/api/run" in summary_a
    assert "session=" not in summary_a
    assert "trace=" not in summary_a


def test_normalize_workflow_failure_signature_keeps_distinct_errors_separate() -> None:
    outputs = {
        "error_class": "RuntimeError",
        "source_kind": "effect",
        "node_type": "effect:git:push-branch",
    }
    rejected = (
        "Rejected branch update for refs/heads/agents/session-123/update-branch-host-a "
        "because the remote branch already exists"
    )
    existing = "Existing branch refs/heads/agents/session-987/update-branch-host-b already exists"

    rejected_summary, rejected_key = admin_service._normalize_workflow_failure_signature(
        rejected, outputs
    )
    existing_summary, existing_key = admin_service._normalize_workflow_failure_signature(
        existing, outputs
    )

    assert rejected_key != existing_key
    assert rejected_summary != existing_summary


def test_normalize_workflow_failure_signature_falls_back_to_class_and_source() -> None:
    summary, key = admin_service._normalize_workflow_failure_signature(
        "   ",
        {
            "error_class": "RuntimeError",
            "source_kind": "effect",
            "node_type": "effect:git:push-branch",
        },
    )

    assert summary == "RuntimeError effect"
    assert key == "runtimeerror effect"


def test_read_workflow_failure_clusters_report_clusters_volatile_sqlite_records_and_orders_examples_newest_first(
    tmp_path: Path,
) -> None:
    write_failure_cluster_config(tmp_path)
    service = build_admin_runtime(write_config(tmp_path))
    db_path = tmp_path / "runtime" / "orchestration.db"
    queue_store = SqliteTriggerEventQueue(db_path)
    effect_store = SqliteExecutionStore(db_path)

    trigger_failure = TriggerEvent(
        event_id="evt-trigger-root",
        trigger_id="orphan-trigger",
        trigger_type="trigger:ticket:created",
        trace_id="trace-trigger-root",
        occurred_at=datetime(2026, 6, 12, 9, 0, tzinfo=UTC),
        payload={
            "session_id": "session-root",
            "pr_id": "pr-root",
            "issue_id": "issue-root",
            "ado_item_id": "ado-root",
            "artifact_id": "comm_00000000000000000000000000000001",
        },
    )
    queue_store.publish(trigger_failure, idempotency_key="evt-trigger-root")
    queue_store.mark_event_cleared(
        "evt-trigger-root",
        cleared_at=datetime(2026, 6, 12, 9, 5, tzinfo=UTC),
        clear_reason="Rejected branch update for refs/heads/agents/session-root/update-branch-host-root because the remote branch already exists",
        error="Rejected branch update for refs/heads/agents/session-root/update-branch-host-root because the remote branch already exists",
    )

    error_a = (
        "Rejected branch update for refs/heads/agents/session-123/update-branch-host-a "
        "at 2026-06-12T10:00:00Z; session_id=11111111-1111-1111-1111-111111111111; "
        "trace_id=22222222-2222-2222-2222-222222222222; thread_id=33333333-3333-3333-3333-333333333333; "
        "execution_id=44444444-4444-4444-4444-444444444444; request_id=55555555-5555-5555-5555-555555555555; "
        "artifact_url=https://example.com/api/run?session=11111111-1111-1111-1111-111111111111&trace=22222222-2222-2222-2222-222222222222"
    )
    error_b = (
        "Rejected branch update for refs/heads/agents/session-987/update-branch-host-b "
        "at 2026-06-12T11:00:00Z; session_id=66666666-6666-6666-6666-666666666666; "
        "trace_id=77777777-7777-7777-7777-777777777777; thread_id=88888888-8888-8888-8888-888888888888; "
        "execution_id=99999999-9999-9999-9999-999999999999; request_id=aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa; "
        "artifact_url=https://example.com/api/run?session=66666666-6666-6666-6666-666666666666&trace=77777777-7777-7777-7777-777777777777"
    )
    effect_store.record_execution(
        EffectExecutionRecord(
            execution_id="effect-1",
            idempotency_key="evt-effect-1::effect-push-branch",
            event_id="evt-effect-1",
            trace_id="trace-effect-1",
            source_node_id="trigger-ticket",
            source_trigger_type="trigger:ticket:created",
            effect_id="effect-push-branch",
            effect_type="effect:git:push-branch",
            status="failed",
            started_at=datetime(2026, 6, 12, 10, 0, tzinfo=UTC),
            finished_at=datetime(2026, 6, 12, 10, 1, tzinfo=UTC),
            resolved_config_hash="hash-effect-1",
            resolved_config={
                "session_id": "session-a",
                "pr_id": "pr-101",
            },
            input_payload={
                "issue_id": "issue-a",
                "work_item_id": "work-789",
            },
            outputs={
                "message": error_a,
                "artifact_id": "comm_00000000000000000000000000000002",
            },
            dismissed_at=None,
        )
    )
    effect_store.record_execution(
        EffectExecutionRecord(
            execution_id="effect-2",
            idempotency_key="evt-effect-2::effect-push-branch",
            event_id="evt-effect-2",
            trace_id="trace-effect-2",
            source_node_id="trigger-ticket",
            source_trigger_type="trigger:ticket:created",
            effect_id="effect-push-branch",
            effect_type="effect:git:push-branch",
            status="error_routed",
            started_at=datetime(2026, 6, 12, 11, 0, tzinfo=UTC),
            finished_at=datetime(2026, 6, 12, 11, 1, tzinfo=UTC),
            resolved_config_hash="hash-effect-2",
            resolved_config={
                "session_id": "session-b",
                "pr_id": "pr-102",
            },
            input_payload={
                "issue_id": "issue-b",
            },
            outputs={
                "error_message": error_b,
                "artifact_id": "comm_00000000000000000000000000000003",
            },
            inputs_by_source={"trigger-ticket": {"ado_item_id": "ado-556"}},
            dismissed_at=None,
        )
    )
    effect_store.record_execution(
        EffectExecutionRecord(
            execution_id="effect-success",
            idempotency_key="evt-effect-success::effect-push-branch",
            event_id="evt-effect-success",
            trace_id="trace-effect-success",
            source_node_id="trigger-ticket",
            source_trigger_type="trigger:ticket:created",
            effect_id="effect-push-branch",
            effect_type="effect:git:push-branch",
            status="succeeded",
            started_at=datetime(2026, 6, 12, 12, 0, tzinfo=UTC),
            finished_at=datetime(2026, 6, 12, 12, 1, tzinfo=UTC),
            resolved_config_hash="hash-effect-success",
            resolved_config={"session_id": "session-b"},
            input_payload={},
            outputs={"message": "completed successfully"},
            dismissed_at=None,
        )
    )
    effect_store.record_execution(
        EffectExecutionRecord(
            execution_id="effect-3",
            idempotency_key="evt-effect-3::effect-push-branch",
            event_id="evt-effect-3",
            trace_id="trace-effect-3",
            source_node_id="trigger-ticket",
            source_trigger_type="trigger:ticket:created",
            effect_id="effect-push-branch",
            effect_type="effect:git:push-branch",
            status="failed",
            started_at=datetime(2026, 6, 12, 12, 30, tzinfo=UTC),
            finished_at=datetime(2026, 6, 12, 12, 31, tzinfo=UTC),
            resolved_config_hash="hash-effect-3",
            resolved_config={"session_id": "session-c", "pr_id": "pr-103"},
            input_payload={"issue_id": "issue-c"},
            outputs={
                "message": "Existing branch refs/heads/agents/session-987/update-branch-host-b already exists",
                "artifact_id": "comm_00000000000000000000000000000004",
            },
            dismissed_at=None,
        )
    )

    report = service.workflow_reports_admin.read_workflow_failure_clusters_report(
        period_start=datetime(2026, 6, 12, 8, 0, tzinfo=UTC),
        period_end=datetime(2026, 6, 12, 13, 0, tzinfo=UTC),
        limit=10,
        recent_threshold_seconds=7200,
    )

    assert report.limit == 10
    assert report.recent_threshold_seconds == 7200
    assert report.has_more is False
    assert report.clusters and len(report.clusters) == 3

    resolved = next(cluster for cluster in report.clusters if cluster.count == 2)
    active = next(cluster for cluster in report.clusters if cluster.event_ids == ["evt-effect-3"])
    historical = next(
        cluster for cluster in report.clusters if cluster.event_ids == ["evt-trigger-root"]
    )

    assert resolved.graph_id == "graph-ticket"
    assert resolved.graph_name == "Ticket intake"
    assert resolved.node_id == "effect-push-branch"
    assert resolved.node_type == "effect:git:push-branch"
    assert resolved.effect_type == "effect:git:push-branch"
    assert resolved.count == 2
    assert resolved.event_ids == ["evt-effect-2", "evt-effect-1"]
    assert resolved.trace_ids == ["trace-effect-2", "trace-effect-1"]
    assert resolved.session_ids[0] == "session-b"
    assert set(resolved.session_ids) >= {"session-a", "session-b"}
    assert set(resolved.pr_ids) >= {"pr-101", "pr-102"}
    assert set(resolved.ado_item_ids) >= {"ado-556", "work-789"}
    assert set(resolved.issue_ids) >= {"issue-a", "issue-b"}
    assert set(resolved.artifact_ids) >= {
        "comm_00000000000000000000000000000002",
        "comm_00000000000000000000000000000003",
    }
    assert "<agent_ref>" in resolved.error_signature
    assert resolved.latest_error_summary == resolved.error_signature
    assert resolved.examples[0].event_id == "evt-effect-2"
    assert resolved.examples[1].event_id == "evt-effect-1"
    assert resolved.examples[0].run_detail_href == "#workflow?event_id=evt-effect-2"
    assert resolved.examples[0].api_run_detail_href == (
        "/api/workflow-editor/run-detail?event_id=evt-effect-2"
    )

    assert active.graph_id == "graph-ticket"
    assert active.state == "active"
    assert active.count == 1
    assert active.event_ids == ["evt-effect-3"]
    assert active.latest_error_summary.startswith("Existing branch")
    assert active.examples[0].event_id == "evt-effect-3"

    assert historical.graph_id == "root"
    assert historical.graph_name == "Unassigned"
    assert historical.state == "historical"
    assert historical.source_kind == "trigger"
    assert historical.trigger_type == "trigger:ticket:created"
    assert historical.count == 1
    assert historical.event_ids == ["evt-trigger-root"]
    assert historical.latest_error_summary.startswith("Rejected branch update")
    assert historical.examples[0].event_id == "evt-trigger-root"

    empty_period = service.workflow_reports_admin.read_workflow_failure_clusters_report(
        period_start=datetime(2026, 6, 13, 0, 0, tzinfo=UTC),
        period_end=datetime(2026, 6, 13, 1, 0, tzinfo=UTC),
        limit=10,
    )
    assert empty_period.clusters == []
    assert empty_period.has_more is False


def test_read_workflow_failure_clusters_report_uses_json_fallback_without_initializing_missing_files(
    tmp_path: Path,
) -> None:
    write_config(tmp_path)
    event_queue_store_path = tmp_path / "runtime" / "trigger-orchestrator-events.json"
    effect_execution_store_path = tmp_path / "runtime" / "effect-orchestrator-executions.json"
    gate_execution_store_path = tmp_path / "runtime" / "gate-orchestrator-executions.json"
    write_failure_cluster_config(
        tmp_path,
        orchestration_store_backend="json",
        event_queue_store_path=event_queue_store_path,
        effect_execution_store_path=effect_execution_store_path,
        gate_execution_store_path=gate_execution_store_path,
    )
    service = build_admin_runtime(write_config(tmp_path))

    report = service.workflow_reports_admin.read_workflow_failure_clusters_report(
        period_start=datetime(2026, 6, 12, 8, 0, tzinfo=UTC),
        period_end=datetime(2026, 6, 12, 13, 0, tzinfo=UTC),
        limit=10,
    )

    assert report.clusters == []
    assert event_queue_store_path.exists() is False
    assert effect_execution_store_path.exists() is False
    assert gate_execution_store_path.exists() is False


def test_workflow_failure_cluster_payload_models_validate_representative_json() -> None:
    report = admin_service.WorkflowFailureClusterReportPayload.model_validate(
        {
            "generated_at": "2026-06-12T12:00:00Z",
            "period_start": "2026-06-11T12:00:00Z",
            "period_end": "2026-06-12T12:00:00Z",
            "recent_threshold_seconds": 7200,
            "limit": 25,
            "clusters": [
                {
                    "cluster_id": "cluster-1",
                    "graph_id": "graph-ticket",
                    "graph_name": "Ticket intake",
                    "graph_description": "Handles ticket intake.",
                    "directory_path": "root",
                    "node_id": "effect-ticket",
                    "node_display_name": "Prompt ticket",
                    "node_type": "effect:agent:prompt",
                    "effect_type": "effect:agent:prompt",
                    "source_kind": "effect",
                    "error_signature": "Rejected branch update for <agent_ref>",
                    "error_signature_key": "rejected branch update for <agent_ref>",
                    "count": 2,
                    "first_seen_at": "2026-06-12T10:00:00Z",
                    "last_seen_at": "2026-06-12T11:00:00Z",
                    "latest_error_summary": "Rejected branch update for refs/heads/agents/session-987/update-branch-host-b",
                    "state": "recent",
                    "event_ids": ["evt-2", "evt-1"],
                    "trace_ids": ["trace-1"],
                    "session_ids": ["session-1"],
                    "pr_ids": ["123"],
                    "ado_item_ids": ["456"],
                    "issue_ids": ["root::local::1"],
                    "examples": [
                        {
                            "event_id": "evt-2",
                            "trace_id": "trace-2",
                            "session_id": "session-1",
                            "occurred_at": "2026-06-12T11:00:00Z",
                            "run_state": "active",
                            "error_summary": "Rejected branch update for refs/heads/agents/session-987/update-branch-host-b",
                            "run_detail_href": "#workflow?event_id=evt-2",
                            "api_run_detail_href": "/api/workflow-editor/run-detail?event_id=evt-2",
                        }
                    ],
                }
            ],
        }
    )

    cluster = report.clusters[0]
    example = cluster.examples[0]

    assert report.worker_id == "trigger-orchestrator"
    assert cluster.source_kind == "effect"
    assert cluster.state == "recent"
    assert cluster.event_ids == ["evt-2", "evt-1"]
    assert cluster.artifact_ids == []
    assert example.run_detail_href == "#workflow?event_id=evt-2"
    assert example.api_run_detail_href == "/api/workflow-editor/run-detail?event_id=evt-2"
    assert example.artifact_ids == []
    assert report.model_dump(mode="json")["clusters"][0]["examples"][0]["run_detail_href"] == (
        "#workflow?event_id=evt-2"
    )


def test_admin_service_read_workflow_run_detail_from_sqlite_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
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
    ContainerWorkerExecutionRepository(runtime_dir / "container-worker-executions.json").put(
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
        expires_at=datetime(2099, 6, 23, 11, 0, tzinfo=UTC),
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
            input_occurred_at_by_source={"effect-prompt": datetime(2026, 5, 3, 11, 0, tzinfo=UTC)},
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
                    "directory_path": "root",
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

    detail = service.workflow_config_admin.read_workflow_run_detail(event_id="evt-sqlite")
    detail_by_trace = service.workflow_config_admin.read_workflow_run_detail(
        trace_id="trace-sqlite"
    )

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


def test_admin_service_read_workflow_run_detail_preserves_approval_trigger_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    runtime_dir = tmp_path / "runtime"
    db_path = runtime_dir / "orchestration.db"
    approval_request = ApprovalRequest(
        request_id="approval-pending",
        session_id="session-approval",
        action_type="email_send_approved",
        tool_name="email_send_approved",
        payload_digest="digest-approval",
        human_summary="approve the pending email",
        payload={"body": "sqlite approval run detail"},
        directory_path="root",
        status=ApprovalStatus.PENDING,
        created_at=datetime(2026, 5, 18, 12, 0, tzinfo=UTC),
        expires_at=datetime(2026, 5, 19, 12, 0, tzinfo=UTC),
    )
    service.runtime.approvals.put(approval_request.request_id, approval_request)
    request_payload = approval_request.model_dump(mode="json")
    queue_store = SqliteTriggerEventQueue(db_path)
    queue_store.publish(
        TriggerEvent(
            event_id="evt-approval",
            trigger_id="approval-trigger",
            trigger_type="trigger:approval:pending-created",
            occurred_at=approval_request.created_at,
            payload={
                "approval_request_id": request_payload["request_id"],
                "session_id": request_payload["session_id"],
                "directory_path": request_payload["directory_path"],
                "action_type": request_payload["action_type"],
                "tool_name": request_payload["tool_name"],
                "human_summary": request_payload["human_summary"],
                "payload_digest": request_payload["payload_digest"],
                "payload": request_payload["payload"],
                "created_at": request_payload["created_at"],
                "expires_at": request_payload["expires_at"],
                "approval_request": request_payload,
            },
            trace_id="trace-approval",
        )
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

    detail = service.workflow_config_admin.read_workflow_run_detail(event_id="evt-approval")

    assert detail.event_id == "evt-approval"
    assert detail.trace_id == "trace-approval"
    assert detail.trigger_event is not None
    assert detail.trigger_event.trigger_type == "trigger:approval:pending-created"
    assert detail.trigger_event.payload["approval_request_id"] == "approval-pending"
    assert detail.trigger_event.payload["approval_request"]["request_id"] == "approval-pending"
    assert detail.trigger_event.payload["approval_request"]["human_summary"] == (
        "approve the pending email"
    )


def test_admin_service_keeps_completion_pending_workflow_runs_visible_and_clearable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
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
        "await_agent_store_path": runtime_dir
        / "providers"
        / "gates"
        / "await-agent"
        / "pending.json",
        "legacy_await_agent_store_path": runtime_dir / "await-agent-pending.json",
    }
    workflow_admin = service.workflow_reports_admin
    monkeypatch.setattr(workflow_admin, "_workflow_run_store_paths", lambda: stores)
    monkeypatch.setattr(
        workflow_admin,
        "_read_composed_workflow_editor_document",
        lambda: SimpleNamespace(graphs=[]),
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

    snapshot = service.workflow_config_admin.read_workflow_run_snapshot()
    assert len(snapshot.runs) == 1
    snapshot_run = snapshot.runs[0]
    assert snapshot_run.run_state == "active"
    assert {node.node_id for node in snapshot_run.active_nodes} == {"effect-1", "gate-1"}
    assert {node.status for node in snapshot_run.active_nodes} == {"completion_pending"}

    search = service.workflow_config_admin.read_workflow_run_search(node_id="effect-1")
    assert len(search.runs) == 1
    search_run = search.runs[0]
    assert search_run.run_state == "active"
    assert search_run.status_counts == {"completion_pending": 2}

    clear = service.workflow_config_admin.clear_workflow_run("evt-1")
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


def test_admin_service_read_workflow_run_search_uses_sqlite_event_slice(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    workflow_admin = service.workflow_reports_admin
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    stores = {
        "runtime_dir": runtime_dir,
        "orchestration_store_backend": "sqlite",
        "orchestration_store_path": runtime_dir / "orchestration.db",
        "orchestration_retention_days": 365,
        "event_queue_store_path": runtime_dir / "trigger-orchestrator-events.json",
        "effect_execution_store_path": runtime_dir / "effect-orchestrator-executions.json",
        "gate_execution_store_path": runtime_dir / "gate-orchestrator-executions.json",
        "workflow_config_snapshot_store_path": runtime_dir / "workflow-config-snapshots.json",
        "await_agent_store_path": runtime_dir
        / "providers"
        / "gates"
        / "await-agent"
        / "pending.json",
        "legacy_await_agent_store_path": runtime_dir / "await-agent-pending.json",
    }
    monkeypatch.setattr(workflow_admin, "_workflow_run_store_paths", lambda: stores)
    monkeypatch.setattr(
        workflow_admin,
        "_read_composed_workflow_editor_document",
        lambda: SimpleNamespace(graphs=[]),
    )

    queue_store = SqliteTriggerEventQueue(stores["orchestration_store_path"])
    effect_store = SqliteExecutionStore(stores["orchestration_store_path"])
    gate_store = SqliteGateExecutionStore(stores["orchestration_store_path"])
    older_at = datetime(2026, 5, 19, 10, 0, tzinfo=UTC)
    newer_at = datetime(2026, 5, 20, 10, 0, tzinfo=UTC)
    for event_id, occurred_at in (
        ("evt-old", older_at),
        ("evt-new", newer_at),
        ("evt-other", newer_at + timedelta(hours=1)),
    ):
        queue_store.publish(
            TriggerEvent(
                event_id=event_id,
                trigger_id="trigger-search",
                trigger_type="trigger:test",
                occurred_at=occurred_at,
                payload={},
                trace_id=f"trace-{event_id}",
            )
        )
        queue_store.mark_delivered(event_id, delivered_at=occurred_at + timedelta(seconds=5))

    for event_id, started_at, effect_id in (
        ("evt-old", older_at + timedelta(minutes=1), "effect-search"),
        ("evt-new", newer_at + timedelta(minutes=1), "effect-search"),
        ("evt-other", newer_at + timedelta(hours=1, minutes=1), "effect-other"),
    ):
        effect_store.record_execution(
            EffectExecutionRecord(
                execution_id=f"exec-{event_id}",
                idempotency_key=f"{event_id}::{effect_id}",
                event_id=event_id,
                trace_id=f"trace-{event_id}",
                source_node_id="trigger-search",
                source_trigger_type="trigger:test",
                effect_id=effect_id,
                effect_type="effect:test",
                status="succeeded",
                started_at=started_at,
                finished_at=started_at + timedelta(seconds=1),
                resolved_config_hash=f"hash-{event_id}",
                resolved_config={},
                input_payload={},
                outputs={},
            )
        )
    gate_store.record_execution(
        GateExecutionRecord(
            execution_id="gate-evt-new",
            idempotency_key="evt-new::gate-search",
            event_id="evt-new",
            trace_id="trace-evt-new",
            source_node_id="effect-search",
            source_trigger_type="trigger:test",
            gate_id="gate-search",
            gate_type="gate:test",
            status="succeeded",
            started_at=newer_at + timedelta(minutes=2),
            finished_at=newer_at + timedelta(minutes=2, seconds=1),
            resolved_config_hash="hash-gate-new",
            resolved_config={},
            input_payload={},
            outputs={},
            inputs_by_source={},
            input_event_ids_by_source={},
            input_occurred_at_by_source={},
        )
    )

    monkeypatch.setattr(
        SqliteTriggerEventQueue,
        "read_all",
        lambda self: pytest.fail("run search should not scan every trigger event"),
    )
    monkeypatch.setattr(
        SqliteExecutionStore,
        "read_all",
        lambda self: pytest.fail("run search should not scan every effect execution"),
    )
    monkeypatch.setattr(
        SqliteGateExecutionStore,
        "read_all",
        lambda self: pytest.fail("run search should not scan every gate execution"),
    )

    search = service.workflow_config_admin.read_workflow_run_search(
        node_id="effect-search", limit=1
    )

    assert search.matched == 2
    assert search.returned == 1
    assert search.has_more is True
    assert [run.event_id for run in search.runs] == ["evt-new"]
    assert search.runs[0].matched_node_ids == ["effect-search"]
    assert search.runs[0].node_ids == ["effect-search", "gate-search", "trigger-search"]
    assert search.runs[0].status_counts == {"delivered": 1, "succeeded": 2}

    active_only_search = service.workflow_config_admin.read_workflow_run_search(
        node_id="effect-search", limit=1, include_completed=False
    )
    assert active_only_search.matched == 0
    assert active_only_search.runs == []


def test_admin_service_read_workflow_run_search_without_session_reaches_past_sqlite_slice(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    workflow_admin = service.workflow_reports_admin
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    stores = {
        "runtime_dir": runtime_dir,
        "orchestration_store_backend": "sqlite",
        "orchestration_store_path": runtime_dir / "orchestration.db",
        "orchestration_retention_days": 365,
        "event_queue_store_path": runtime_dir / "trigger-orchestrator-events.json",
        "effect_execution_store_path": runtime_dir / "effect-orchestrator-executions.json",
        "gate_execution_store_path": runtime_dir / "gate-orchestrator-executions.json",
        "workflow_config_snapshot_store_path": runtime_dir / "workflow-config-snapshots.json",
        "await_agent_store_path": runtime_dir
        / "providers"
        / "gates"
        / "await-agent"
        / "pending.json",
        "legacy_await_agent_store_path": runtime_dir / "await-agent-pending.json",
    }
    monkeypatch.setattr(workflow_admin, "_workflow_run_store_paths", lambda: stores)
    monkeypatch.setattr(
        workflow_admin,
        "_read_composed_workflow_editor_document",
        lambda: SimpleNamespace(graphs=[]),
    )

    queue_store = SqliteTriggerEventQueue(stores["orchestration_store_path"])
    effect_store = SqliteExecutionStore(stores["orchestration_store_path"])
    base_at = datetime(2026, 5, 20, 10, 0, tzinfo=UTC)
    candidates = [("evt-no-session", base_at, None)]
    candidates.extend(
        (
            f"evt-session-{index:03d}",
            base_at + timedelta(minutes=index + 1),
            f"session-{index:03d}",
        )
        for index in range(500)
    )
    for event_id, occurred_at, session_id in candidates:
        queue_store.publish(
            TriggerEvent(
                event_id=event_id,
                trigger_id="trigger-search",
                trigger_type="trigger:test",
                occurred_at=occurred_at,
                payload={},
                trace_id=f"trace-{event_id}",
            )
        )
        queue_store.mark_delivered(event_id, delivered_at=occurred_at + timedelta(seconds=1))
        effect_store.record_execution(
            EffectExecutionRecord(
                execution_id=f"exec-{event_id}",
                idempotency_key=f"{event_id}::effect-search",
                event_id=event_id,
                trace_id=f"trace-{event_id}",
                source_node_id="trigger-search",
                source_trigger_type="trigger:test",
                effect_id="effect-search",
                effect_type="effect:test",
                status="succeeded",
                started_at=occurred_at + timedelta(seconds=2),
                finished_at=occurred_at + timedelta(seconds=3),
                resolved_config_hash=f"hash-{event_id}",
                resolved_config={"session_id": session_id} if session_id else {},
                input_payload={},
                outputs={},
            )
        )

    monkeypatch.setattr(
        SqliteTriggerEventQueue,
        "read_all",
        lambda self: pytest.fail("run search should not scan every trigger event"),
    )
    monkeypatch.setattr(
        SqliteExecutionStore,
        "read_all",
        lambda self: pytest.fail("run search should not scan every effect execution"),
    )
    monkeypatch.setattr(
        SqliteGateExecutionStore,
        "read_all",
        lambda self: pytest.fail("run search should not scan every gate execution"),
    )

    search = service.workflow_config_admin.read_workflow_run_search(
        node_id="effect-search", without_session=True
    )

    assert search.matched == 1
    assert search.returned == 1
    assert search.has_more is False
    assert [run.event_id for run in search.runs] == ["evt-no-session"]
    assert search.runs[0].session_ids == []
    assert search.runs[0].has_agent_session is False

    active_only_search = service.workflow_config_admin.read_workflow_run_search(
        node_id="effect-search", without_session=True, include_completed=False
    )
    assert active_only_search.matched == 0
    assert active_only_search.runs == []


def test_admin_service_read_workflow_run_snapshot_includes_recent_node_activity_from_json_stores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    workflow_admin = service.workflow_reports_admin
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    now = datetime(2026, 5, 21, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(workflow_reports_service, "_utc_now", lambda: now)
    stores = {
        "runtime_dir": runtime_dir,
        "orchestration_store_backend": "json",
        "orchestration_store_path": runtime_dir / "orchestration.db",
        "orchestration_retention_days": 30,
        "event_queue_store_path": runtime_dir / "trigger-orchestrator-events.json",
        "effect_execution_store_path": runtime_dir / "effect-orchestrator-executions.json",
        "gate_execution_store_path": runtime_dir / "gate-orchestrator-executions.json",
        "workflow_config_snapshot_store_path": runtime_dir / "workflow-config-snapshots.json",
        "await_agent_store_path": runtime_dir
        / "providers"
        / "gates"
        / "await-agent"
        / "pending.json",
        "legacy_await_agent_store_path": runtime_dir / "await-agent-pending.json",
    }
    monkeypatch.setattr(workflow_admin, "_workflow_run_store_paths", lambda: stores)
    monkeypatch.setattr(
        workflow_admin,
        "_read_composed_workflow_editor_document",
        lambda: SimpleNamespace(graphs=[]),
    )

    queue_store = TriggerEventQueue(stores["event_queue_store_path"])
    queue_store.publish(
        TriggerEvent(
            event_id="evt-trigger-recent",
            trigger_id="trigger-recent",
            trigger_type="trigger:test",
            occurred_at=now - timedelta(seconds=20),
            payload={"kind": "json"},
            trace_id="trace-trigger-recent",
        )
    )
    queue_store.mark_delivered("evt-trigger-recent", delivered_at=now - timedelta(seconds=3))

    effect_store = JsonExecutionStore(stores["effect_execution_store_path"])
    effect_store.record_execution(
        EffectExecutionRecord(
            execution_id="exec-effect-recent",
            idempotency_key="evt-effect-recent::effect-recent",
            event_id="evt-effect-recent",
            trace_id="trace-effect-recent",
            source_node_id="trigger-recent",
            source_trigger_type="trigger:test",
            effect_id="effect-recent",
            effect_type="effect:test",
            status="succeeded",
            started_at=now - timedelta(seconds=4),
            finished_at=now - timedelta(seconds=2),
            resolved_config_hash="hash-effect-recent",
            resolved_config={},
            input_payload={},
            outputs={},
        )
    )
    effect_store.record_execution(
        EffectExecutionRecord(
            execution_id="exec-effect-old",
            idempotency_key="evt-effect-old::effect-old",
            event_id="evt-effect-old",
            trace_id="trace-effect-old",
            source_node_id="trigger-old",
            source_trigger_type="trigger:test",
            effect_id="effect-old",
            effect_type="effect:test",
            status="succeeded",
            started_at=now - timedelta(seconds=30),
            finished_at=now - timedelta(seconds=20),
            resolved_config_hash="hash-effect-old",
            resolved_config={},
            input_payload={},
            outputs={},
        )
    )

    gate_store = JsonGateExecutionStore(stores["gate_execution_store_path"])
    gate_store.record_execution(
        GateExecutionRecord(
            execution_id="exec-gate-recent",
            idempotency_key="evt-gate-recent::gate-recent",
            event_id="evt-gate-recent",
            trace_id="trace-gate-recent",
            source_node_id="effect-recent",
            source_trigger_type="trigger:test",
            gate_id="gate-recent",
            gate_type="gate:test",
            status="succeeded",
            started_at=now - timedelta(seconds=3),
            finished_at=now - timedelta(seconds=1),
            resolved_config_hash="hash-gate-recent",
            resolved_config={},
            input_payload={},
            outputs={},
            inputs_by_source={},
            input_event_ids_by_source={},
            input_occurred_at_by_source={},
        )
    )

    snapshot = service.workflow_config_admin.read_workflow_run_snapshot()
    payload = snapshot.model_dump(mode="json")

    assert snapshot.generated_at == now
    assert payload["runs"] == []
    assert payload["node_activity"] == {}
    assert [item["node_id"] for item in payload["recent_node_activity"]] == [
        "gate-recent",
        "effect-recent",
        "trigger-recent",
    ]
    assert payload["recent_node_activity"][2]["activity_at"] == "2026-05-21T11:59:57Z"
    assert payload["recent_node_activity"][0]["source"] == "gate_execution"


def test_admin_service_read_workflow_run_snapshot_json_does_not_initialize_store_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    workflow_admin = service.workflow_reports_admin
    runtime_dir = tmp_path / "json-runtime"
    stores = {
        "runtime_dir": runtime_dir,
        "orchestration_store_backend": "json",
        "orchestration_store_path": runtime_dir / "orchestration.db",
        "orchestration_retention_days": 30,
        "event_queue_store_path": runtime_dir / "trigger-orchestrator-events.json",
        "effect_execution_store_path": runtime_dir / "effect-orchestrator-executions.json",
        "gate_execution_store_path": runtime_dir / "gate-orchestrator-executions.json",
        "workflow_config_snapshot_store_path": runtime_dir / "workflow-config-snapshots.json",
        "await_agent_store_path": runtime_dir
        / "providers"
        / "gates"
        / "await-agent"
        / "pending.json",
        "legacy_await_agent_store_path": runtime_dir / "await-agent-pending.json",
    }
    monkeypatch.setattr(workflow_admin, "_workflow_run_store_paths", lambda: stores)
    monkeypatch.setattr(
        workflow_admin,
        "_workflow_run_store_bundle",
        lambda _: pytest.fail("JSON snapshot read constructed workflow stores"),
    )
    monkeypatch.setattr(
        workflow_admin,
        "_read_composed_workflow_editor_document",
        lambda: SimpleNamespace(graphs=[]),
    )

    snapshot = service.workflow_config_admin.read_workflow_run_snapshot()

    assert snapshot.runs == []
    assert snapshot.node_activity == {}
    assert snapshot.recent_node_activity == []
    assert not runtime_dir.exists()


def test_admin_service_read_workflow_run_snapshot_includes_recent_node_activity_from_sqlite_stores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    workflow_admin = service.workflow_reports_admin
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    now = datetime(2026, 5, 21, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(workflow_reports_service, "_utc_now", lambda: now)
    stores = {
        "runtime_dir": runtime_dir,
        "orchestration_store_backend": "sqlite",
        "orchestration_store_path": runtime_dir / "orchestration.db",
        "orchestration_retention_days": 365,
        "event_queue_store_path": runtime_dir / "trigger-orchestrator-events.json",
        "effect_execution_store_path": runtime_dir / "effect-orchestrator-executions.json",
        "gate_execution_store_path": runtime_dir / "gate-orchestrator-executions.json",
        "workflow_config_snapshot_store_path": runtime_dir / "workflow-config-snapshots.json",
        "await_agent_store_path": runtime_dir
        / "providers"
        / "gates"
        / "await-agent"
        / "pending.json",
        "legacy_await_agent_store_path": runtime_dir / "await-agent-pending.json",
    }
    monkeypatch.setattr(workflow_admin, "_workflow_run_store_paths", lambda: stores)
    monkeypatch.setattr(
        workflow_admin,
        "_read_composed_workflow_editor_document",
        lambda: SimpleNamespace(graphs=[]),
    )

    queue_store = SqliteTriggerEventQueue(stores["orchestration_store_path"])
    queue_store.publish(
        TriggerEvent(
            event_id="evt-trigger-sqlite",
            trigger_id="trigger-sqlite",
            trigger_type="trigger:test",
            occurred_at=now - timedelta(seconds=20),
            payload={"kind": "sqlite"},
            trace_id="trace-trigger-sqlite",
        )
    )
    queue_store.mark_delivered("evt-trigger-sqlite", delivered_at=now - timedelta(seconds=3))

    effect_store = SqliteExecutionStore(stores["orchestration_store_path"])
    effect_store.record_execution(
        EffectExecutionRecord(
            execution_id="exec-effect-sqlite",
            idempotency_key="evt-effect-sqlite::effect-sqlite",
            event_id="evt-effect-sqlite",
            trace_id="trace-effect-sqlite",
            source_node_id="trigger-sqlite",
            source_trigger_type="trigger:test",
            effect_id="effect-sqlite",
            effect_type="effect:test",
            status="succeeded",
            started_at=now - timedelta(seconds=4),
            finished_at=now - timedelta(seconds=2),
            resolved_config_hash="hash-effect-sqlite",
            resolved_config={},
            input_payload={},
            outputs={},
        )
    )
    effect_store.record_execution(
        EffectExecutionRecord(
            execution_id="exec-effect-sqlite-old",
            idempotency_key="evt-effect-sqlite-old::effect-old",
            event_id="evt-effect-sqlite-old",
            trace_id="trace-effect-sqlite-old",
            source_node_id="trigger-sqlite-old",
            source_trigger_type="trigger:test",
            effect_id="effect-sqlite-old",
            effect_type="effect:test",
            status="succeeded",
            started_at=now - timedelta(seconds=30),
            finished_at=now - timedelta(seconds=20),
            resolved_config_hash="hash-effect-sqlite-old",
            resolved_config={},
            input_payload={},
            outputs={},
        )
    )

    gate_store = SqliteGateExecutionStore(stores["orchestration_store_path"])
    gate_store.record_execution(
        GateExecutionRecord(
            execution_id="exec-gate-sqlite",
            idempotency_key="evt-gate-sqlite::gate-sqlite",
            event_id="evt-gate-sqlite",
            trace_id="trace-gate-sqlite",
            source_node_id="effect-sqlite",
            source_trigger_type="trigger:test",
            gate_id="gate-sqlite",
            gate_type="gate:test",
            status="succeeded",
            started_at=now - timedelta(seconds=3),
            finished_at=now - timedelta(seconds=1),
            resolved_config_hash="hash-gate-sqlite",
            resolved_config={},
            input_payload={},
            outputs={},
            inputs_by_source={},
            input_event_ids_by_source={},
            input_occurred_at_by_source={},
        )
    )

    snapshot = service.workflow_config_admin.read_workflow_run_snapshot()
    payload = snapshot.model_dump(mode="json")

    assert snapshot.generated_at == now
    assert payload["runs"] == []
    assert payload["node_activity"] == {}
    assert [item["node_id"] for item in payload["recent_node_activity"]] == [
        "gate-sqlite",
        "effect-sqlite",
        "trigger-sqlite",
    ]
    assert payload["recent_node_activity"][2]["activity_at"] == "2026-05-21T11:59:57Z"
    assert payload["recent_node_activity"][2]["source"] == "trigger_event"


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
    service = build_admin_runtime(config_path)

    payload = service.workflow_reports_admin.read_conformance_reports()

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
    service = build_admin_runtime(write_config(tmp_path))

    payload = service.workflow_reports_admin.read_conformance_reports()

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
    service = build_admin_runtime(config_path)

    reports = {
        report.kind: report
        for report in service.workflow_reports_admin.read_conformance_reports().reports
    }

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
    service = build_admin_runtime(config_path)

    reports = {
        report.kind: report
        for report in service.workflow_reports_admin.read_conformance_reports().reports
    }

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
    service = build_admin_runtime(config_path)
    service.workflow_reports_admin._project_dir_provider = lambda: REPO_ROOT
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
        Path(env["EVANDER_TRIGGER_CONFORMANCE_REPORT"]).write_text(
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

    payload = service.workflow_reports_admin.run_conformance_report("trigger")

    assert payload.kind == "trigger"
    assert payload.exit_code == 0
    assert payload.output == "ok"
    assert len(calls) == 1
    assert calls[0]["command"][1:] == [
        "-m",
        "pytest",
        "-q",
        "tests/test_trigger_conformance_suite.py",
    ]
    assert calls[0]["cwd"] == REPO_ROOT
    assert calls[0]["text"] is True
    assert calls[0]["capture_output"] is True
    assert calls[0]["timeout"] == admin_service.CONFORMANCE_REPORT_RUN_TIMEOUT_SECONDS
    assert calls[0]["env"]["EVANDER_TRIGGER_CONFORMANCE_REPORT"] == str(
        runtime_dir / "trigger-conformance-report.json"
    )
    reports = {report.kind: report for report in payload.reports.reports}
    assert reports["trigger"].state == "pass"
    assert reports["trigger"].requirement_count == 1
    assert reports["effect"].state == "missing"


def test_run_conformance_report_rejects_unknown_kind(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))

    with pytest.raises(ValueError, match="Unknown conformance report"):
        service.workflow_reports_admin.run_conformance_report("workflow")


def test_run_conformance_report_timeout_decodes_captured_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.workflow_reports_admin._project_dir_provider = lambda: REPO_ROOT

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
        service.workflow_reports_admin.run_conformance_report("trigger")

    message = str(exc_info.value)
    assert "Trigger conformance suite timed out after" in message
    assert "partial stdout" in message
    assert "partial stderr" in message
    assert "b'partial" not in message


def test_run_conformance_report_returns_payload_for_pytest_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    service.workflow_reports_admin._project_dir_provider = lambda: REPO_ROOT

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
        Path(env["EVANDER_EFFECT_CONFORMANCE_REPORT"]).write_text(
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
        return subprocess.CompletedProcess(
            command, 1, stdout="failed stdout", stderr="failed stderr"
        )

    monkeypatch.setattr(admin_service.subprocess, "run", fake_run)

    payload = service.workflow_reports_admin.run_conformance_report("effect")

    assert payload.exit_code == 1
    assert payload.output == "failed stdout\nfailed stderr"
    reports = {report.kind: report for report in payload.reports.reports}
    assert reports["effect"].state == "fail"
    assert reports["effect"].missing_coverage_count == 1


def test_workflow_editor_rejects_graph_metadata_that_crosses_connected_components(
    tmp_path: Path,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))

    with pytest.raises(ValueError, match="node_ids must match one connected graph"):
        service.workflow_config_admin.validate_workflow_editor_document(
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
    service = build_admin_runtime(write_config(tmp_path))

    with pytest.raises(
        ValueError, match="Workflow layout references unknown node IDs: missing-node"
    ):
        service.workflow_config_admin.validate_workflow_editor_document(
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
    service = build_admin_runtime(write_config(tmp_path))

    result = service.workflow_config_admin.validate_workflow_editor_document(
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
    service = build_admin_runtime(write_config(tmp_path))

    result = service.workflow_config_admin.validate_workflow_editor_document(
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


def test_workflow_editor_save_and_reload_preserve_deserialize_declared_outputs(
    tmp_path: Path,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))

    save_result = service.workflow_config_admin.save_workflow_editor_document(
        config={
            "runtime_dir": str(tmp_path / "runtime"),
            "triggers": [
                {
                    "id": "trigger-event-main",
                    "display_name": "Trigger event",
                    "type": "trigger:event",
                    "event_type": "workflow.payload-ready",
                    "outputs": {
                        "payload_text": {"type": "string"},
                    },
                }
            ],
            "gates": [
                {
                    "id": "gate-deserialize-1",
                    "display_name": "Deserialize",
                    "type": "gate:deserialize",
                    "content": "${{trigger-event-main.payload_text}}",
                    "format": "auto",
                    "outputs": {
                        "title": {"type": "string"},
                        "metadata": {"type": "object"},
                    },
                }
            ],
            "effects": [
                {
                    "id": "prompt-agent",
                    "display_name": "Prompt agent",
                    "type": "effect:agent:prompt",
                    "session_id": "session-1",
                    "prompt": "Review ${{gate-deserialize-1.title}}",
                }
            ],
            "edges": [
                {"source_id": "trigger-event-main", "target_id": "gate-deserialize-1"},
                {"source_id": "gate-deserialize-1", "target_id": "prompt-agent"},
            ],
        },
        layout={},
    )

    expected_outputs = {
        "title": {"type": "string"},
        "metadata": {"type": "object"},
    }

    gate = next(item for item in save_result.config["gates"] if item["id"] == "gate-deserialize-1")
    assert gate["outputs"] == expected_outputs
    assert save_result.output_schemas["gate-deserialize-1"] == expected_outputs
    assert save_result.validation_diagnostics == []

    reread = service.workflow_config_admin.read_workflow_editor_document()
    reread_gate = next(
        item for item in reread.config["gates"] if item["id"] == "gate-deserialize-1"
    )
    assert reread_gate["outputs"] == expected_outputs
    assert reread.output_schemas["gate-deserialize-1"] == expected_outputs


def test_workflow_editor_validation_derives_serialize_outputs_server_side(
    tmp_path: Path,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))

    result = service.workflow_config_admin.validate_workflow_editor_document(
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
    service = build_admin_runtime(write_config(tmp_path))

    result = service.workflow_config_admin.save_workflow_editor_document(
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
    reread = service.workflow_config_admin.read_workflow_editor_document()
    assert reread.config["effects"][0]["id"] == "prompt-agent"


def test_workflow_editor_save_writes_via_atomic_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    replace_calls: list[tuple[str, str]] = []
    real_replace = os.replace

    def tracking_replace(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        replace_calls.append((os.fspath(src), os.fspath(dst)))
        real_replace(src, dst)

    monkeypatch.setattr(admin_service.os, "replace", tracking_replace)

    service.workflow_config_admin.save_workflow_editor_document(
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
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    create_result = service.directory_admin.create_child(parent="root", name="team")
    assert create_result.ok is True and create_result.data is not None
    team_path = create_result.data.details.directory.path
    write_result = service.operations_admin.write_directory_domain_config(
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

    monkeypatch.setattr("evander.admin.services.process_helpers.subprocess.run", fake_run)

    result = service.vm_agent_admin.create_issue_vm(
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
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    create_result = service.directory_admin.create_child(parent="root", name="team")
    assert create_result.ok is True and create_result.data is not None
    team_path = create_result.data.details.directory.path
    write_result = service.operations_admin.write_directory_domain_config(
        team_path,
        "vm",
        {
            "gateway_url": "http://10.20.30.40:8000/mcp",
            "openai_relay_port": 12000,
            "nuget_relay_port": 13000,
        },
    )
    assert write_result.ok is True
    issue = service.issue_git_admin.create_local_issue(
        title="Team issue", body="body", directory_path=team_path
    )

    captured_env: dict[str, str] = {}

    def fake_run(*args, **kwargs):
        captured_env.update(kwargs.get("env", {}))
        return subprocess.CompletedProcess(args[0], 0, stdout="", stderr="")

    monkeypatch.setattr("evander.admin.services.process_helpers.subprocess.run", fake_run)

    result = service.vm_agent_admin.create_issue_vm(
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
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    team_a = service.directory_admin.create_child(parent="root", name="team-a")
    team_b = service.directory_admin.create_child(parent="root", name="team-b")
    assert team_a.ok is True and team_a.data is not None
    assert team_b.ok is True and team_b.data is not None
    team_a_path = team_a.data.details.directory.path
    team_b_path = team_b.data.details.directory.path

    assert (
        service.operations_admin.write_directory_domain_config(
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
        service.operations_admin.write_directory_domain_config(
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

    project_a_issue = service.issue_git_admin.create_local_issue(
        directory_path=team_a_path,
        yaml_path=str(project_a_source),
    )
    service.issue_git_admin.create_local_issue(
        directory_path=team_b_path,
        yaml_path=str(project_b_source),
    )

    captured_env: dict[str, str] = {}

    def fake_run(*args, **kwargs):
        captured_env.update(kwargs.get("env", {}))
        return subprocess.CompletedProcess(args[0], 0, stdout="", stderr="")

    monkeypatch.setattr("evander.admin.services.process_helpers.subprocess.run", fake_run)

    result = service.vm_agent_admin.create_issue_vm(
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
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    create_result = service.directory_admin.create_child(parent="root", name="team")
    assert create_result.ok is True and create_result.data is not None
    team_path = create_result.data.details.directory.path
    write_result = service.operations_admin.write_directory_domain_config(
        team_path,
        "issue_setup",
        {
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

    monkeypatch.setattr("evander.admin.services.process_helpers.subprocess.run", fake_run)

    result = service.vm_agent_admin.create_issue_vm(
        issue="124",
        session_id="issue-124-session",
        directory_path=team_path,
        model="gpt-5.2-codex",
    )

    assert result.success is True
    assert "EVANDER_GIT_ROOT" not in captured_env
    assert captured_env["EVANDER_GIT_HOST"] == "10.1.2.3"
    assert captured_env["EVANDER_GIT_USER"] == "gitbot"
    assert captured_env["EVANDER_GIT_SSH_ALIAS"] == "issue-git"
    assert captured_env["AGENT_POST_EXEC_VERIFICATION_PROMPT"] == "verify everything"
    assert captured_env["ENABLE_GUEST_HEARTBEAT"] == "1"
    assert captured_env["GUEST_HEARTBEAT_PROMPT"] == "continue working"
    assert captured_env["EVANDER_AZURE_DEVOPS_PR_PAT_ENV"] == "AZURE_DEVOPS_PR_PAT"


def test_create_issue_vm_uses_issue_setup_azure_pat_override(tmp_path: Path, monkeypatch) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    create_result = service.directory_admin.create_child(parent="root", name="team")
    assert create_result.ok is True and create_result.data is not None
    team_path = create_result.data.details.directory.path
    write_result = service.operations_admin.write_directory_domain_config(
        team_path,
        "issue_setup",
        {
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

    monkeypatch.setattr("evander.admin.services.process_helpers.subprocess.run", fake_run)

    result = service.vm_agent_admin.create_issue_vm(
        issue="125",
        session_id="issue-125-session",
        directory_path=team_path,
        model="gpt-5.2-codex",
    )

    assert result.success is True
    assert captured_env["EVANDER_AZURE_DEVOPS_PR_PAT_ENV"] == "TEAM_ISSUE_SETUP_ADO_PAT"


def test_create_issue_vm_uses_issue_setup_fallback_env_when_domain_missing(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("EVANDER_GIT_HOST", "10.55.0.1")
    monkeypatch.setenv("EVANDER_GIT_USER", "env-user")
    monkeypatch.setenv("EVANDER_GIT_SSH_ALIAS", "env-git")
    monkeypatch.setenv("EVANDER_AZURE_DEVOPS_PR_PAT_ENV", "ENV_PAT_NAME")
    monkeypatch.setenv("AGENT_POST_EXEC_VERIFICATION_PROMPT", "verify from env")
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))

    captured_env: dict[str, str] = {}

    def fake_run(*args, **kwargs):
        captured_env.update(kwargs.get("env", {}))
        return subprocess.CompletedProcess(args[0], 0, stdout="", stderr="")

    monkeypatch.setattr("evander.admin.services.process_helpers.subprocess.run", fake_run)

    result = service.vm_agent_admin.create_issue_vm(
        issue="126",
        session_id="issue-126-session",
        directory_path="root",
        model="gpt-5.2-codex",
    )

    assert result.success is True
    assert "EVANDER_GIT_ROOT" not in captured_env
    assert captured_env["EVANDER_GIT_HOST"] == "10.55.0.1"
    assert captured_env["EVANDER_GIT_USER"] == "env-user"
    assert captured_env["EVANDER_GIT_SSH_ALIAS"] == "env-git"
    assert captured_env["EVANDER_AZURE_DEVOPS_PR_PAT_ENV"] == "ENV_PAT_NAME"
    assert captured_env["AGENT_POST_EXEC_VERIFICATION_PROMPT"] == "verify from env"


def test_resolve_agent_model_prefers_session_model_over_issue_setup_default(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    service.operations_admin.write_directory_domain_config(
        "root", "issue_setup", {"default_model": "gpt-5.2-codex"}
    )
    manifest = SessionManifest(
        session_id="session-1",
        vm_id="vm-123",
        directory_path="root",
        allowed_tools=[],
        model="gpt-5.3-codex",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    service.runtime.session_repository.upsert_session(manifest)

    resolved = service.vm_agent_admin._resolve_agent_model_for_session("session-1")

    assert resolved == "gpt-5.3-codex"


def test_resolve_agent_model_uses_issue_setup_default_when_session_model_missing(
    tmp_path: Path,
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    service.operations_admin.write_directory_domain_config(
        "root", "issue_setup", {"default_model": "gpt-5.2-codex"}
    )
    manifest = SessionManifest(
        session_id="session-1",
        vm_id="vm-123",
        directory_path="root",
        allowed_tools=[],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    service.runtime.session_repository.upsert_session(manifest)

    resolved = service.vm_agent_admin._resolve_agent_model_for_session("session-1")

    assert resolved == "gpt-5.2-codex"


def test_resolve_agent_model_fails_when_session_and_issue_setup_models_missing(
    tmp_path: Path,
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
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
        service.vm_agent_admin._resolve_agent_model_for_session("session-1")


def test_create_issue_vm_never_uses_pat_secret_as_env_var_name(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("EVANDER_AZURE_DEVOPS_PR_PAT_ENV", raising=False)
    monkeypatch.setenv("AZURE_DEVOPS_PR_PAT", "super-secret-pat")
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))

    captured_env: dict[str, str] = {}

    def fake_run(*args, **kwargs):
        captured_env.update(kwargs.get("env", {}))
        return subprocess.CompletedProcess(args[0], 0, stdout="", stderr="")

    monkeypatch.setattr("evander.admin.services.process_helpers.subprocess.run", fake_run)

    result = service.vm_agent_admin.create_issue_vm(
        issue="127",
        session_id="issue-127-session",
        directory_path="root",
        model="gpt-5.2-codex",
    )

    assert result.success is True
    assert captured_env["EVANDER_AZURE_DEVOPS_PR_PAT_ENV"] == "AZURE_DEVOPS_PR_PAT"


def test_create_issue_vm_deletes_session_when_setup_fails_before_vm_exists(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    issue = service.issue_git_admin.create_local_issue(title="Issue VM", body="create and fail")
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.dominfo_field", lambda vm_name, field_name: None
    )
    monkeypatch.setattr(
        "evander.admin.services.process_helpers.run_subprocess",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=17, stdout="", stderr="boom"
        ),
    )

    result = service.vm_agent_admin.create_issue_vm(
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
    service = build_admin_runtime(write_config(tmp_path))
    issue = service.issue_git_admin.create_local_issue(title="Issue VM", body="create and fail")
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.dominfo_field",
        lambda vm_name, field_name: (_ for _ in ()).throw(RuntimeError("virsh unavailable")),
    )
    monkeypatch.setattr(
        "evander.admin.services.process_helpers.run_subprocess",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=17, stdout="", stderr="boom"
        ),
    )

    result = service.vm_agent_admin.create_issue_vm(
        issue=issue.issue_number,
        session_id="issue-1-session",
        model="gpt-5.2-codex",
    )

    assert result.success is False
    assert result.message == "boom"
    assert service.runtime.session_repository.get_session("issue-1-session") is None


def test_admin_service_rejects_directory_edits_when_feature_flag_disabled(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))

    result = service.directory_admin.create_child(parent="root", name="team")

    assert result.ok is False
    assert result.errors[0].code == "feature_disabled"


def test_renew_session_sets_expiration_from_now(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="issue-14084-session",
            vm_id="vm-14084",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(days=5),
        ),
    )

    before = datetime.now(UTC)
    result = service.operations_admin.renew_session("14084", extension="6h")
    after = datetime.now(UTC)

    renewed = service.runtime.session_repository.get_session("issue-14084-session")
    assert renewed is not None
    assert result.success is True
    assert result.session_id == "issue-14084-session"
    assert before + timedelta(hours=6) <= renewed.expires_at <= after + timedelta(hours=6)


def test_revoke_session_expires_immediately(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_id="vm-1",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=2),
        ),
    )

    before = datetime.now(UTC)
    result = service.operations_admin.revoke_session("session-1")
    after = datetime.now(UTC)

    revoked = service.runtime.session_repository.get_session("session-1")
    assert revoked is not None
    assert result.success is True
    assert before <= revoked.expires_at <= after


def test_delete_session_removes_detached_session(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
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

    result = service.vm_agent_admin.delete_session("session-a")

    remaining = service.runtime.session_repository.read_all()
    assert result.success is True
    assert result.session_id == "session-a"
    assert set(remaining) == {"session-b"}


def test_delete_session_rejects_vm_linked_agent_session(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="issue-13301-session",
            vm_id="vm-13301",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    with pytest.raises(ValueError, match="Delete the agent instead"):
        service.vm_agent_admin.delete_session("issue-13301-session")

    assert service.runtime.session_repository.get_session("issue-13301-session") is not None


def test_delete_session_rejects_container_agent_session(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
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
        service.vm_agent_admin.delete_session("container-session")

    assert service.runtime.session_repository.get_session("container-session") is not None


def test_delete_session_cleans_detached_worker_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    now = datetime(2026, 5, 21, 12, 0, tzinfo=UTC)
    authorized_keys = tmp_path / "authorized_keys"
    authorized_keys.write_text(
        "\n".join(
            [
                "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOldWriteKey old",
                "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKeepMe keep",
                "",
            ]
        ),
        encoding="utf-8",
    )
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="detached-session",
            allowed_tools=[],
            expires_at=now + timedelta(hours=1),
            metadata={
                "container_git_write_access": {
                    "public_key_body": "AAAAC3NzaC1lZDI1NTE5AAAAIOldWriteKey",
                    "authorized_keys_path": str(authorized_keys),
                },
            },
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
            for record in repository.list_by_session(session_id):
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
        service.vm_agent_admin,
        "container_worker_backend",
        MethodType(lambda self, **_kwargs: fake_backend, service.vm_agent_admin),
    )

    result = service.vm_agent_admin.delete_session("detached-session")

    assert result.success is True
    assert fake_backend.deleted_sessions == ["detached-session"]
    assert service.runtime.session_repository.get_session("detached-session") is None
    assert repository.read_all() == {}
    assert "worker_session_deleted=1" in result.message
    assert "removed_mappings=1" in result.message
    assert "git_write_access_revoked=1" in result.message
    authorized_keys_text = authorized_keys.read_text(encoding="utf-8")
    assert "AAAAC3NzaC1lZDI1NTE5AAAAIOldWriteKey" not in authorized_keys_text
    assert "AAAAC3NzaC1lZDI1NTE5AAAAIKeepMe" in authorized_keys_text


def test_cleanup_container_session_records_cleaned_up_tombstone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    now = datetime(2026, 5, 21, 12, 0, tzinfo=UTC)
    monkeypatch.setattr("evander.admin.services.vm_agent_service._utc_now", lambda: now)
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
        service.vm_agent_admin,
        "container_worker_backend",
        MethodType(lambda self, **_kwargs: fake_backend, service.vm_agent_admin),
    )

    result = service.vm_agent_admin.cleanup_container_session("container-session")

    assert result.success is True
    assert result.action == "cleanup_container_session"
    assert fake_backend.deleted_sessions == ["container-session"]
    updated = service.runtime.session_repository.get_session("container-session")
    assert updated is not None
    assert updated.metadata["container_lifecycle"]["state"] == "cleaned_up"
    assert updated.metadata["container_lifecycle"]["cleanup_status"] == "cleanup_succeeded"
    assert updated.expires_at <= now
    summaries = {
        item.session_id: item for item in service.vm_agent_admin.list_sessions(include_expired=True)
    }
    lifecycle = summaries["container-session"].container_lifecycle
    assert lifecycle is not None
    assert lifecycle.state == ContainerSessionLifecycleState.CLEANED_UP
    assert lifecycle.actions.cleanup.supported is False
    assert lifecycle.cleanup_error is None
    assert lifecycle.last_signal_at == now - timedelta(minutes=5)


def test_cleanup_container_session_records_failure_and_keeps_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    now = datetime(2026, 5, 21, 12, 0, tzinfo=UTC)
    monkeypatch.setattr("evander.admin.services.vm_agent_service._utc_now", lambda: now)
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
            for record in repository.list_by_session(session_id):
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
        service.vm_agent_admin,
        "container_worker_backend",
        MethodType(lambda self, **_kwargs: FakeContainerBackend(), service.vm_agent_admin),
    )

    result = service.vm_agent_admin.cleanup_container_session("container-session")

    assert result.success is False
    assert result.action == "cleanup_container_session"
    assert "cleanup exploded" in result.message
    updated = service.runtime.session_repository.get_session("container-session")
    assert updated is not None
    assert updated.metadata["container_lifecycle"]["state"] == "cleanup_failed"
    assert updated.metadata["container_lifecycle"]["cleanup_status"] == "cleanup_failed"
    assert "cleanup exploded" in updated.metadata["container_lifecycle"]["cleanup_error"]
    summaries = {
        item.session_id: item for item in service.vm_agent_admin.list_sessions(include_expired=True)
    }
    lifecycle = summaries["container-session"].container_lifecycle
    assert lifecycle is not None
    assert lifecycle.state == ContainerSessionLifecycleState.CLEANUP_FAILED
    assert lifecycle.actions.cleanup.supported is True
    assert (
        lifecycle.cleanup_error
        == "container worker DELETE /v1/sessions/container-session failed: cleanup exploded"
    )
    assert lifecycle.last_signal_at == now - timedelta(minutes=5)


def test_delete_admin_vm_agent_removes_chat_assistant_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
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

    monkeypatch.setattr("evander.admin.services.process_helpers.subprocess.run", _fake_run)

    result = service.vm_agent_admin.delete_admin_agent("assistant-shared", backend="libvirt_vm")

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
    service = build_admin_runtime(write_config(tmp_path))
    authorized_keys = tmp_path / "authorized_keys"
    authorized_keys.write_text(
        "\n".join(
            [
                "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOldWriteKey old",
                "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKeepMe keep",
                "",
            ]
        ),
        encoding="utf-8",
    )
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
                "container_git_write_access": {
                    "public_key_body": "AAAAC3NzaC1lZDI1NTE5AAAAIOldWriteKey",
                    "authorized_keys_path": str(authorized_keys),
                },
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
        service.vm_agent_admin,
        "container_worker_backend",
        MethodType(lambda self, **_kwargs: fake_backend, service.vm_agent_admin),
    )

    result = service.vm_agent_admin.delete_admin_agent(
        "container-session", backend="container_worker_vm"
    )

    assert result["success"] is True
    assert result["status"] == "deleted"
    assert result["action"] == "delete"
    assert fake_backend.deleted_sessions == ["container-session"]
    pr_map_payload = json.loads(service.config.pr_map_path.read_text(encoding="utf-8"))
    assert set(pr_map_payload["entries"]) == {"azure_devops:org/other:1"}
    authorized_keys_text = authorized_keys.read_text(encoding="utf-8")
    assert "AAAAC3NzaC1lZDI1NTE5AAAAIOldWriteKey" not in authorized_keys_text
    assert "AAAAC3NzaC1lZDI1NTE5AAAAIKeepMe" in authorized_keys_text
    assert "git_write_access_revoked=1" in result["message"]
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
    service = build_admin_runtime(write_config(tmp_path))
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
        service.vm_agent_admin,
        "container_worker_backend",
        MethodType(lambda self, **_kwargs: fake_backend, service.vm_agent_admin),
    )

    result = service.vm_agent_admin.delete_admin_agent(
        "container-session", backend="container_worker_vm"
    )

    assert result["success"] is True
    assert result["status"] == "deleted"
    assert fake_backend.destroyed == []
    assert fake_backend.deleted_sessions == ["container-session"]
    assert service.runtime.session_repository.get_session("container-session") is None


def test_delete_admin_agent_destroys_running_container_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    authorized_keys = tmp_path / "authorized_keys"
    authorized_keys.write_text(
        "\n".join(
            [
                "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOldWriteKey old",
                "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKeepMe keep",
                "",
            ]
        ),
        encoding="utf-8",
    )
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
                "container_git_write_access": {
                    "public_key_body": "AAAAC3NzaC1lZDI1NTE5AAAAIOldWriteKey",
                    "authorized_keys_path": str(authorized_keys),
                },
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
        service.vm_agent_admin,
        "container_worker_backend",
        MethodType(lambda self, **_kwargs: fake_backend, service.vm_agent_admin),
    )

    result = service.vm_agent_admin.delete_admin_agent("exec-1", backend="container_worker_vm")

    assert result["success"] is True
    assert result["status"] == "deleted"
    assert fake_backend.destroyed == ["exec-1"]
    assert fake_backend.deleted_sessions == ["container-session"]
    authorized_keys_text = authorized_keys.read_text(encoding="utf-8")
    assert "AAAAC3NzaC1lZDI1NTE5AAAAIOldWriteKey" not in authorized_keys_text
    assert "AAAAC3NzaC1lZDI1NTE5AAAAIKeepMe" in authorized_keys_text
    assert "git_write_access_revoked=1" in result["message"]
    assert service.runtime.session_repository.get_session("container-session") is None
    assert repository.read_all() == {}


def test_set_session_allowed_tools_updates_manifest(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="issue-13302-session",
            vm_id="vm-13302",
            allowed_tools=[ToolName.CLAIM_NEXT_ISSUE],
            expires_at=datetime.now(UTC) + timedelta(hours=2),
        ),
    )

    result = service.operations_admin.set_session_allowed_tools(
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


def test_set_session_allowed_tools_fast_forwards_existing_discord_target_cursor(
    tmp_path: Path,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    communication_targets = SqliteCommunicationTargetsRepository(
        communication_targets_db_path(service.config.runtime_dir)
    )
    communication_targets.write_overlay(
        "root",
        {
            "targets": [
                {
                    "id": "discord-ops",
                    "provider": "discord",
                    "kind": "discord",
                    "address": "123456789012345678",
                    "policy": {"capabilities": ["read", "write"]},
                }
            ]
        },
    )
    service.runtime.discord_inbox_service.store.add_message_if_new(
        _discord_message(
            "msg-1",
            channel_id="123456789012345678",
            created_at=datetime(2026, 3, 21, tzinfo=UTC),
        )
    )
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="issue-13302-session",
            vm_id="vm-13302",
            allowed_tools=[ToolName.CLAIM_NEXT_ISSUE],
            discord_channel_allowlist=["discord-ops"],
            expires_at=datetime.now(UTC) + timedelta(hours=2),
        ),
    )
    assert (
        service.runtime.discord_inbox_service.store.get_session_cursor(
            "issue-13302-session", "123456789012345678"
        )
        is None
    )

    before = datetime.now(UTC)
    result = service.operations_admin.set_session_allowed_tools(
        "13302",
        [
            ToolName.CLAIM_NEXT_ISSUE.value,
            ToolName.DISCORD_GET_COMMANDS.value,
        ],
    )
    after = datetime.now(UTC)

    assert result.success is True
    _assert_cursor_seeded_during_operation(
        service.runtime.discord_inbox_service.store.get_session_cursor(
            "issue-13302-session", "123456789012345678"
        ),
        before=before,
        after=after,
    )
    assert (
        service.runtime.discord_inbox_service.store.has_unread_messages(
            session_id="issue-13302-session",
            channel_ids=["123456789012345678"],
        )
        is False
    )


def test_set_session_allowed_script_names_updates_manifest(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="issue-13302-session",
            vm_id="vm-13302",
            allowed_tools=[ToolName.CLAIM_NEXT_ISSUE],
            allowed_script_names=["old-script"],
            expires_at=datetime.now(UTC) + timedelta(hours=2),
        ),
    )

    result = service.operations_admin.set_session_allowed_script_names(
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
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    service.operations_admin.write_directory_domain_config(
        "root",
        "contacts",
        {
            "contacts": {
                "ops": {
                    "name": "Ops",
                    "phone_numbers": [{"label": "main", "number": "+15550001", "primary": True}],
                },
                "support": {
                    "name": "Support",
                    "phone_numbers": [{"label": "main", "number": "+15550002", "primary": True}],
                },
            }
        },
    )
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="issue-13302-session",
            vm_id="vm-13302",
            allowed_tools=[ToolName.CLAIM_NEXT_ISSUE],
            allowed_script_names=["old-script"],
            allowed_contact_ids=["ops"],
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

    result = service.operations_admin.update_session_policy(
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
            "allowed_contact_ids": [" ops ", "support", "ops"],
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
            "web_search_enabled": True,
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
    assert updated.allowed_contact_ids == ["ops", "support"]
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
    assert updated.web_search_enabled is True
    assert updated.metadata == {"keep": "new-value"}

    with pytest.raises(
        ValueError,
        match=(
            r"contacts allowlist for session 'issue-13302-session' and directory 'root' "
            r"references unknown contact ids: missing"
        ),
    ):
        service.operations_admin.update_session_policy(
            "13302",
            {
                "allowed_contact_ids": ["missing"],
            },
        )


def test_update_session_policy_fast_forwards_added_discord_target_cursor(
    tmp_path: Path,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    communication_targets = SqliteCommunicationTargetsRepository(
        communication_targets_db_path(service.config.runtime_dir)
    )
    communication_targets.write_overlay(
        "root",
        {
            "targets": [
                {
                    "id": "discord-ops",
                    "provider": "discord",
                    "kind": "discord",
                    "address": "123456789012345678",
                    "policy": {"capabilities": ["read", "write"]},
                }
            ]
        },
    )
    service.runtime.discord_inbox_service.store.add_messages_if_new(
        [
            _discord_message(
                "msg-1",
                channel_id="123456789012345678",
                created_at=datetime(2026, 3, 21, tzinfo=UTC),
            ),
            _discord_message(
                "msg-2",
                channel_id="123456789012345678",
                created_at=datetime(2026, 6, 23, 18, 53, 43, tzinfo=UTC),
            ),
        ]
    )
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="issue-13302-session",
            vm_id="vm-13302",
            allowed_tools=[ToolName.DISCORD_GET_COMMANDS],
            expires_at=datetime.now(UTC) + timedelta(hours=2),
        ),
    )

    before = datetime.now(UTC)
    result = service.operations_admin.update_session_policy(
        "13302",
        {
            "discord_channel_allowlist": ["discord-ops"],
        },
    )
    after = datetime.now(UTC)

    assert result.success is True
    updated = service.runtime.session_repository.get_session("issue-13302-session")
    assert updated is not None
    assert updated.discord_channel_allowlist == ["discord-ops"]
    _assert_cursor_seeded_during_operation(
        service.runtime.discord_inbox_service.store.get_session_cursor(
            "issue-13302-session", "123456789012345678"
        ),
        before=before,
        after=after,
    )
    assert (
        service.runtime.discord_inbox_service.store.has_unread_messages(
            session_id="issue-13302-session",
            channel_ids=["123456789012345678"],
        )
        is False
    )


def test_update_session_policy_and_agent_validate_approval_notification_targets(
    tmp_path: Path,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    communication_targets = SqliteCommunicationTargetsRepository(
        communication_targets_db_path(service.config.runtime_dir)
    )
    communication_targets.write_overlay(
        "root",
        {
            "targets": [
                {
                    "id": "ops-approvals",
                    "provider": "teams",
                    "kind": "teams",
                    "address": "team-a/channel-1",
                    "policy": {"capabilities": ["notify"]},
                },
                {
                    "id": "ops-read",
                    "provider": "discord",
                    "kind": "discord",
                    "address": "123456789012345678",
                    "policy": {"capabilities": ["read"]},
                },
            ]
        },
    )
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="issue-13302-session",
            vm_id="vm-13302",
            allowed_tools=[ToolName.CLAIM_NEXT_ISSUE],
            expires_at=datetime.now(UTC) + timedelta(hours=2),
        ),
    )

    result = service.operations_admin.update_session_policy(
        "13302",
        {
            "approval_notification_target_ids": [" OPS-Approvals ", "ops-approvals"],
        },
    )
    updated = service.runtime.session_repository.get_session("issue-13302-session")
    assert result.success is True
    assert updated is not None
    assert updated.approval_notification_target_ids == ["ops-approvals"]

    with pytest.raises(ValueError, match="unknown communication target\\(s\\): missing-target"):
        service.operations_admin.update_session_policy(
            "13302",
            {
                "approval_notification_target_ids": ["missing-target"],
            },
        )

    agent_result = service.vm_agent_admin.update_admin_agent(
        "vm-13302",
        payload={
            "approval_notification_target_ids": ["ops-approvals"],
        },
    )
    assert agent_result["status"] == "updated"
    updated = service.runtime.session_repository.get_session("issue-13302-session")
    assert updated is not None
    assert updated.approval_notification_target_ids == ["ops-approvals"]

    with pytest.raises(ValueError, match="without notify capability: ops-read"):
        service.vm_agent_admin.update_admin_agent(
            "vm-13302",
            payload={
                "approval_notification_target_ids": ["ops-read"],
            },
        )


def test_effective_tool_policy_payload_combines_directory_and_session_rules(
    tmp_path: Path,
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    service.operations_admin.write_directory_domain_config(
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

    effective = service.vm_agent_admin.effective_tool_policy_payload(
        service.runtime.session_repository.get_session("issue-13302-session")
    )

    assert effective is not None
    assert effective["allowed_tools"] == ["claim_next_issue", "approval_check"]
    assert effective["inherited_approval_required_tools"] == ["approval_check"]
    assert effective["approval_required_tools"] == [
        "approval_check",
        "complete_issue",
    ]
    assert [
        item["id"] for item in effective["tool_argument_policies"]["create_ado_backlog_item"]
    ] == [
        "directory-policy",
        "session-policy",
    ]


def test_effective_tool_policy_payload_reuses_resolved_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="agent-session",
            vm_id="agent-session",
            allowed_tools=[ToolName.RUN_SCRIPT],
            approval_required_tools=[ToolName.RUN_SCRIPT],
            expires_at=datetime.now(UTC) + timedelta(hours=2),
        ),
    )
    session = service.runtime.session_repository.get_session("agent-session")
    assert session is not None
    original_resolve = service.runtime.policy_config.resolve_for_session_manifest
    resolve_calls: list[str] = []

    def resolve_for_session_manifest(candidate: SessionManifest):
        resolve_calls.append(candidate.session_id)
        return original_resolve(candidate)

    monkeypatch.setattr(
        service.runtime.policy_config,
        "resolve_for_session_manifest",
        resolve_for_session_manifest,
    )

    effective = service.vm_agent_admin.effective_tool_policy_payload(session)

    assert effective is not None
    assert effective["tool_summary"]["session_id"] == "agent-session"
    run_script_summary = next(
        item
        for item in effective["tool_summary"]["available_tools"]
        if item["tool_name"] == ToolName.RUN_SCRIPT.value
    )
    assert run_script_summary["requires_approval"] is True
    assert "approval_settings" in run_script_summary
    assert resolve_calls == ["agent-session"]


def test_update_admin_agent_updates_chat_allowlist(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="agent-13304",
            vm_id="vm-13304",
            allowed_tools=[ToolName.CLAIM_NEXT_ISSUE],
            expires_at=datetime.now(UTC) + timedelta(hours=2),
        ),
    )

    result = service.vm_agent_admin.update_admin_agent(
        "agent-13304",
        payload={"teams_chat_allowlist": [" chat:chat-4 ", "chat:chat-4"]},
    )

    updated = service.runtime.session_repository.get_session("agent-13304")
    assert result["status"] == "updated"
    assert updated is not None
    assert updated.teams_chat_allowlist == ["chat:chat-4"]


def test_set_session_model_updates_manifest(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="issue-13303-session",
            vm_id="vm-13303",
            allowed_tools=[ToolName.CLAIM_NEXT_ISSUE],
            expires_at=datetime.now(UTC) + timedelta(hours=2),
        ),
    )

    result = service.operations_admin.set_session_model("13303", "  gpt-5.5  ")

    updated = service.runtime.session_repository.get_session("issue-13303-session")
    assert result.success is True
    assert result.session_id == "issue-13303-session"
    assert updated is not None
    assert updated.model == "gpt-5.5"


def test_set_session_model_rejects_empty_value(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="issue-13304-session",
            vm_id="vm-13304",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=2),
        ),
    )

    with pytest.raises(ValueError, match="model must be non-empty"):
        service.operations_admin.set_session_model("13304", "   ")


def test_list_background_workers_reads_systemd_state(tmp_path: Path, monkeypatch) -> None:
    service = build_admin_runtime(write_config(tmp_path))

    payloads = {
        "evander-ado-worker.service": {
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
        "evander-proxy-issue-sync.service": {
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
        "evander-discord-ingest.service": {
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
        "evander-discord-wake.service": {
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
        "evander-scheduled-wake.service": {
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
        "evander-teams-ingest.service": {
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
        "evander-teams-meeting-ingest.service": {
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
        "evander-effect-orchestrator.service": {
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
        "evander-trigger-orchestrator.service": {
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
    monkeypatch.setattr(worker_control_service, "_systemctl_show", lambda unit: payloads[unit])

    summaries = service.operations_admin.list_background_workers()

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
    service = build_admin_runtime(write_config(tmp_path))

    def _raise(unit: str) -> dict[str, str]:
        raise RuntimeError(f"cannot inspect {unit}")

    monkeypatch.setattr(worker_control_service, "_systemctl_show", _raise)

    summaries = service.operations_admin.list_background_workers()

    assert all(summary.active_state == "unavailable" for summary in summaries)
    assert all(summary.running is False for summary in summaries)
    assert all(summary.failed is False for summary in summaries)


def test_get_background_worker_log_snapshot_returns_unit_journal(
    tmp_path: Path, monkeypatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    monkeypatch.setattr(
        worker_control_service,
        "_journalctl_tail",
        lambda unit, lines: f"{unit} log tail ({lines})",
    )

    text = service.operations_admin.get_background_worker_log_snapshot("ado", lines=25)

    assert "Worker: ADO Worker" in text
    assert "Unit: evander-ado-worker.service" in text
    assert "evander-ado-worker.service log tail (25)" in text


def test_start_background_worker_controls_systemd_unit(tmp_path: Path, monkeypatch) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    calls: list[tuple[str, str]] = []

    class _Completed:
        returncode = 0
        stdout = "started"
        stderr = ""

    def _fake_control(action: str, unit: str):
        calls.append((action, unit))
        return _Completed()

    monkeypatch.setattr(worker_control_service, "_run_systemd_control", _fake_control)

    result = service.operations_admin.start_background_worker("ado")

    assert calls == [("start", "evander-ado-worker.service")]
    assert result.success is True
    assert result.worker_id == "ado"
    assert result.action == "start"
    assert result.message == "started"


def test_stop_background_worker_controls_systemd_unit(tmp_path: Path, monkeypatch) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    calls: list[tuple[str, str]] = []

    class _Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_control(action: str, unit: str):
        calls.append((action, unit))
        return _Completed()

    monkeypatch.setattr(worker_control_service, "_run_systemd_control", _fake_control)

    result = service.operations_admin.stop_background_worker("discord-wake")

    assert calls == [("stop", "evander-discord-wake.service")]
    assert result.success is True
    assert result.worker_id == "discord-wake"
    assert result.action == "stop"
    assert "Stopped Discord Wake Worker" == result.message


def test_background_worker_actions_reject_unknown_worker(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))

    start = service.operations_admin.start_background_worker("missing")
    stop = service.operations_admin.stop_background_worker("missing")

    assert start.success is False
    assert stop.success is False
    assert start.message == "Unknown worker: missing"
    assert stop.message == "Unknown worker: missing"


def test_issue_actions_use_local_behavior_for_non_proxy_issue(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    issue = service.issue_git_admin.create_local_issue(title="Local", body="only local")

    comment_result = service.issue_git_admin.post_issue_comment(issue.issue_number, "hello local")
    update_result = service.issue_git_admin.update_issue(
        issue.issue_number, state="Completed", fields={"priority": "high"}
    )
    read_result = service.issue_git_admin.read_issue(issue.issue_number)

    assert comment_result["source_type"] == "local"
    assert update_result["source_type"] == "local"
    assert read_result["source_type"] == "local"
    assert read_result["state"] == "Completed"
    assert read_result["comments"][-1]["text"] == "hello local"


def test_set_issue_plan_updates_local_issue_metadata_and_yaml(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    issue = service.issue_git_admin.create_local_issue(title="Planned", body="only local")

    result = service.issue_git_admin.set_issue_plan(
        issue.issue_number,
        ["Analyze repo", "Implement fix", "Verify tests"],
        summary="Understand the repo, make the fix, and verify the outcome.",
    )
    read_result = service.issue_git_admin.read_issue(issue.issue_number)
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
    service = build_admin_runtime(write_config(tmp_path))
    issue = service.issue_git_admin.create_local_issue(title="Planned", body="only local")

    with pytest.raises(ValueError, match="steps must contain at least one non-empty item"):
        service.issue_git_admin.set_issue_plan(issue.issue_number, ["", "   "])


def test_set_issue_plan_clears_existing_summary_when_omitted(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    issue = service.issue_git_admin.create_local_issue(title="Planned", body="only local")

    service.issue_git_admin.set_issue_plan(
        issue.issue_number, ["Analyze repo"], summary="Original summary"
    )
    result = service.issue_git_admin.set_issue_plan(issue.issue_number, ["Implement fix"])
    read_result = service.issue_git_admin.read_issue(issue.issue_number)
    yaml_text = Path(issue.yaml_path).read_text(encoding="utf-8")

    assert result["summary"] is None
    assert "plan_summary" not in read_result["metadata"]
    assert "plan_summary:" not in yaml_text


def test_workflow_operations_are_not_exposed_on_worker_control_boundaries(
    tmp_path: Path,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))

    assert hasattr(service.workflow_config_admin, "clear_workflow_run")
    assert not hasattr(service.worker_control_admin, "clear_workflow_run")
    assert not hasattr(service.vm_inventory_admin, "clear_workflow_run")


def test_get_issue_returns_full_local_record_for_closed_issue(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    issue = service.issue_git_admin.create_local_issue(title="Closed task", body="only local")
    service.issue_git_admin.update_issue(
        issue.issue_number, state="Closed", fields={"Reason": "Out of scope"}
    )

    result = service.issue_git_admin.get_issue(issue.issue_number)

    assert result["issue_id"] == issue.issue_id
    assert result["issue_number"] == issue.issue_number
    assert result["source_type"] == "local"
    assert result["directory_path"] == "root"
    assert result["state"] == "Closed"
    assert result["reason"] == "Out of scope"
    assert result["rejection_reason"] == "Out of scope"
    assert result["yaml_path"] == issue.yaml_path


def test_create_local_issue_defaults_to_new_state(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))

    issue = service.issue_git_admin.create_local_issue(title="Local", body="only local")

    stored = service.local_issue_store.get(issue.issue_id)
    assert stored is not None
    assert stored.state == "New"


def test_update_issue_rejects_invalid_local_state(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    issue = service.issue_git_admin.create_local_issue(title="Local", body="only local")

    with pytest.raises(ValueError, match="Invalid issue state"):
        service.issue_git_admin.update_issue(issue.issue_number, state="Done")


def test_update_issue_accepts_approved_state(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    issue = service.issue_git_admin.create_local_issue(title="Local", body="only local")

    result = service.issue_git_admin.update_issue(
        issue.issue_number, state="Approved", fields={"Priority": "P1"}
    )

    stored = service.local_issue_store.get(issue.issue_id)
    assert result["source_type"] == "local"
    assert stored is not None
    assert stored.state == "Approved"
    assert stored.metadata["fields"]["Priority"] == "P1"


def test_update_issue_updates_local_yaml_file(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    issue = service.issue_git_admin.create_local_issue(title="Task", body="only local")
    original_yaml = Path(issue.yaml_path).read_text(encoding="utf-8")

    result = service.issue_git_admin.update_issue(
        issue.issue_number, state="Committed", fields={"Priority": "P1"}
    )

    updated_yaml = Path(issue.yaml_path).read_text(encoding="utf-8")
    assert result["source_type"] == "local"
    assert updated_yaml != original_yaml
    assert "state: Committed" in updated_yaml
    assert "Priority: P1" in updated_yaml


def test_issue_actions_reject_ambiguous_bare_number_across_directories(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    created = service.directory_admin.create_child(parent="root", name="team")
    assert created.ok is True and created.data is not None
    team_path = created.data.details.directory.path

    root_yaml = tmp_path / "root-issue.yaml"
    root_yaml.write_text("issue_number: 7\ntitle: Root issue\nbody: Root body\n", encoding="utf-8")
    team_yaml = tmp_path / "team-issue.yaml"
    team_yaml.write_text("issue_number: 7\ntitle: Team issue\nbody: Team body\n", encoding="utf-8")

    root_issue = service.issue_git_admin.create_local_issue(
        yaml_path=str(root_yaml), directory_path="root"
    )
    team_issue = service.issue_git_admin.create_local_issue(
        yaml_path=str(team_yaml), directory_path=team_path
    )

    with pytest.raises(ValueError, match=r"Ambiguous local issue identifier: 7\."):
        service.issue_git_admin.read_issue("7")
    with pytest.raises(ValueError, match=r"Ambiguous local issue identifier: 7\."):
        service.issue_git_admin.update_issue("7", state="Completed")
    with pytest.raises(ValueError, match=r"Ambiguous local issue identifier: 7\."):
        service.issue_git_admin.update_local_issue(
            "7",
            title="Edited through bare number",
            body="Edited body",
            state="In Progress",
        )

    root_read = service.issue_git_admin.read_issue(root_issue.issue_id)
    team_updated = service.issue_git_admin.update_local_issue(
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
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    created = service.directory_admin.create_child(parent="root", name="team")
    assert created.ok is True and created.data is not None
    team_path = created.data.details.directory.path

    root_yaml = tmp_path / "root-issue.yaml"
    root_yaml.write_text("issue_number: 7\ntitle: Root issue\nbody: Root body\n", encoding="utf-8")
    team_yaml = tmp_path / "team-issue.yaml"
    team_yaml.write_text("issue_number: 7\ntitle: Team issue\nbody: Team body\n", encoding="utf-8")

    service.issue_git_admin.create_local_issue(yaml_path=str(root_yaml), directory_path="root")
    service.issue_git_admin.create_local_issue(yaml_path=str(team_yaml), directory_path=team_path)

    create_result = service.vm_agent_admin.create_issue_vm_from_local_issue("7")
    delete_result = service.issue_git_admin.delete_local_issue("7")

    assert create_result.success is False
    assert delete_result.success is False
    assert create_result.message.startswith("Ambiguous local issue identifier: 7.")
    assert delete_result.message.startswith("Ambiguous local issue identifier: 7.")


def test_create_issue_vm_rejects_ambiguous_bare_local_issue_number_in_primary_path(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    created = service.directory_admin.create_child(parent="root", name="team")
    assert created.ok is True and created.data is not None
    team_path = created.data.details.directory.path

    root_yaml = tmp_path / "root-issue.yaml"
    root_yaml.write_text("issue_number: 7\ntitle: Root issue\nbody: Root body\n", encoding="utf-8")
    team_yaml = tmp_path / "team-issue.yaml"
    team_yaml.write_text("issue_number: 7\ntitle: Team issue\nbody: Team body\n", encoding="utf-8")

    service.issue_git_admin.create_local_issue(yaml_path=str(root_yaml), directory_path="root")
    service.issue_git_admin.create_local_issue(yaml_path=str(team_yaml), directory_path=team_path)

    def _fail_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError(
            "create_issue_vm should reject ambiguous identifiers before invoking setup"
        )

    monkeypatch.setattr("evander.admin.services.process_helpers.subprocess.run", _fail_run)

    result = service.vm_agent_admin.create_issue_vm(issue="7")

    assert result.success is False
    assert result.message.startswith("Ambiguous local issue identifier: 7.")


def test_claim_next_issue_prefers_highest_priority_committed_issue(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    low = service.issue_git_admin.create_local_issue(title="Low", body="x")
    high = service.issue_git_admin.create_local_issue(title="High", body="x")
    service.issue_git_admin.update_issue(
        low.issue_number, state="Committed", fields={"Priority": "P2"}
    )
    service.issue_git_admin.update_issue(
        high.issue_number, state="Committed", fields={"Priority": "P0"}
    )
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="worker-session",
            vm_id="issue-worker-1-vm",
            directory_path="root",
            allowed_tools=[ToolName.CLAIM_NEXT_ISSUE],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    claimed = service.issue_git_admin.claim_next_issue(session_id="worker-session")

    assert claimed is not None
    assert claimed.issue_number == high.issue_number
    assert claimed.state == "In Progress"
    assert claimed.metadata["fields"]["claimed_by_session_id"] == "worker-session"
    assert claimed.metadata["fields"]["claimed_by_vm_id"] == "issue-worker-1-vm"


def test_complete_issue_marks_claimed_issue_completed(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    issue = service.issue_git_admin.create_local_issue(title="Task", body="x")
    service.issue_git_admin.update_issue(
        issue.issue_number, state="Committed", fields={"Priority": "P1"}
    )
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="worker-session",
            vm_id="issue-worker-1-vm",
            directory_path="root",
            allowed_tools=[ToolName.CLAIM_NEXT_ISSUE, ToolName.COMPLETE_ISSUE],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    claimed = service.issue_git_admin.claim_next_issue(session_id="worker-session")
    assert claimed is not None

    completed = service.issue_git_admin.complete_issue(
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
    service = build_admin_runtime(write_config(tmp_path))
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

    created = service.issue_git_admin.create_local_issue(yaml_path=str(first_yaml))
    updated = service.issue_git_admin.create_local_issue(yaml_path=str(updated_yaml))

    assert created.issue_number == "42"
    assert updated.issue_number == "42"
    issues = service.issue_git_admin.list_local_issues()
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
    service = build_admin_runtime(write_config(tmp_path))
    issue = service.issue_git_admin.create_local_issue(
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

    read_result = service.issue_git_admin.read_issue(
        issue.issue_number, include_remote_comments=True, sync_proxy_cache=True
    )
    comment_result = service.issue_git_admin.post_issue_comment(
        issue.issue_number, "push to remote", sync_proxy_cache=True
    )
    update_result = service.issue_git_admin.update_issue(
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
    service = build_admin_runtime(write_config(tmp_path))
    issue = service.issue_git_admin.create_local_issue(
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

    result = service.issue_git_admin.get_issue(
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
    service = build_admin_runtime(write_config(tmp_path))
    project_a_issue = service.issue_git_admin.create_local_issue(
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
    project_b_issue = service.issue_git_admin.create_local_issue(
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
        service.issue_git_admin.read_issue("proxy:ado/42")
    with pytest.raises(ValueError, match=r"Ambiguous proxy issue identifier: proxy:ado/42\."):
        service.issue_git_admin.update_issue("proxy:ado/42", state="Resolved")
    with pytest.raises(ValueError, match=r"Ambiguous proxy issue identifier: proxy:ado/42\."):
        service.issue_git_admin.post_issue_comment("proxy:ado/42", "push to remote")

    assert (
        service.local_issue_identity_resolver.resolve_record(project_a_issue.issue_id).issue_id
        == project_a_issue.issue_id
    )
    assert (
        service.local_issue_identity_resolver.resolve_record(project_b_issue.issue_id).issue_id
        == project_b_issue.issue_id
    )


def test_issue_actions_reject_unknown_proxy_identifier(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))

    with pytest.raises(ValueError, match=r"Unknown local issue identifier: proxy:ado/999"):
        service.issue_git_admin.read_issue("proxy:ado/999")
    with pytest.raises(ValueError, match=r"Unknown local issue identifier: proxy:ado/999"):
        service.issue_git_admin.update_issue("proxy:ado/999", state="Resolved")
    with pytest.raises(ValueError, match=r"Unknown local issue identifier: proxy:ado/999"):
        service.issue_git_admin.post_issue_comment("proxy:ado/999", "push to remote")


def test_create_issue_vm_accepts_unified_proxy_identifier(tmp_path: Path, monkeypatch: Any) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.issue_git_admin.create_local_issue(
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

    monkeypatch.setattr("evander.admin.services.process_helpers.subprocess.run", _fake_run)

    result = service.vm_agent_admin.create_issue_vm(issue="proxy:ado/77", model="gpt-5.2-codex")

    assert result.success is True
    assert any(str(part).endswith("setup_vm.sh") for part in captured["cmd"])
    assert "vm-77" in captured["cmd"]
    assert captured["cmd"][-1] == "agent"


def test_create_issue_vm_from_local_issue_rejects_unknown_proxy_identifier_without_setup(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = build_admin_runtime(write_config(tmp_path))

    def _fail_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError(
            "create_issue_vm_from_local_issue should reject unknown identifiers before setup"
        )

    monkeypatch.setattr("evander.admin.services.process_helpers.subprocess.run", _fail_run)

    result = service.vm_agent_admin.create_issue_vm_from_local_issue("proxy:ado/999")

    assert result.success is False
    assert result.message == "Unknown local issue identifier: proxy:ado/999"


def test_create_issue_vm_from_local_issue_accepts_unified_proxy_identifier(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.issue_git_admin.create_local_issue(
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

    monkeypatch.setattr("evander.admin.services.process_helpers.subprocess.run", _fake_run)

    result = service.vm_agent_admin.create_issue_vm_from_local_issue(
        "proxy:ado/77", model="gpt-5.2-codex"
    )

    assert result.success is True
    assert any(str(part).endswith("setup_vm.sh") for part in captured["cmd"])
    assert "vm-77" in captured["cmd"]
    assert captured["cmd"][-1] == "agent"


def test_create_issue_vm_from_local_issue_rejects_ambiguous_proxy_identifier(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.issue_git_admin.create_local_issue(
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
    service.issue_git_admin.create_local_issue(
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
        raise AssertionError(
            "create_issue_vm_from_local_issue should reject ambiguous identifiers before setup"
        )

    monkeypatch.setattr("evander.admin.services.process_helpers.subprocess.run", _fail_run)

    result = service.vm_agent_admin.create_issue_vm_from_local_issue("proxy:ado/42")

    assert result.success is False
    assert result.message.startswith("Ambiguous proxy issue identifier: proxy:ado/42.")


def test_create_vm_launches_generic_vm_and_bootstraps_selected_toolchains(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    created = service.directory_admin.create_child(parent="root", name="team")
    assert created.ok is True and created.data is not None
    team_path = created.data.details.directory.path
    write_result = service.operations_admin.write_directory_domain_config(
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
    monkeypatch.setenv("HOST_CLAUDE_BIN", "/opt/claude/bin/claude")
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.dominfo_field", lambda vm_name, field_name: None
    )
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.resolve_vm_ip", lambda vm_name: "192.168.250.10"
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

    monkeypatch.setattr("evander.admin.services.process_helpers.subprocess.run", _fake_run)

    result = service.vm_agent_admin.create_vm(
        vm_name="lab-vm",
        session_id="lab-session",
        labels={"workflow": "issue-worker", "agent_role": "issue"},
        metadata={"workflow": {"state": "active"}, "labels": {"priority": "high"}},
        model_provider_mode="local_openai",
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
        and "HOST_CLAUDE_BIN" in part
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
    metadata = dict(manifest.metadata)
    mcp_session_auth = metadata.pop("mcp_session_auth")
    assert setup_env["EVANDER_MCP_SESSION_TOKEN"] == mcp_session_auth["token"]
    assert mcp_session_auth["token_sha256"]
    assert metadata == {
        "model_provider_mode": "local_openai",
        "workflow": {"state": "active"},
        "labels": {
            "workflow": "issue-worker",
            "agent_role": "issue",
            "priority": "high",
        },
    }
    assert result.message == "ok:setup_vm.sh"


def test_create_vm_uses_ttl_seconds_for_session_expiry(tmp_path: Path, monkeypatch: Any) -> None:
    service = build_admin_runtime(write_config(tmp_path))

    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.dominfo_field", lambda vm_name, field_name: None
    )

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout="ok:setup_vm.sh", stderr=""
        )

    monkeypatch.setattr("evander.admin.services.process_helpers.run_subprocess", _fake_run)

    before = datetime.now(UTC)
    result = service.vm_agent_admin.create_vm(
        vm_name="lab-vm",
        session_id="lab-session",
        ttl_seconds=90,
    )
    after = datetime.now(UTC)

    assert result.success is True
    manifest = service.runtime.session_repository.get_session("lab-session")
    assert manifest is not None
    assert before + timedelta(seconds=90) <= manifest.expires_at
    assert manifest.expires_at <= after + timedelta(seconds=90)


def test_effect_agent_create_passes_selected_provider_and_libvirt_base_image_as_setup_args(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    created = service.directory_admin.create_child(parent="root", name="team")
    assert created.ok is True and created.data is not None
    team_path = created.data.details.directory.path
    write_result = service.operations_admin.write_directory_domain_config(
        team_path,
        "vm",
        {
            "libvirt_vm_default_template": "default",
            "libvirt_vm_templates": {
                "default": "ubuntu-24.04-server-cloudimg-amd64.img",
                "dotnet": "dotnet-agent.qcow2",
            },
        },
    )
    assert write_result.ok is True

    monkeypatch.setenv("SSH_PUBLIC_KEY_FILE", "/home/test-user/.ssh/gh_id_ed25519.pub")
    monkeypatch.setenv("SSH_PRIVATE_KEY_PATH", "/home/test-user/.ssh/gh_id_ed25519")
    monkeypatch.setenv("HOST_CLAUDE_BIN", "/opt/claude/bin/claude")
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.dominfo_field", lambda vm_name, field_name: None
    )
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.resolve_vm_ip", lambda vm_name: "192.168.250.10"
    )
    captured: dict[str, Any] = {}

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env")
        script_name = next(
            (Path(part).name for part in cmd if part.endswith(".sh")), Path(cmd[0]).name
        )
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout=f"ok:{script_name}", stderr=""
        )

    monkeypatch.setattr("evander.admin.services.process_helpers.subprocess.run", _fake_run)

    factory = EffectProviderFactory(
        provider_config_resolver=ProviderConfigResolver(service.config), admin_service=service
    )
    effect = AgentCreateEffectDefinition(
        id="effect-agent",
        display_name="Create agent",
        type="effect:agent:create",
        execution_backend="libvirt_vm",
        agent_provider="claude",
        vm_name="lab-vm",
        session_id="lab-session",
        vm_image="dotnet",
        directory_path=team_path,
    )

    result = factory.build(effect).execute()

    assert result["vm_image"] == "dotnet-agent.qcow2"
    assert result["vm_image_alias"] == "dotnet"
    assert any(str(part).endswith("setup_vm.sh") for part in captured["cmd"])
    assert captured["cmd"][:2] == ["sudo", "-n"]
    preserve_env_args = [part for part in captured["cmd"] if part.startswith("--preserve-env=")]
    assert all("AGENT_PROVIDER" not in part for part in preserve_env_args)
    assert all("LIBVIRT_BASE_IMAGE" not in part for part in preserve_env_args)
    assert any("HOST_CLAUDE_BIN" in part for part in preserve_env_args)
    setup_script_index = next(
        index for index, part in enumerate(captured["cmd"]) if part.endswith("setup_vm.sh")
    )
    setup_args = captured["cmd"][setup_script_index + 1 :]
    assert setup_args[:4] == [
        "--agent-provider",
        "claude",
        "--libvirt-base-image",
        "dotnet-agent.qcow2",
    ]
    setup_env = captured["env"]
    assert setup_env is not None
    assert setup_env["AGENT_PROVIDER"] == "claude"
    assert setup_env["AGENT_MODEL"] == "claude-sonnet-4-6"
    assert setup_env["LIBVIRT_BASE_IMAGE"] == "dotnet-agent.qcow2"
    manifest = service.runtime.session_repository.get_session("lab-session")
    assert manifest is not None
    assert manifest.model == "claude-sonnet-4-6"
    assert manifest.metadata["libvirt_vm_template"] == {
        "image_ref": "dotnet-agent.qcow2",
        "template_alias": "dotnet",
    }


def test_create_vm_falls_back_to_pkexec_when_sudo_requires_prompt(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = build_admin_runtime(write_config(tmp_path))

    monkeypatch.setenv("SSH_PUBLIC_KEY_FILE", "/home/test-user/.ssh/id_ed25519.pub")
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.dominfo_field", lambda vm_name, field_name: None
    )
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.resolve_vm_ip", lambda vm_name: "192.168.250.10"
    )
    monkeypatch.setattr(
        "evander.admin.services.process_helpers.can_prompt_for_privileges", lambda: True
    )

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

    monkeypatch.setattr("evander.admin.services.process_helpers.subprocess.run", _fake_run)

    result = service.vm_agent_admin.create_vm(vm_name="lab-vm", session_id="lab-session")

    assert result.success is True
    assert calls[0][:2] == ["sudo", "-n"]
    assert calls[1][:2] == ["pkexec", "env"]


def test_create_vm_surfaces_noninteractive_sudo_failure_without_pkexec_fallback(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = build_admin_runtime(write_config(tmp_path))

    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.dominfo_field", lambda vm_name, field_name: None
    )
    monkeypatch.setattr(
        "evander.admin.services.process_helpers.can_prompt_for_privileges", lambda: False
    )

    calls: list[list[str]] = []

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(
            args=cmd, returncode=1, stdout="", stderr="sudo: a password is required"
        )

    monkeypatch.setattr("evander.admin.services.process_helpers.subprocess.run", _fake_run)

    result = service.vm_agent_admin.create_vm(vm_name="lab-vm", session_id="lab-session")

    assert result.success is False
    assert result.message == "Failed to create VM lab-vm: sudo: a password is required"
    assert len(calls) == 1
    assert calls[0][:2] == ["sudo", "-n"]


def test_list_vms_surfaces_shared_session_labels(tmp_path: Path, monkeypatch: Any) -> None:
    service = build_admin_runtime(write_config(tmp_path))
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

    monkeypatch.setattr("evander.admin.services.vm_helpers.virsh_lines", lambda args: ["lab-vm"])
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.dominfo_field",
        lambda vm_name, field_name: "running",
    )
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.resolve_vm_ip", lambda vm_name: "192.0.2.10"
    )
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.probe_agent_state",
        lambda vm_name, vm_ip, vm_user: (
            "idle",
            ["sample-repo"],
            datetime(2026, 4, 5, 12, 0, tzinfo=UTC),
            None,
            None,
        ),
    )

    summaries = service.vm_agent_admin.list_vms()

    assert len(summaries) == 1
    assert summaries[0].labels == {"workflow": "issue-worker", "agent_role": "issue"}


def test_kill_agent_terminates_selected_guest_pid(tmp_path: Path, monkeypatch) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.dominfo_field",
        lambda vm_name, field_name: "running",
    )
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.resolve_vm_ssh_access",
        lambda vm_name, vm_user: ("192.168.122.10", Path("/tmp/test-key")),
    )

    captured: dict[str, object] = {}

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = cmd
        captured["input"] = kwargs.get("input")
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout="terminated pid=4321 state_files=1\n", stderr=""
        )

    monkeypatch.setattr("evander.admin.services.process_helpers.run_subprocess", _fake_run)

    result = service.vm_agent_admin.kill_agent("vm-123", 4321)

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
    service = build_admin_runtime(write_config(tmp_path))
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.dominfo_field",
        lambda vm_name, field_name: "running",
    )

    def _fake_resolve_vm_ssh_access(vm_name: str, vm_user: str):
        if vm_user == "agent-admin":
            raise RuntimeError("Timed out waiting for SSH on 'agent-admin@192.168.122.10'")
        return "192.168.122.10", Path("/tmp/test-key")

    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.resolve_vm_ssh_access", _fake_resolve_vm_ssh_access
    )

    captured: dict[str, object] = {}

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout="terminated pid=4321 state_files=1\n", stderr=""
        )

    monkeypatch.setattr("evander.admin.services.process_helpers.run_subprocess", _fake_run)

    result = service.vm_agent_admin.kill_agent("vm-123", 4321)

    assert result.success is True
    assert "agent@192.168.122.10" in captured["cmd"]


_MANAGED_REPO_ID = "mr_0123456789abcdefabcd"


def _stub_vm_managed_repository(service: Any, monkeypatch: Any) -> None:
    managed_git = service.vm_agent_admin._deps.managed_git_admin
    monkeypatch.setattr(
        managed_git,
        "clone_info",
        lambda managed_repo_id, **kwargs: {
            "managed_repo_id": managed_repo_id,
            "display_name": "sample-repo",
            "default_branch": "main",
        },
    )
    monkeypatch.setattr(
        managed_git,
        "resolve_internal_repository",
        lambda managed_repo_id, directory_path: (
            Path(f"/managed-git/{managed_repo_id}.git"),
            Path("/managed-git"),
        ),
    )
    monkeypatch.setattr(managed_git, "bind", lambda *args, **kwargs: None)

    def _run_with_managed_access_metadata(
        cmd: list[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        completed = process_helpers.subprocess.run(cmd, **kwargs)
        if cmd and cmd[0].endswith("bootstrap_git_vm.sh") and completed.returncode == 0:
            return subprocess.CompletedProcess(
                args=completed.args,
                returncode=0,
                stdout=(
                    "EVANDER_GIT_PUBLIC_KEY_BODY=ssh-ed25519 AAAATestManagedKey\n"
                    "EVANDER_GIT_AUTHORIZED_KEYS_PATH=/tmp/evander-test-authorized-keys\n"
                    "EVANDER_GIT_AUTH_ACCESS=write\n"
                ),
                stderr=completed.stderr,
            )
        return completed

    monkeypatch.setattr(process_helpers, "run_subprocess", _run_with_managed_access_metadata)


def test_clone_repo_into_vm_clones_managed_repo(tmp_path: Path, monkeypatch: Any) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    _stub_vm_managed_repository(service, monkeypatch)
    captured_runs: list[tuple[list[str], dict[str, str] | None]] = []

    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.dominfo_field", lambda vm_name, field_name: None
    )
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.resolve_vm_ip", lambda vm_name: "192.168.250.10"
    )
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.resolve_private_key_path",
        lambda: Path("/tmp/test-key"),
    )
    monkeypatch.setattr(
        "evander.admin.services.process_helpers.resolve_host_python_with_pip",
        lambda: str(REPO_ROOT / ".venv" / "bin" / "python"),
    )

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured_runs.append((cmd, kwargs.get("env")))
        if cmd == ["git", "-C", "/src/repo", "config", "--get", "branch.main.remote"]:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="origin\n", stderr="")
        if cmd[:5] == ["git", "-C", "/src/repo", "symbolic-ref", "--quiet"]:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="origin/main\n", stderr=""
            )
        if cmd[:4] == ["git", "-C", "/src/repo", "branch"]:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="main\n", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("evander.admin.services.process_helpers.subprocess.run", _fake_run)

    service.vm_agent_admin.create_vm(
        vm_name="lab-vm",
        session_id="lab-session",
    )
    result = service.vm_agent_admin.clone_repo_into_vm(
        vm_name="lab-vm",
        session_id="lab-session",
        managed_repo_id=_MANAGED_REPO_ID,
    )

    assert result.success is True
    captured_cmds = [cmd for cmd, _env in captured_runs]
    assert not any(cmd[0].endswith("create_git_remote.sh") for cmd in captured_cmds)
    assert any(
        cmd[0].endswith("bootstrap_git_vm.sh") and cmd[1:] == ["lab-vm", "agent"]
        for cmd in captured_cmds
    )
    bootstrap_env = next(
        env for cmd, env in captured_runs if cmd[0].endswith("bootstrap_git_vm.sh")
    )
    assert bootstrap_env is not None
    assert bootstrap_env["EVANDER_GIT_REPO"] == _MANAGED_REPO_ID
    assert bootstrap_env["EVANDER_GIT_ROOT"] == "/managed-git"
    assert bootstrap_env["EVANDER_GIT_SESSION_ID"] == "lab-session"
    assert any(
        cmd[0] == "ssh" and cmd[-3:] == ["agent@192.168.250.10", "bash", "-s"]
        for cmd in captured_cmds
    )


def test_clone_repo_into_vm_installs_agent_push_and_pr_note_helpers(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    _stub_vm_managed_repository(service, monkeypatch)
    captured_ssh_input: list[str] = []

    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.dominfo_field", lambda vm_name, field_name: None
    )
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.resolve_vm_ip", lambda vm_name: "192.168.250.10"
    )
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.resolve_private_key_path",
        lambda: Path("/tmp/test-key"),
    )

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if cmd[:5] == ["git", "-C", "/src/repo", "symbolic-ref", "--quiet"]:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="origin/main\n", stderr=""
            )
        if cmd[:4] == ["git", "-C", "/src/repo", "branch"]:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="main\n", stderr="")
        if cmd[0] == "ssh":
            captured_ssh_input.append(str(kwargs.get("input") or ""))
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("evander.admin.services.process_helpers.subprocess.run", _fake_run)

    service.vm_agent_admin.create_vm(
        vm_name="lab-vm",
        session_id="issue-14724-lab-session",
    )
    result = service.vm_agent_admin.clone_repo_into_vm(
        vm_name="lab-vm",
        session_id="issue-14724-lab-session",
        managed_repo_id=_MANAGED_REPO_ID,
    )

    assert result.success is True
    assert captured_ssh_input
    remote_script = captured_ssh_input[-1]
    assert "config alias.agent-pr-note" in remote_script
    assert "config alias.push-agent" in remote_script
    assert "config alias.sync-base" in remote_script
    assert 'AGENT_PUSH_HELPER_B64="' in remote_script
    assert 'SYNC_BASE_HELPER_B64="' in remote_script
    assert ".evander/bin/push-agent" in remote_script
    assert ".evander/bin/sync-base" in remote_script
    assert "config evander.prNotesRef" in remote_script
    assert "config evander.branchSuffix" in remote_script
    assert "config evander.workBranch" in remote_script


def test_clone_repo_into_vm_generated_script_allows_existing_checkout(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    _stub_vm_managed_repository(service, monkeypatch)
    captured_ssh_input: list[str] = []

    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.dominfo_field", lambda vm_name, field_name: None
    )
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.resolve_vm_ip", lambda vm_name: "192.168.250.10"
    )
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.resolve_private_key_path",
        lambda: Path("/tmp/test-key"),
    )

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if cmd[:5] == ["git", "-C", "/src/repo", "symbolic-ref", "--quiet"]:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="origin/main\n", stderr=""
            )
        if cmd[:4] == ["git", "-C", "/src/repo", "branch"]:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="main\n", stderr="")
        if cmd[0] == "ssh":
            captured_ssh_input.append(str(kwargs.get("input") or ""))
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("evander.admin.services.process_helpers.subprocess.run", _fake_run)

    service.vm_agent_admin.create_vm(
        vm_name="lab-vm",
        session_id="issue-14724-lab-session",
    )
    result = service.vm_agent_admin.clone_repo_into_vm(
        vm_name="lab-vm",
        session_id="issue-14724-lab-session",
        managed_repo_id=_MANAGED_REPO_ID,
    )

    assert result.success is True
    remote_script = captured_ssh_input[-1]
    assert 'if [[ ! -d "${TARGET_DIR}/.git" ]]; then' in remote_script
    assert "REPO_ALREADY_PREPARED=1" in remote_script
    assert "Guest repo path already exists" not in remote_script
    assert 'remote set-url agent "${REMOTE_URL}"' in remote_script
    assert 'if [[ "${REPO_ALREADY_PREPARED}" == "0" ]]; then' in remote_script
    assert 'config evander.workBranch "${CURRENT_BRANCH}"' in remote_script


def test_clone_repo_into_vm_configures_separate_author_and_committer(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    _stub_vm_managed_repository(service, monkeypatch)
    captured_ssh_input: list[str] = []

    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.dominfo_field", lambda vm_name, field_name: None
    )
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.resolve_vm_ip", lambda vm_name: "192.168.250.10"
    )
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.resolve_private_key_path",
        lambda: Path("/tmp/test-key"),
    )

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if cmd[:5] == ["git", "-C", "/src/repo", "symbolic-ref", "--quiet"]:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="origin/main\n", stderr=""
            )
        if cmd[:4] == ["git", "-C", "/src/repo", "branch"]:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="main\n", stderr="")
        if cmd[0] == "ssh":
            captured_ssh_input.append(str(kwargs.get("input") or ""))
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("evander.admin.services.process_helpers.subprocess.run", _fake_run)

    service.vm_agent_admin.create_vm(
        vm_name="lab-vm",
        session_id="issue-14724-lab-session",
    )
    result = service.vm_agent_admin.clone_repo_into_vm(
        vm_name="lab-vm",
        session_id="issue-14724-lab-session",
        managed_repo_id=_MANAGED_REPO_ID,
        git_author_name="Ada Lovelace",
        git_author_email="ada@example.com",
        git_committer_name="Truman Bot",
        git_committer_email="truman@example.com",
    )

    assert result.success is True
    remote_script = captured_ssh_input[-1]
    assert "GIT_USER_NAME='Ada Lovelace'" in remote_script
    assert "GIT_USER_EMAIL=ada@example.com" in remote_script
    assert "GIT_AUTHOR_NAME='Ada Lovelace'" in remote_script
    assert "GIT_AUTHOR_EMAIL=ada@example.com" in remote_script
    assert "GIT_COMMITTER_NAME='Truman Bot'" in remote_script
    assert "GIT_COMMITTER_EMAIL=truman@example.com" in remote_script
    assert 'git -C "${TARGET_DIR}" config user.name "${GIT_USER_NAME}"' in remote_script
    assert 'git -C "${TARGET_DIR}" config author.name "${GIT_AUTHOR_NAME}"' in remote_script
    assert 'git -C "${TARGET_DIR}" config author.email "${GIT_AUTHOR_EMAIL}"' in remote_script
    assert 'git -C "${TARGET_DIR}" config committer.name "${GIT_COMMITTER_NAME}"' in remote_script
    assert 'git -C "${TARGET_DIR}" config committer.email "${GIT_COMMITTER_EMAIL}"' in remote_script
    assert 'config evander.gitAuthorName "${GIT_AUTHOR_NAME}"' in remote_script
    assert 'config evander.gitCommitterName "${GIT_COMMITTER_NAME}"' in remote_script


def test_clone_repo_into_vm_honors_clone_branch_and_records_base_branch(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    _stub_vm_managed_repository(service, monkeypatch)
    captured_ssh_input: list[str] = []

    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.dominfo_field", lambda vm_name, field_name: None
    )
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.resolve_vm_ip", lambda vm_name: "192.168.250.10"
    )
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.resolve_private_key_path",
        lambda: Path("/tmp/test-key"),
    )

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if cmd[:5] == ["git", "-C", "/src/repo", "symbolic-ref", "--quiet"]:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="origin/main\n", stderr=""
            )
        if cmd[:4] == ["git", "-C", "/src/repo", "branch"]:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="main\n", stderr="")
        if cmd[0] == "ssh":
            captured_ssh_input.append(str(kwargs.get("input") or ""))
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("evander.admin.services.process_helpers.subprocess.run", _fake_run)

    service.vm_agent_admin.create_vm(
        vm_name="lab-vm",
        session_id="issue-14724-lab-session",
    )
    result = service.vm_agent_admin.clone_repo_into_vm(
        vm_name="lab-vm",
        session_id="issue-14724-lab-session",
        managed_repo_id=_MANAGED_REPO_ID,
        clone_branch="release/1.2",
    )

    assert result.success is True
    remote_script = captured_ssh_input[-1]
    assert 'CLONE_BRANCH="release/1.2"' in remote_script
    assert 'git clone --branch "${CLONE_BRANCH}" "${REMOTE_URL}" "${TARGET_DIR}"' in remote_script
    assert 'BASE_BRANCH="${CLONE_BRANCH:-}"' in remote_script
    assert 'config evander.baseBranch "${BASE_BRANCH}"' in remote_script
    assert 'git -C "${TARGET_DIR}" switch -c "${WORK_BRANCH}"' in remote_script


def test_clone_repo_into_vm_keeps_managed_clone_url_internal(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    _stub_vm_managed_repository(service, monkeypatch)
    captured_ssh_input: list[str] = []

    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.dominfo_field", lambda vm_name, field_name: None
    )
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.resolve_vm_ip", lambda vm_name: "192.168.250.10"
    )
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.resolve_private_key_path",
        lambda: Path("/tmp/test-key"),
    )

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if cmd[0] == "ssh":
            captured_ssh_input.append(str(kwargs.get("input") or ""))
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("evander.admin.services.process_helpers.subprocess.run", _fake_run)

    service.vm_agent_admin.create_vm(
        vm_name="lab-vm",
        session_id="issue-14724-lab-session",
    )
    result = service.vm_agent_admin.clone_repo_into_vm(
        vm_name="lab-vm",
        session_id="issue-14724-lab-session",
        managed_repo_id=_MANAGED_REPO_ID,
    )

    assert result.success is True
    remote_script = captured_ssh_input[-1]
    assert 'CANONICAL_REMOTE_URL=""' in remote_script
    assert (
        f'REMOTE_URL="ssh://evander-git-host/managed-git/{_MANAGED_REPO_ID}.git"' in remote_script
    )


def test_bootstrap_python_in_vm_runs_python_bootstrap(tmp_path: Path, monkeypatch: Any) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    captured_runs: list[tuple[list[str], dict[str, str] | None]] = []

    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.dominfo_field", lambda vm_name, field_name: None
    )
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.resolve_vm_ip", lambda vm_name: "192.168.250.10"
    )
    monkeypatch.setattr(
        "evander.admin.services.process_helpers.resolve_host_python_with_pip",
        lambda: str(REPO_ROOT / ".venv" / "bin" / "python"),
    )

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured_runs.append((cmd, kwargs.get("env")))
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("evander.admin.services.process_helpers.subprocess.run", _fake_run)

    service.vm_agent_admin.create_vm(vm_name="lab-vm", session_id="lab-session")
    result = service.vm_agent_admin.bootstrap_python_in_vm(
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
    service = build_admin_runtime(write_config(tmp_path))

    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.dominfo_field", lambda vm_name, field_name: None
    )
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.resolve_vm_ip", lambda vm_name: "192.168.250.10"
    )
    monkeypatch.setattr(
        "evander.admin.services.process_helpers.subprocess.run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout="ok", stderr=""
        ),
    )

    result = service.vm_agent_admin.create_vm(
        vm_name="lab-vm",
        session_id="lab-session",
        allowed_tools=["list_unread_feedback", "post_pr_feedback_reply"],
        tool_argument_policies={
            "create_ado_backlog_item": [
                {
                    "id": "create-policy",
                    "constraints": [{"id": "title", "path": "title", "operator": "required"}],
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


def test_create_vm_fast_forwards_discord_cursor_for_new_target(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    communication_targets = SqliteCommunicationTargetsRepository(
        communication_targets_db_path(service.config.runtime_dir)
    )
    communication_targets.write_overlay(
        "root",
        {
            "targets": [
                {
                    "id": "discord-ops",
                    "provider": "discord",
                    "kind": "discord",
                    "address": "123456789012345678",
                    "policy": {"capabilities": ["read", "write"]},
                }
            ]
        },
    )
    service.runtime.discord_inbox_service.store.add_message_if_new(
        _discord_message(
            "msg-1",
            channel_id="123456789012345678",
            created_at=datetime(2026, 6, 23, 18, 53, 43, tzinfo=UTC),
        )
    )

    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.dominfo_field", lambda vm_name, field_name: None
    )
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.resolve_vm_ip", lambda vm_name: "192.168.250.10"
    )
    monkeypatch.setattr(
        "evander.admin.services.process_helpers.subprocess.run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout="ok", stderr=""
        ),
    )

    before = datetime.now(UTC)
    result = service.vm_agent_admin.create_vm(
        vm_name="lab-vm",
        session_id="lab-session",
        discord_channels=["discord-ops"],
    )
    after = datetime.now(UTC)

    assert result.success is True
    manifest = service.runtime.session_repository.get_session("lab-session")
    assert manifest is not None
    assert manifest.discord_channel_allowlist == ["discord-ops"]
    _assert_cursor_seeded_during_operation(
        service.runtime.discord_inbox_service.store.get_session_cursor(
            "lab-session", "123456789012345678"
        ),
        before=before,
        after=after,
    )
    assert (
        service.runtime.discord_inbox_service.store.has_unread_messages(
            session_id="lab-session",
            channel_ids=["123456789012345678"],
        )
        is False
    )


def test_create_vm_upsert_validates_tool_argument_policies_for_existing_session(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="lab-session",
            allowed_tools=[ToolName.CREATE_ADO_BACKLOG_ITEM],
            expires_at=datetime.now(UTC) + timedelta(hours=2),
        )
    )

    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.dominfo_field", lambda vm_name, field_name: None
    )
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.resolve_vm_ip", lambda vm_name: "192.168.250.10"
    )
    monkeypatch.setattr(
        "evander.admin.services.process_helpers.subprocess.run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout="ok", stderr=""
        ),
    )

    result = service.vm_agent_admin.create_vm(
        vm_name="lab-vm",
        session_id="lab-session",
        allow_existing_session=True,
        tool_argument_policies={
            "create_ado_backlog_item": [
                {
                    "id": "upsert-policy",
                    "constraints": [{"id": "project", "path": "project", "operator": "required"}],
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


def test_create_vm_upsert_preserves_web_search_policy_unless_supplied(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="lab-session",
            allowed_tools=[ToolName.GET_AGENT_STATUS],
            web_search_enabled=True,
            expires_at=datetime.now(UTC) + timedelta(hours=2),
        )
    )

    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.dominfo_field", lambda vm_name, field_name: None
    )
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.resolve_vm_ip", lambda vm_name: "192.168.250.10"
    )
    monkeypatch.setattr(
        "evander.admin.services.process_helpers.subprocess.run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout="ok", stderr=""
        ),
    )

    result = service.vm_agent_admin.create_vm(
        vm_name="lab-vm",
        session_id="lab-session",
        allow_existing_session=True,
    )

    assert result.success is True
    manifest = service.runtime.session_repository.get_session("lab-session")
    assert manifest is not None
    assert manifest.web_search_enabled is True

    result = service.vm_agent_admin.create_vm(
        vm_name="lab-vm-2",
        session_id="lab-session",
        allow_existing_session=True,
        web_search_enabled=False,
    )

    assert result.success is True
    manifest = service.runtime.session_repository.get_session("lab-session")
    assert manifest is not None
    assert manifest.web_search_enabled is False


def test_create_vm_sets_teams_chat_allowlist_on_session_manifest(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = build_admin_runtime(write_config(tmp_path))

    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.dominfo_field", lambda vm_name, field_name: None
    )
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.resolve_vm_ip", lambda vm_name: "192.168.250.10"
    )
    monkeypatch.setattr(
        "evander.admin.services.process_helpers.subprocess.run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout="ok", stderr=""
        ),
    )

    result = service.vm_agent_admin.create_vm(
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
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    service.operations_admin.write_directory_domain_config(
        "root",
        "contacts",
        {
            "contacts": {
                "ops": {
                    "name": "Ops",
                    "phone_numbers": [{"label": "main", "number": "+15550001", "primary": True}],
                },
                "support": {
                    "name": "Support",
                    "phone_numbers": [{"label": "main", "number": "+15550002", "primary": True}],
                },
            }
        },
    )

    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.dominfo_field", lambda vm_name, field_name: None
    )
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.resolve_vm_ip", lambda vm_name: "192.168.250.10"
    )
    monkeypatch.setattr(
        "evander.admin.services.process_helpers.subprocess.run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout="ok", stderr=""
        ),
    )

    result = service.vm_agent_admin.create_vm(
        vm_name="lab-vm",
        session_id="lab-session",
        allowed_script_names=["deploy-review", " collect-logs ", "deploy-review"],
        allowed_contact_ids=[" ops ", "support", "ops"],
    )

    assert result.success is True
    manifest = service.runtime.session_repository.get_session("lab-session")
    assert manifest is not None
    assert manifest.allowed_script_names == ["deploy-review", "collect-logs"]
    assert manifest.allowed_contact_ids == ["ops", "support"]


def test_bootstrap_frontend_in_vm_bootstraps_frontend_runtime(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    captured_runs: list[tuple[list[str], dict[str, str] | None]] = []

    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.dominfo_field", lambda vm_name, field_name: None
    )

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured_runs.append((cmd, kwargs.get("env")))
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("evander.admin.services.process_helpers.subprocess.run", _fake_run)

    service.vm_agent_admin.create_vm(vm_name="lab-vm", session_id="lab-session")
    result = service.vm_agent_admin.bootstrap_frontend_in_vm(
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
    service = build_admin_runtime(write_config(tmp_path))
    captured_runs: list[tuple[list[str], dict[str, str] | None]] = []

    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.dominfo_field", lambda vm_name, field_name: None
    )
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.resolve_vm_ip", lambda vm_name: "192.168.250.10"
    )

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured_runs.append((cmd, kwargs.get("env")))
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("evander.admin.services.process_helpers.subprocess.run", _fake_run)

    result = service.vm_agent_admin.create_vm(
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
    service = build_admin_runtime(write_config(tmp_path))
    service.skill_admin.create_skill({"name": "managed.skill", "content": "# Managed\n"})
    captured_runs: list[tuple[list[str], dict[str, str] | None]] = []

    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.dominfo_field", lambda vm_name, field_name: None
    )
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.resolve_vm_ip", lambda vm_name: "192.168.250.10"
    )

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured_runs.append((cmd, kwargs.get("env")))
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("evander.admin.services.process_helpers.subprocess.run", _fake_run)

    result = service.vm_agent_admin.create_vm(
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
    service = build_admin_runtime(write_config(tmp_path))

    result = service.vm_agent_admin.bootstrap_frontend_in_vm(
        vm_name="lab-vm",
        host_checkout="/src/repo",
        install_roots=[],
    )

    assert result.success is False
    assert result.message == "install_roots is required."


def test_create_vm_preserves_session_when_bootstrap_fails_after_launch(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    state = {"launched": False}
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.dominfo_field",
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

    monkeypatch.setattr("evander.admin.services.process_helpers.subprocess.run", _fake_run)

    result = service.vm_agent_admin.create_vm(vm_name="lab-vm", session_id="lab-session")

    assert result.success is False
    assert result.message == (
        "Failed to create VM lab-vm: codex failed\n"
        "VM lab-vm and session lab-session were preserved for inspection."
    )
    manifest = service.runtime.session_repository.get_session("lab-session")
    assert manifest is not None
    assert manifest.vm_id == "lab-vm"


def test_create_vm_failure_preserves_stdout_and_stderr(tmp_path: Path, monkeypatch: Any) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    state = {"launched": False}
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.dominfo_field",
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

    monkeypatch.setattr("evander.admin.services.process_helpers.subprocess.run", _fake_run)

    result = service.vm_agent_admin.create_vm(
        vm_name="idea-generator-vm", session_id="idea-generator"
    )

    assert result.success is False
    assert "Bootstrapping Codex into agent@192.168.250.204" in result.message
    assert "scp: /home/agent/bin/codex: No such file or directory" in result.message
    assert "were preserved for inspection" in result.message


def test_create_vm_waits_for_ssh_before_bootstrap(tmp_path: Path, monkeypatch: Any) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.dominfo_field", lambda vm_name, field_name: None
    )
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.resolve_vm_ip", lambda vm_name: "192.168.250.154"
    )
    captured_cmds: list[list[str]] = []
    monkeypatch.setattr(
        "evander.admin.services.process_helpers.subprocess.run",
        lambda cmd, **kwargs: (
            captured_cmds.append(cmd)
            or subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")
        ),
    )

    result = service.vm_agent_admin.create_vm(
        vm_name="idea-generator-vm", session_id="idea-generator"
    )

    assert result.success is True
    assert any(any(part.endswith("setup_vm.sh") for part in cmd) for cmd in captured_cmds)


def test_resolve_host_python_with_pip_prefers_current_interpreter(monkeypatch: Any) -> None:
    monkeypatch.delenv("HOST_PYTHON_BIN", raising=False)
    monkeypatch.setattr(admin_service.sys, "executable", "/work/.venv/bin/python")

    def _fake_ensure_pip(candidate: str) -> bool:
        return candidate == "/work/.venv/bin/python"

    monkeypatch.setattr(
        "evander.admin.services.process_helpers.ensure_python_has_pip",
        _fake_ensure_pip,
    )

    assert admin_service._resolve_host_python_with_pip() == "/work/.venv/bin/python"


def test_resolve_host_python_with_pip_honors_configured_override(monkeypatch: Any) -> None:
    monkeypatch.setenv("HOST_PYTHON_BIN", "/custom/python")
    monkeypatch.setattr(
        "evander.admin.services.process_helpers.ensure_python_has_pip",
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

    monkeypatch.setattr(
        "evander.admin.services.process_helpers.ensure_python_has_pip",
        _fake_ensure,
    )

    assert admin_service._resolve_host_python_with_pip() == "/work/.venv/bin/python3"
    assert attempts == ["/work/.venv/bin/python3"]


def test_bootstrap_python_in_vm_requires_host_checkout(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))

    result = service.vm_agent_admin.bootstrap_python_in_vm(
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

    monkeypatch.setattr("evander.admin.services.vm_helpers.resolve_vm_ip", _fake_plain)
    monkeypatch.setattr("evander.admin.services.vm_helpers.resolve_vm_ip_with_pkexec", _fake_pkexec)
    monkeypatch.setattr("evander.admin.services.vm_helpers.time.sleep", lambda _seconds: None)

    ip = admin_service._wait_for_vm_ip(
        "vm-test",
        timeout_seconds=1,
        allow_pkexec=True,
    )

    assert ip == "192.168.250.10"
    assert calls == [False, True]


def test_unlock_privileged_operations_starts_helper(monkeypatch, tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
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

    monkeypatch.setattr("evander.admin.composition.PrivilegedHelperSession", FakeHelper)

    assert service.operations_admin.privileged_helper_required() is True
    assert (
        service.operations_admin.unlock_privileged_operations() == "Privileged operations unlocked."
    )
    assert service.operations_admin.privileged_operations_unlocked() is True
    assert service.privileged_helper is not None
    service.operations_admin.lock_privileged_operations()
    assert service.privileged_helper is None
    assert started == ["init", "start", "close"]


def test_privileged_helper_session_round_trips_requests(monkeypatch, tmp_path: Path) -> None:
    class FakeStdin:
        def __init__(self) -> None:
            self.writes: list[str] = []
            self.flushes = 0

        def write(self, value: str) -> None:
            self.writes.append(value)

        def flush(self) -> None:
            self.flushes += 1

    class FakeStdout:
        def __init__(self) -> None:
            self.responses = [
                json.dumps({"ok": True, "status": "pong"}) + "\n",
                json.dumps({"ok": True, "returncode": 0, "stdout": "done"}) + "\n",
                json.dumps({"ok": True}) + "\n",
            ]

        def readline(self) -> str:
            return self.responses.pop(0)

    class FakeStderr:
        def read(self) -> str:
            return ""

    class FakeProcess:
        def __init__(self) -> None:
            self.stdin = FakeStdin()
            self.stdout = FakeStdout()
            self.stderr = FakeStderr()
            self.terminated = False

        def poll(self) -> int | None:
            return None

        def terminate(self) -> None:
            self.terminated = True

    fake_process = FakeProcess()

    monkeypatch.setattr(
        operations_helpers.subprocess,
        "Popen",
        lambda *args, **kwargs: fake_process,
    )

    session = operations_helpers.PrivilegedHelperSession(
        python_executable="/usr/bin/python3",
        project_dir=tmp_path,
    )

    session.start()
    response = session.call("run_script", script_path="/tmp/script.sh", args=["--flag"])
    session.close()

    assert response == {"ok": True, "returncode": 0, "stdout": "done"}
    assert fake_process.stdin.flushes == 3
    assert [json.loads(item) for item in fake_process.stdin.writes] == [
        {"op": "ping"},
        {"op": "run_script", "script_path": "/tmp/script.sh", "args": ["--flag"]},
        {"op": "exit"},
    ]
    assert fake_process.terminated is True


def test_privileged_helper_preserves_host_claude_binary_env() -> None:
    filtered = privileged_helper._filtered_env(
        {"HOST_CLAUDE_BIN": "/opt/claude/bin/claude", "UNRELATED": "ignored"}
    )

    assert filtered["HOST_CLAUDE_BIN"] == "/opt/claude/bin/claude"
    assert "UNRELATED" not in filtered


def test_destroy_vm_prunes_generic_sessions_and_feedback_state(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
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

    monkeypatch.setattr("evander.admin.services.process_helpers.subprocess.run", _fake_run)
    monkeypatch.setattr(
        "evander.admin.services.session_helpers.prune_approvals_for_sessions",
        lambda runtime, session_ids: len(session_ids),
    )
    monkeypatch.setattr(
        "evander.admin.services.git_helpers.prune_pr_map",
        lambda path, session_ids: (len(session_ids), {"stream-a"} if session_ids else set()),
    )
    monkeypatch.setattr(
        "evander.admin.services.git_helpers.prune_pr_feedback_store",
        lambda path, streams: (len(streams), len(streams)),
    )

    result = service.vm_agent_admin.destroy_vm("lab-vm")

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
    service = build_admin_runtime(write_config(tmp_path))
    monkeypatch.setattr(
        "evander.admin.services.process_helpers.can_prompt_for_privileges", lambda: False
    )
    calls: list[list[str]] = []

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(
            args=cmd, returncode=1, stdout="", stderr="sudo: a password is required"
        )

    monkeypatch.setattr("evander.admin.services.process_helpers.subprocess.run", _fake_run)

    result = service.vm_agent_admin.destroy_vm("lab-vm")

    assert result.success is False
    assert result.message == "sudo: a password is required"
    assert len(calls) == 1
    assert calls[0][:2] == ["sudo", "-n"]


def test_destroy_vm_falls_back_to_pkexec_when_prompt_is_available(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    monkeypatch.setattr(
        "evander.admin.services.process_helpers.can_prompt_for_privileges", lambda: True
    )
    calls: list[list[str]] = []

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if cmd[:2] == ["sudo", "-n"]:
            return subprocess.CompletedProcess(
                args=cmd, returncode=1, stdout="", stderr="sudo: a password is required"
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="destroyed", stderr="")

    monkeypatch.setattr("evander.admin.services.process_helpers.subprocess.run", _fake_run)

    result = service.vm_agent_admin.destroy_vm("lab-vm")

    assert result.success is True
    assert calls[0][:2] == ["sudo", "-n"]
    assert calls[1][:2] == ["pkexec", "env"]


def test_create_and_list_wake_schedules_include_last_dispatch(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.operations_admin.create_wake_schedule(
        session_id="issue-1-session",
        prompt="Resume now",
        cron="0 6,15 * * 1-5",
        source="admin_cli",
        resume_mode="new_context",
    )
    schedules = service.operations_admin.list_wake_schedules(session_id="issue-1-session")
    assert len(schedules) == 1
    assert schedules[0]["cron"] == "0 6,15 * * 1-5"
    assert schedules[0]["resume_mode"] == "new_context"
    assert schedules[0]["last_dispatch"] is None


def test_create_and_list_wake_schedules_include_cancellation_fields(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    created = service.operations_admin.create_wake_schedule(
        session_id="issue-1-session",
        prompt="Resume later",
        resume_at=datetime.now(UTC) + timedelta(hours=1),
        source="admin_cli",
    )
    cancellation = service.runtime.wake_store.cancel_request(
        created.request_id,
        caller_session_id="issue-1-session",
        reason="No longer needed",
        metadata={"source": "admin"},
    )

    schedules = service.operations_admin.list_wake_schedules(session_id="issue-1-session")

    assert cancellation.status == "cancelled"
    assert len(schedules) == 1
    assert schedules[0]["status"] == "cancelled"
    assert schedules[0]["enabled"] is False
    assert schedules[0]["cancelled_by_session_id"] == "issue-1-session"
    assert schedules[0]["cancel_reason"] == "No longer needed"
    assert schedules[0]["cancel_metadata"] == {"source": "admin"}
    assert schedules[0]["cancelled_at"] is not None


def test_edit_wake_schedule_rejects_cancelled_schedule(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    created = service.operations_admin.create_wake_schedule(
        session_id="issue-1-session",
        prompt="Resume later",
        resume_at=datetime.now(UTC) + timedelta(hours=1),
        source="admin_cli",
    )
    service.runtime.wake_store.cancel_request(
        created.request_id,
        caller_session_id="issue-1-session",
        reason="No longer needed",
        metadata={"source": "admin"},
    )

    with pytest.raises(ValueError, match="cancelled and cannot be edited"):
        service.operations_admin.edit_wake_schedule(created.request_id, enabled=True)

    refreshed = service.runtime.wake_store.get_request(created.request_id)
    assert refreshed is not None
    assert refreshed.status == "cancelled"
    assert refreshed.enabled is False
    assert refreshed.next_run_at is None


def test_create_wake_schedule_rejects_invalid_cron(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    with pytest.raises(ValueError, match="Invalid cron expression"):
        service.operations_admin.create_wake_schedule(
            session_id="issue-1-session",
            prompt="Resume now",
            cron="* *",
        )


def test_wake_schedule_cron_validation_accepts_standard_range_syntax() -> None:
    assert admin_service._require_valid_cron(" 0 6,15 * * 1-5 ") == "0 6,15 * * 1-5"


def test_create_wake_schedule_enforces_max_active_per_session(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.config.wake_schedule_max_active_per_session = 1
    service.operations_admin.create_wake_schedule(
        session_id="issue-1-session",
        prompt="Resume now",
        cron="*/5 * * * *",
    )

    with pytest.raises(ValueError, match="max_active_per_session=1"):
        service.operations_admin.create_wake_schedule(
            session_id="issue-1-session",
            prompt="Resume again",
            cron="*/10 * * * *",
        )


def test_create_wake_schedule_enforces_max_active_total(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.config.wake_schedule_max_active_total = 1
    service.operations_admin.create_wake_schedule(
        session_id="issue-1-session",
        prompt="Resume now",
        cron="*/5 * * * *",
    )

    with pytest.raises(ValueError, match="max_active_total=1"):
        service.operations_admin.create_wake_schedule(
            session_id="issue-2-session",
            prompt="Resume another",
            cron="*/10 * * * *",
        )


def test_create_wake_schedule_enforces_min_interval(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.config.wake_schedule_min_interval_seconds = 600

    with pytest.raises(ValueError, match="minimum=600s actual=300s"):
        service.operations_admin.create_wake_schedule(
            session_id="issue-1-session",
            prompt="Resume now",
            cron="*/5 * * * *",
        )


def test_edit_wake_schedule_enforces_guardrails_when_enabling_or_tightening_cron(
    tmp_path: Path,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.config.wake_schedule_max_active_per_session = 1
    service.config.wake_schedule_min_interval_seconds = 600
    created = service.operations_admin.create_wake_schedule(
        session_id="issue-1-session",
        prompt="Resume later",
        cron="*/15 * * * *",
        enabled=False,
    )
    service.operations_admin.create_wake_schedule(
        session_id="issue-1-session",
        prompt="Existing active",
        cron="*/20 * * * *",
    )

    with pytest.raises(ValueError, match="max_active_per_session=1"):
        service.operations_admin.edit_wake_schedule(created.request_id, enabled=True)

    other = service.operations_admin.create_wake_schedule(
        session_id="issue-2-session",
        prompt="Safe interval",
        cron="*/15 * * * *",
    )
    with pytest.raises(ValueError, match="minimum=600s actual=300s"):
        service.operations_admin.edit_wake_schedule(other.request_id, cron="*/5 * * * *")


def test_edit_wake_schedule_rejects_new_context_for_mcp_source(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    created = service.operations_admin.create_wake_schedule(
        session_id="issue-1-session",
        prompt="Resume now",
        resume_at=datetime.now(UTC) + timedelta(hours=1),
        source="mcp_tool",
        resume_mode="same_context",
    )
    with pytest.raises(ValueError, match="MCP scheduling only supports"):
        service.operations_admin.edit_wake_schedule(created.request_id, resume_mode="new_context")


def test_create_wake_schedule_rejects_new_context_for_mcp_source(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    with pytest.raises(ValueError, match="MCP scheduling only supports"):
        service.operations_admin.create_wake_schedule(
            session_id="issue-1-session",
            prompt="Resume now",
            resume_at=datetime.now(UTC) + timedelta(hours=1),
            source="mcp_schedule_resume",
            resume_mode="new_context",
        )


def test_list_wake_schedules_filters_disabled_entries(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    created = service.operations_admin.create_wake_schedule(
        session_id="issue-1-session",
        prompt="Resume now",
        cron="*/5 * * * *",
        source="admin_cli",
    )
    service.operations_admin.edit_wake_schedule(created.request_id, enabled=False)

    schedules = service.operations_admin.list_wake_schedules(
        session_id="issue-1-session", include_disabled=False
    )

    assert schedules == []


def test_list_scheduled_wake_ledger_filters_statuses_and_completed_entries(
    tmp_path: Path,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    scheduled_for = datetime.now(UTC) + timedelta(minutes=30)
    failed = service.operations_admin.create_wake_schedule(
        session_id="issue-1-session",
        prompt="Retry me later.",
        resume_at=scheduled_for,
        source="admin_cli",
    )
    service.runtime.wake_store.record_dispatch(
        WakeDispatchRecord(
            dispatch_id="dispatch-failed",
            request_id=failed.request_id,
            session_id="issue-1-session",
            status="failed",
            resume_prompt=WakeResumePromptPayload(prompt="Retry me later."),
            dispatched_at=datetime.now(UTC),
            error="stack trace\nsecret token",
        )
    )

    completed = service.operations_admin.create_wake_schedule(
        session_id="issue-1-session",
        prompt="Already finished.",
        resume_at=scheduled_for,
        source="admin_cli",
    )
    service.runtime.wake_store.record_dispatch(
        WakeDispatchRecord(
            dispatch_id="dispatch-completed",
            request_id=completed.request_id,
            session_id="issue-1-session",
            status="dispatched",
            resume_prompt=WakeResumePromptPayload(prompt="Already finished."),
            dispatched_at=datetime.now(UTC),
        )
    )
    service.runtime.wake_store.mark_completed_by_dispatch_id(
        "dispatch-completed",
        completed_at=datetime.now(UTC),
        evidence={"dispatch_id": "dispatch-completed", "session_id": "issue-1-session"},
    )

    entries = service.operations_admin.list_scheduled_wake_ledger(
        session_id="issue-1-session",
        statuses=["failed", "completed_or_acknowledged"],
        include_completed=True,
    )
    assert {item["request_id"] for item in entries} == {failed.request_id, completed.request_id}
    failed_entry = next(item for item in entries if item["request_id"] == failed.request_id)
    assert failed_entry["retry_available"] is True
    assert failed_entry["prompt_summary"] == "Retry me later."
    assert failed_entry["redacted_prompt"] == "[redacted]"
    assert failed_entry["failure_reason"] is not None
    assert "\n" not in failed_entry["failure_reason"]

    open_entries = service.operations_admin.list_scheduled_wake_ledger(
        session_id="issue-1-session",
        include_completed=False,
    )
    assert {item["request_id"] for item in open_entries} == {failed.request_id}


def test_list_scheduled_wake_ledger_marks_dispatched_wakes_awaiting_confirmation(
    tmp_path: Path,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    scheduled_for = datetime.now(UTC) + timedelta(minutes=30)
    dispatched = service.operations_admin.create_wake_schedule(
        session_id="issue-1-session",
        prompt="Still waiting.",
        resume_at=scheduled_for,
        source="admin_cli",
    )
    service.runtime.wake_store.record_dispatch(
        WakeDispatchRecord(
            dispatch_id="dispatch-dispatched",
            request_id=dispatched.request_id,
            session_id="issue-1-session",
            status="dispatched",
            resume_prompt=WakeResumePromptPayload(prompt="Still waiting."),
            dispatched_at=datetime.now(UTC),
        )
    )

    entries = service.operations_admin.list_scheduled_wake_ledger(
        session_id="issue-1-session",
        statuses=["dispatched"],
        include_completed=True,
    )

    assert [item["request_id"] for item in entries] == [dispatched.request_id]
    item = entries[0]
    assert item["status"] == "dispatched"
    assert item["confirmation_state"] == "awaiting_confirmation"
    assert item["completed_at"] is None
    assert item["completion_evidence"] == {}


def test_retry_scheduled_wake_reschedules_failed_request_and_preserves_history(
    tmp_path: Path,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    scheduled_for = datetime.now(UTC) + timedelta(minutes=5)
    retry_at = datetime.now(UTC) + timedelta(hours=1)
    created = service.operations_admin.create_wake_schedule(
        session_id="issue-1-session",
        prompt="Resume work.",
        resume_at=scheduled_for,
        source="admin_cli",
    )
    service.runtime.wake_store.record_dispatch(
        WakeDispatchRecord(
            dispatch_id="dispatch-failed",
            request_id=created.request_id,
            session_id="issue-1-session",
            status="failed",
            resume_prompt=WakeResumePromptPayload(prompt="Resume work."),
            dispatched_at=datetime.now(UTC),
            error="stack trace\nsecret token",
        )
    )
    request = service.runtime.wake_store.get_request(created.request_id)
    assert request is not None
    request.auto_dispatch_attempts = 2
    request.auto_retry_started_at = datetime.now(UTC) - timedelta(hours=2)
    request.auto_retry_last_backoff_seconds = 120.0
    request.auto_retry_exhausted_at = datetime.now(UTC) - timedelta(minutes=5)
    request.enabled = False
    request.next_run_at = None
    request.status = "failed"
    request.status_reason = "One-time wake retry limit reached after 2/2 attempts."
    request.last_error = request.status_reason
    request.last_error_at = datetime.now(UTC) - timedelta(minutes=5)
    service.runtime.wake_store.upsert_request(request)

    updated = service.operations_admin.retry_scheduled_wake(
        created.request_id,
        resume_at=retry_at,
        run_now=False,
    )
    request = service.runtime.wake_store.get_request(created.request_id)

    assert updated["request_id"] == created.request_id
    assert updated["status"] == "pending"
    assert updated["enabled"] is True
    assert updated["next_run_at"] == retry_at.isoformat().replace("+00:00", "Z")
    assert updated["resume_at"] == retry_at.isoformat().replace("+00:00", "Z")
    assert updated["failure_reason"] is None
    assert updated["last_error"] is None
    assert updated["status_reason"] is None
    assert updated["latest_dispatch"]["status"] == "failed"
    assert request is not None
    assert request.schedule.resume_at == retry_at
    assert request.auto_dispatch_attempts == 0
    assert request.auto_retry_started_at is None
    assert request.auto_retry_last_backoff_seconds is None
    assert request.auto_retry_exhausted_at is None
    retry_history = request.resume_prompt.metadata["retry_history"]
    assert retry_history[0]["previous_status"] == "failed"
    assert retry_history[0]["previous_failure_reason"] is not None
    assert retry_history[0]["previous_auto_dispatch_attempts"] == 2
    assert retry_history[0]["previous_auto_retry_started_at"] is not None
    assert retry_history[0]["previous_auto_retry_last_backoff_seconds"] == 120.0
    assert retry_history[0]["previous_auto_retry_exhausted_at"] is not None
    assert retry_history[0]["requested_resume_at"] == retry_at.isoformat().replace("+00:00", "Z")


def test_pr_create_writes_inline_body_to_temporary_file_and_cleans_it_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    managed_git = service.managed_git_admin
    captured: dict[str, Any] = {}

    monkeypatch.setattr(
        managed_git,
        "_require_record",
        lambda managed_repo_id: {
            "managed_repo_id": managed_repo_id,
            "directory_path": "root",
            "provider": "github",
            "canonical_repository_url": "https://github.com/example/repo.git",
            "default_branch": "main",
        },
    )
    monkeypatch.setattr(
        managed_git,
        "push_branch",
        lambda *args, **kwargs: {"success": True, "commit_sha": "a" * 40},
    )
    monkeypatch.setattr(
        managed_git,
        "_provider_credentials",
        lambda _record: ("token", "test-provider-token"),
    )

    def fake_run_git(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if cmd[:3] == ["gh", "pr", "list"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="[]", stderr="")
        if cmd[:3] == ["gh", "pr", "view"]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps(
                    {
                        "number": 14724,
                        "url": "https://github.com/example/repo/pull/14724",
                        "headRefOid": "a" * 40,
                    }
                ),
                stderr="",
            )
        captured["cmd"] = list(cmd)
        body_path = Path(cmd[cmd.index("--body-file") + 1])
        captured["body_file"] = body_path
        captured["body"] = body_path.read_text(encoding="utf-8")
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout="https://github.com/example/repo/pull/14724\n",
            stderr="",
        )

    monkeypatch.setattr(managed_git, "_run_git", fake_run_git)

    result = managed_git.pr_create(
        managed_repo_id="mr_0123456789abcdefabcd",
        source_ref="refs/evander/operations/op-1/source",
        expected_commit_sha="a" * 40,
        destination_branch="feature/issue-14724",
        directory_path="root",
        title="14724: Test Issue: Truman",
        body="Inline PR body",
        draft=True,
    )

    cmd = captured["cmd"]
    body_path = captured["body_file"]
    assert cmd[:3] == ["gh", "pr", "create"]
    assert "--draft" in cmd
    assert cmd.count("--body-file") == 1
    assert captured["body"] == "Inline PR body"
    assert not body_path.exists()
    assert result["success"] is True
    assert result["pr_url"] == "https://github.com/example/repo/pull/14724"
    assert result["managed_repo_id"] == "mr_0123456789abcdefabcd"


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
