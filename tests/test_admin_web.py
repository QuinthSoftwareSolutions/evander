from __future__ import annotations

import asyncio
import hashlib
import hmac
import io
import json
import subprocess
import tarfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MethodType
from types import SimpleNamespace

import httpx
import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

import evander.admin.services.workflow_reports_service as workflow_reports_service
import evander.admin.web.app as admin_web_app
import evander.admin.web.vms_issues as vms_issues_module
from evander.admin.composition import AdminRuntimeComponents, build_admin_runtime
from evander.admin.services.vm_agent.common_models import AgentCreateOperation
from evander.admin.payloads import (
    CapacityControlPlanePayload,
    CapacityControlPlaneStoragePayload,
    CapacityHealthPayload,
    CapacityPromptTelemetryPayload,
    CapacityQueueTelemetryPayload,
    CapacitySessionStoragePayload,
    CapacitySessionSummaryPayload,
    CapacityWorkerTelemetryEntryPayload,
    CapacityWorkerTelemetrySummaryPayload,
    WorkflowFailureClusterExamplePayload,
    WorkflowFailureClusterPayload,
    WorkflowFailureClusterReportPayload,
    WorkflowRunRecentNodeActivityPayload,
    WorkflowRunSnapshotPayload,
)
from evander.admin import release_metadata as release_metadata_module
from evander.admin.web.cache import AdminWebJsonResponseCache
from evander.admin.web.app import _workflow_run_events, create_app
from evander.admin.web.shared import AdminWebConfig, request_has_privileged_operations
from evander.admin.web.rbac import (
    ADMIN_OWNER_PERMISSION,
    AUDITOR_GROUP_ID,
    GROUPS_MANAGE_PERMISSION,
    OWNER_GROUP_ID,
    TEAM_CHATS_READ_ALL_PERMISSION,
    WORKFLOW_PACKS_MANAGE_PERMISSION,
    WORKFLOWS_READ_PERMISSION,
    WORKFLOWS_WRITE_PERMISSION,
    WORKFLOW_RUNS_CONTROL_PERMISSION,
    WORKFLOW_RUNS_READ_PERMISSION,
    WORKFLOW_VIEWER_GROUP_ID,
)
from evander.admin.web.user_store import AdminWebUserStore
from evander.capacity_health import (
    DiskThresholds,
    DiskUsageTelemetry,
    DirectorySizeTelemetry,
    HealthState,
    SessionStorageSample,
    TelemetryError,
    TelemetryStatus,
)
from evander.config import GatewayConfig, TriggerOrchestratorWorkerGlobalConfig
from evander.daemons.effect_orchestrator import (
    EffectExecutionRecord,
    GateExecutionRecord,
    JsonExecutionStore,
    JsonGateExecutionStore,
    SqliteExecutionStore,
    SqliteGateExecutionStore,
)
from evander.daemons.trigger_orchestrator import TriggerOrchestratorWorker
from evander.artifact_spaces import ArtifactSpaceStore
from evander.communication_artifact_links import (
    issue_communication_artifact_download_link,
)
from evander.communication_artifacts import CommunicationArtifactStore
from evander.directory_config import DirectoryConfigStore
from evander.directories import DirectoryService
from evander.published_artifacts import PublishedArtifactStore
from evander.execution.container_worker_repository import (
    ContainerWorkerExecutionRecord,
    ContainerWorkerExecutionRepository,
)
from evander.execution.models import (
    ExecutionBackendKind,
    ExecutionStatus,
    ExecutionStatusKind,
)
from evander.messaging.targets import (
    COMMUNICATION_TARGETS_DOMAIN,
    SqliteCommunicationTargetsRepository,
    communication_targets_db_path,
)
from evander.model_usage.pricing import (
    clear_model_pricing_catalog_cache,
    default_model_pricing_catalog,
    seed_default_model_pricing_catalog,
)
from evander.model_usage.runtime import register_model_usage_correlation_context
from evander.models import (
    ApprovalRequest,
    ApprovalStatus,
    LocalIssueRecord,
    SessionManifest,
    SessionSummary,
    ToolName,
    VmActionResult,
    VmSummary,
    WorkerSummary,
)
from evander.events.internal import InternalEventBus
from evander.events.models import TriggerEvent
from evander.orchestration.host_polling import wake_socket_path_for_lock
from evander.repositories.orchestration_db import connect_orchestration_db
from evander.triggers.queue import SqliteTriggerEventQueue
from evander.triggers.queue import TriggerEventQueue
from evander.worker.models import WorkerSessionRecord


def write_config(
    tmp_path: Path,
    *,
    enable_directory_scoped_config: bool = False,
    execution_backend: str = "libvirt_vm",
) -> Path:
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
    if execution_backend != "libvirt_vm":
        lines.append(f'execution_backend: "{execution_backend}"')
    config_path.write_text("\n".join(lines), encoding="utf-8")
    return config_path


def wait_for_agent_create_operation_status(
    service: AdminRuntimeComponents,
    operation_id: str,
    status: str,
    *,
    timeout_seconds: float = 3.0,
):
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        operation = service.vm_agent_admin.get_admin_agent_create_operation(operation_id)
        if operation.status == status:
            return operation
        time.sleep(0.01)
    operation = service.vm_agent_admin.get_admin_agent_create_operation(operation_id)
    raise AssertionError(
        f"Operation {operation_id} did not reach {status}; last status was {operation.status}"
    )


def _write_trigger_orchestrator_sqlite_config(tmp_path: Path) -> Path:
    runtime_dir = tmp_path / "runtime"
    config_path = tmp_path / "config" / "trigger-orchestrator" / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(
            {
                "runtime_dir": str(runtime_dir),
                "orchestration_store_backend": "sqlite",
                "orchestration_store_path": str(runtime_dir / "orchestration.db"),
                "orchestration_retention_days": 365,
                "poll_interval_seconds": 1,
                "jitter_seconds": 0,
                "triggers": [],
                "gates": [],
                "edges": [],
                "effects": [],
                "queries": [],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return config_path


def _sqlite_payload_json_by_execution_id(path: Path, table_name: str) -> dict[str, str]:
    with connect_orchestration_db(path) as connection:
        rows = connection.execute(
            f"SELECT execution_id, payload_json FROM {table_name} ORDER BY execution_id"
        ).fetchall()
    return {row["execution_id"]: row["payload_json"] for row in rows}


def _effect_execution_record(
    *,
    execution_id: str,
    idempotency_key: str,
    event_id: str,
    effect_id: str,
    status: str,
    started_at: datetime,
    finished_at: datetime,
    dismissed_at: datetime | None = None,
    dismiss_reason: str | None = None,
) -> EffectExecutionRecord:
    record = EffectExecutionRecord(
        execution_id=execution_id,
        idempotency_key=idempotency_key,
        event_id=event_id,
        trace_id="trace-test",
        source_node_id="trigger-ticket",
        source_trigger_type="trigger:ticket:created",
        effect_id=effect_id,
        effect_type="effect:agent:prompt",
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        resolved_config_hash="hash-test",
        resolved_config={},
        input_payload={},
        outputs={},
    )
    if dismissed_at is not None:
        record.dismissed_at = dismissed_at
    if dismiss_reason is not None:
        record.dismiss_reason = dismiss_reason
    return record


def _gate_execution_record(
    *,
    execution_id: str,
    idempotency_key: str,
    event_id: str,
    gate_id: str,
    status: str,
    started_at: datetime,
    finished_at: datetime,
    dismissed_at: datetime | None = None,
    dismiss_reason: str | None = None,
) -> GateExecutionRecord:
    record = GateExecutionRecord(
        execution_id=execution_id,
        idempotency_key=idempotency_key,
        event_id=event_id,
        trace_id="trace-test",
        source_node_id="trigger-ticket",
        source_trigger_type="trigger:ticket:created",
        gate_id=gate_id,
        gate_type="gate:filter",
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        resolved_config_hash="hash-test",
        resolved_config={},
        input_payload={},
        outputs={},
        inputs_by_source={},
        input_event_ids_by_source={},
        input_occurred_at_by_source={},
    )
    if dismissed_at is not None:
        record.dismissed_at = dismissed_at
    if dismiss_reason is not None:
        record.dismiss_reason = dismiss_reason
    return record


def _workflow_expected_revision(client: TestClient) -> str:
    response = client.get("/api/workflow-editor/document")
    assert response.status_code == 200
    revision = response.json()["revision"]
    assert isinstance(revision, str)
    return revision


def _workflow_pack_list_revision(client: TestClient, workflow_pack: str | None = None) -> str:
    path = "/api/workflow-editor/document"
    if workflow_pack is not None:
        path = f"{path}?workflow_pack={workflow_pack}"
    response = client.get(path)
    assert response.status_code == 200
    revision = response.json()["pack_list_revision"]
    assert isinstance(revision, str)
    return revision


def _unlock_privileged_operations(client: TestClient) -> None:
    response = client.post("/api/admin/privileged/unlock")
    assert response.status_code == 200


def _snapshot_runtime_files(runtime_dir: Path) -> dict[str, bytes]:
    if not runtime_dir.exists():
        return {}
    snapshot: dict[str, bytes] = {}
    for path in sorted(runtime_dir.rglob("*")):
        if path.is_file():
            snapshot[str(path.relative_to(runtime_dir))] = path.read_bytes()
    return snapshot


def _configuration_expected_revision(
    client: TestClient, directory_path: str, tab_or_domain: str
) -> str:
    response = client.get(f"/api/configuration/state?directory_path={directory_path}")
    assert response.status_code == 200
    payload = response.json()
    snapshot = payload["tab_snapshots"].get(tab_or_domain) or payload["snapshots"][tab_or_domain]
    revision = snapshot["revision"]
    assert isinstance(revision, str)
    return revision


def _pricing_catalog_payload(
    *, version: str = "test-pricing", model: str = "test-model"
) -> dict[str, object]:
    return {
        "version": version,
        "currency": "usd",
        "description": "Test pricing catalog.",
        "sources": ["https://platform.openai.com/docs/pricing"],
        "rules": [
            {
                "id": f"openai:{model}:text",
                "provider": "openai",
                "models": [model],
                "model_patterns": [],
                "effective_from": "2026-06-04T00:00:00Z",
                "effective_to": None,
                "rates_per_1m_tokens": {
                    "input": "1.00",
                    "cached_input": "0.10",
                    "output": "2.00",
                },
                "batch_multiplier": "0.5",
                "service_tier": None,
                "long_context": None,
            }
        ],
    }


def _set_azure_sso_env(
    monkeypatch,
    *,
    allowed_users: str | None = "operator@example.com",
    post_login_redirect: str | None = None,
) -> None:
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AUTH_MODE", "azure_sso")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_SESSION_SECRET", "test-secret")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AZURE_TENANT_ID", "tenant-123")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AZURE_CLIENT_ID", "client-123")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AZURE_CLIENT_SECRET", "secret-123")
    monkeypatch.setenv(
        "EVANDER_ADMIN_WEB_AZURE_REDIRECT_URI",
        "https://admin.example.test/api/auth/azure-sso/callback",
    )
    if allowed_users is None:
        monkeypatch.delenv("EVANDER_ADMIN_WEB_AZURE_ALLOWED_USERS", raising=False)
    else:
        monkeypatch.setenv("EVANDER_ADMIN_WEB_AZURE_ALLOWED_USERS", allowed_users)
    if post_login_redirect is not None:
        monkeypatch.setenv("EVANDER_ADMIN_WEB_AZURE_POST_LOGIN_REDIRECT", post_login_redirect)


def _admin_web_user_store(tmp_path: Path) -> AdminWebUserStore:
    runtime_dir = tmp_path / "runtime"
    return AdminWebUserStore(
        runtime_dir / "admin-web.db",
        legacy_json_path=runtime_dir / "admin-web-users.json",
    )


def _complete_azure_login(
    app,
    client: TestClient,
    *,
    login: str = "operator@example.com",
    display_name: str = "Operator",
    tenant_id: str = "tenant-123",
):
    async def fake_exchange(*, code: str, code_verifier: str) -> dict[str, str]:
        return {"id_token": "stub-token"}

    async def fake_validate(id_token: str) -> dict[str, str]:
        return {
            "sub": "subject-1",
            "name": display_name,
            "preferred_username": login,
            "tid": tenant_id,
            "oid": "object-1",
            "iss": f"https://login.microsoftonline.com/{tenant_id}/v2.0",
        }

    app.state.auth._exchange_azure_code_for_token = fake_exchange  # type: ignore[method-assign]
    app.state.auth._validate_azure_id_token = fake_validate  # type: ignore[method-assign]
    start = client.get("/api/auth/azure-sso/start", follow_redirects=False)
    state = start.headers["location"].split("state=")[1].split("&", 1)[0]
    return client.get(
        f"/api/auth/azure-sso/callback?code=test-code&state={state}",
        follow_redirects=False,
    )


def _workflow_failure_cluster_example(
    *,
    event_id: str,
    trace_id: str | None = None,
    session_id: str | None = None,
    occurred_at: datetime | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    error_summary: str | None = None,
    run_state: str | None = None,
    artifact_ids: list[str] | None = None,
) -> WorkflowFailureClusterExamplePayload:
    return WorkflowFailureClusterExamplePayload(
        event_id=event_id,
        trace_id=trace_id,
        session_id=session_id,
        occurred_at=occurred_at,
        started_at=started_at,
        finished_at=finished_at,
        error_summary=error_summary,
        run_state=run_state,
        run_detail_href=f"#workflow?event_id={event_id}",
        api_run_detail_href=f"/api/workflow-editor/run-detail?event_id={event_id}",
        artifact_ids=list(artifact_ids or []),
    )


def _workflow_failure_cluster_report(
    clusters: list[WorkflowFailureClusterPayload],
    *,
    generated_at: datetime,
    period_start: datetime,
    period_end: datetime,
    recent_threshold_seconds: int | None = None,
    limit: int = 100,
    has_more: bool = False,
) -> WorkflowFailureClusterReportPayload:
    return WorkflowFailureClusterReportPayload(
        generated_at=generated_at,
        period_start=period_start,
        period_end=period_end,
        recent_threshold_seconds=recent_threshold_seconds,
        limit=limit,
        has_more=has_more,
        clusters=clusters,
    )


def _write_workflow_webhook_config(
    tmp_path: Path,
    *,
    slug: str = "build-notify",
    secret_env: str = "WEBHOOK_SECRET",
    event_type: str | None = None,
    duplicate: bool = False,
) -> None:
    config_path = tmp_path / "config" / "trigger-orchestrator" / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    triggers = [
        {
            "id": "webhook-1",
            "display_name": "Webhook 1",
            "type": "trigger:http:webhook",
            "slug": slug,
            "secret_env": secret_env,
        }
    ]
    if event_type is not None:
        triggers[0]["event_type"] = event_type
    if duplicate:
        triggers.append(
            {
                "id": "webhook-2",
                "display_name": "Webhook 2",
                "type": "trigger:http:webhook",
                "slug": slug,
                "secret_env": secret_env,
            }
        )
    config_path.write_text(
        json.dumps(
            {
                "runtime_dir": str(tmp_path / "runtime"),
                "poll_interval_seconds": 1,
                "jitter_seconds": 0,
                "triggers": triggers,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _signed_webhook_headers(
    body: bytes,
    *,
    secret: str,
    query: dict[str, object] | None = None,
    timestamp: int | None = None,
) -> dict[str, str]:
    timestamp_text = str(timestamp if timestamp is not None else int(time.time()))
    query_material = json.dumps(
        query or {},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    digest = hmac.new(
        secret.encode("utf-8"),
        timestamp_text.encode("ascii") + b"." + query_material.encode("utf-8") + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Evander-Timestamp": timestamp_text,
        "X-Evander-Signature-256": f"sha256={digest}",
    }


def _expected_webhook_idempotency_key(
    *,
    trigger_id: str,
    slug: str,
    headers: dict[str, str],
    query: dict[str, object],
) -> str:
    signature_key = headers["X-Evander-Signature-256"].replace("=", ":", 1)
    query_material = json.dumps(query, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    query_digest = hashlib.sha256(query_material.encode("utf-8")).hexdigest()
    return f"webhook:{trigger_id}:{slug}:{signature_key}:query-sha256:{query_digest}"


def test_admin_web_shutdown_cleans_active_managed_worktrees(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    cleanup_calls: list[dict[str, int]] = []

    def cleanup_active_worktrees() -> dict[str, int]:
        result = {"cleaned": 1, "failed": 0}
        cleanup_calls.append(result)
        return result

    monkeypatch.setattr(
        service.managed_git_admin,
        "cleanup_active_worktrees",
        cleanup_active_worktrees,
    )
    app = create_app(service)

    assert cleanup_calls == []
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert cleanup_calls == []

    assert cleanup_calls == [{"cleaned": 1, "failed": 0}]


def test_admin_web_exposes_workflow_editor_api(tmp_path: Path) -> None:
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))

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
    assert payload["graphs"] == []
    assert runs_payload["runs"] == []
    assert any(node["type"] == "effect:vm:exec" for node in catalog_payload["nodes"])
    generic_kind_tooltips = {
        "Triggers start workflow execution from incoming events.",
        "Gates transform, filter, fan out, or combine workflow data between nodes.",
        "Effects perform side effects such as VM actions, session updates, Git operations, or agent prompts.",
        "Queries read workflow or environment state without causing a side effect.",
    }
    descriptions_by_type = {
        node["type"]: node.get("description") for node in catalog_payload["nodes"]
    }
    assert all(descriptions_by_type[node["type"]] for node in catalog_payload["nodes"]), (
        descriptions_by_type
    )
    assert not any(
        descriptions_by_type[node["type"]] in generic_kind_tooltips
        for node in catalog_payload["nodes"]
    )
    assert (
        descriptions_by_type["effect:ado:pipeline-run"]
        == "Queue an Azure DevOps pipeline run and wait for its terminal result."
    )
    assert (
        descriptions_by_type["effect:http:request"]
        == "Perform a host-side raw HTTP GET, POST, or PUT request with URL allowlist control."
    )
    assert (
        descriptions_by_type["effect:runtime:diagnostics-snapshot"]
        == "Capture a bounded, redacted host runtime diagnostics snapshot from allowlisted sources."
    )
    assert (
        descriptions_by_type["effect:agent:exec"]
        == "Run a shell command inside an agent session workspace."
    )
    assert (
        descriptions_by_type["trigger:script-run-request"]
        == "Start from an approved run_script request submitted by a session."
    )
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
    agent_exec_command = next(field for field in agent_exec["fields"] if field["key"] == "command")
    assert agent_exec_command["multiline"] is True
    semantic_ensure = next(
        node
        for node in catalog_payload["nodes"]
        if node["type"] == "effect:semantic:ensure-collection"
    )
    assert [field["key"] for field in semantic_ensure["fields"]] == [
        "collection_name",
        "directory_path",
    ]
    semantic_upsert = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:semantic:upsert"
    )
    assert [field["key"] for field in semantic_upsert["fields"]] == [
        "collection_name",
        "point_id",
        "text",
        "payload",
        "directory_path",
        "wait",
    ]
    semantic_delete = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:semantic:delete"
    )
    assert [field["key"] for field in semantic_delete["fields"]] == [
        "collection_name",
        "point_ids",
        "directory_path",
        "wait",
    ]
    state_update = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:state:update"
    )
    assert state_update["description"] == "Set, patch, or delete graph-local workflow state."
    assert [field["key"] for field in state_update["fields"]] == [
        "namespace",
        "key",
        "operation",
        "value",
        "patch",
        "expected_version",
    ]
    state_update_fields = {field["key"]: field for field in state_update["fields"]}
    assert state_update_fields["namespace"]["required"]
    assert state_update_fields["namespace"]["bindable"]
    assert state_update_fields["operation"]["kind"] == "enum"
    assert [option["value"] for option in state_update_fields["operation"]["options"]] == [
        "set",
        "patch",
        "delete",
    ]
    assert state_update_fields["value"]["kind"] == "json"
    assert state_update_fields["value"]["json_value_type"] == "object"
    assert state_update_fields["patch"]["kind"] == "json"
    assert state_update_fields["patch"]["json_value_type"] == "object"
    assert state_update_fields["expected_version"]["kind"] == "number"
    state_get = next(node for node in catalog_payload["nodes"] if node["type"] == "query:state:get")
    assert state_get["description"] == (
        "Read graph-local workflow state for the current persisted workflow graph."
    )
    assert [field["key"] for field in state_get["fields"]] == [
        "namespace",
        "key",
    ]
    state_get_fields = {field["key"]: field for field in state_get["fields"]}
    assert state_get_fields["namespace"]["required"]
    assert state_get_fields["namespace"]["bindable"]
    assert state_get_fields["key"]["required"]
    assert state_get_fields["key"]["bindable"]
    agent_copy_files = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:agent:copy-files"
    )
    agent_copy_fields = {field["key"]: field for field in agent_copy_files["fields"]}
    assert all(
        agent_copy_fields[key]["description"]
        for key in [
            "target",
            "session_id",
            "vm_name",
            "vm_user",
            "allowed_source_roots",
            "files",
        ]
    )
    agent_copy_file_fields = {
        field["key"]: field for field in agent_copy_fields["files"]["item_fields"]
    }
    assert all(
        agent_copy_file_fields[key]["description"]
        for key in [
            "source_path",
            "source_format",
            "content",
            "content_format",
            "destination_path",
            "mode",
            "overwrite",
            "merge_strategy",
            "section_id",
            "remove_section_ids",
        ]
    )
    assert agent_copy_file_fields["content"]["multiline"]
    assert "/home/agent" in agent_copy_file_fields["destination_path"]["description"]
    assert "0644" in agent_copy_file_fields["mode"]["description"]
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
    issue_metadata = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:issue:update-metadata"
    )
    assert [field["key"] for field in issue_metadata["fields"]] == [
        "issue_id",
        "patch",
        "merge_patch",
        "expected_updated_at",
        "sync_proxy_cache",
    ]
    ado_update = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:ado:update-work-item"
    )
    assert [field["key"] for field in ado_update["fields"]] == [
        "directory_path",
        "project",
        "work_item_id",
        "state",
        "fields",
        "expected_states",
        "sync_proxy_cache",
    ]
    ado_add_tag = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:ado:add-work-item-tag"
    )
    assert [field["key"] for field in ado_add_tag["fields"]] == [
        "directory_path",
        "project",
        "work_item_id",
        "tag",
        "sync_proxy_cache",
    ]
    ado_remove_tag = next(
        node
        for node in catalog_payload["nodes"]
        if node["type"] == "effect:ado:remove-work-item-tag"
    )
    assert [field["key"] for field in ado_remove_tag["fields"]] == [
        "directory_path",
        "project",
        "work_item_id",
        "tag",
        "sync_proxy_cache",
    ]
    ado_comment = next(
        node
        for node in catalog_payload["nodes"]
        if node["type"] == "effect:ado:post-work-item-comment"
    )
    assert [field["key"] for field in ado_comment["fields"]] == [
        "directory_path",
        "project",
        "work_item_id",
        "comment",
    ]
    assert ado_comment["fields"][-1]["multiline"] is True
    pr_update = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:pull-request:update"
    )
    assert [field["key"] for field in pr_update["fields"]] == [
        "provider",
        "repo",
        "pr_id",
        "directory_path",
        "organization_url",
        "project",
        "title",
        "description",
        "status",
        "add_tags",
        "remove_tags",
        "expected_statuses",
    ]
    pr_add_tag = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:pull-request:add-tag"
    )
    assert [field["key"] for field in pr_add_tag["fields"]] == [
        "provider",
        "repo",
        "pr_id",
        "tag",
        "directory_path",
        "organization_url",
        "project",
    ]
    pr_remove_tag = next(
        node
        for node in catalog_payload["nodes"]
        if node["type"] == "effect:pull-request:remove-tag"
    )
    assert [field["key"] for field in pr_remove_tag["fields"]] == [
        "provider",
        "repo",
        "pr_id",
        "tag",
        "directory_path",
        "organization_url",
        "project",
    ]
    event_emit = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:event:emit"
    )
    assert [field["key"] for field in event_emit["fields"]] == [
        "event_type",
        "correlation_key",
        "payload",
    ]
    event_schedule = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:event:schedule"
    )
    assert [field["key"] for field in event_schedule["fields"]] == [
        "event_type",
        "correlation_key",
        "payload",
        "run_at",
        "delay_seconds",
        "idempotency_key",
    ]
    assert event_schedule["fields"][0]["bindable"] is True
    assert event_schedule["fields"][2]["required"] is True
    assert event_schedule["fields"][2]["json_value_type"] == "object"
    assert event_schedule["fields"][3]["bindable"] is True
    assert event_schedule["fields"][4]["bindable"] is True
    assert event_schedule["fields"][5]["bindable"] is True
    communication = next(
        node
        for node in catalog_payload["nodes"]
        if node["type"] == "effect:communication:send-message"
    )
    assert [field["key"] for field in communication["fields"]] == [
        "communication_target_id",
        "message",
        "thread_id",
    ]
    assert (
        next(
            field for field in communication["fields"] if field["key"] == "communication_target_id"
        )["kind"]
        == "enum"
    )
    assert any(
        field["key"] == "message" and field["bindable"] and field["multiline"]
        for field in communication["fields"]
    )
    notification = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:notification:send"
    )
    assert [field["key"] for field in notification["fields"]] == [
        "communication_target_id",
        "message",
        "severity",
        "subject",
        "thread_id",
    ]
    assert any(
        field["key"] == "message" and field["bindable"] and field["multiline"]
        for field in notification["fields"]
    )
    agent_prompt = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:agent:prompt"
    )
    assert any(field["key"] == "model" and field["bindable"] for field in agent_prompt["fields"])
    assert any(
        field["key"] == "mcp_tool_approval_lease_id" and field["bindable"]
        for field in agent_prompt["fields"]
    )
    assert any(
        field["key"] == "resume_thread_metadata_collection" and field["bindable"]
        for field in agent_prompt["fields"]
    )
    assert all(field["key"] != "output_mode" for field in agent_prompt["fields"])
    assert agent_prompt["output_ports"] == [
        {
            "key": "started",
            "label": "Started",
            "description": "Emits after the prompt has been dispatched.",
        },
        {
            "key": "completed",
            "label": "Completed",
            "description": "Emits after the agent run has completed.",
        },
        {
            "key": "error",
            "label": "Error",
            "description": "Emits when prompt dispatch fails and the node is handled by an error route.",
        },
    ]
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
        "dispatch_id",
        "resume_thread_id",
        "resume_mode",
        "resume_thread_metadata_key",
        "resume_thread_metadata_collection",
        "mcp_tool_approval_lease_id",
        "queue_key",
    ]
    assert agent_enqueue_prompt["output_ports"] == [
        {
            "key": "started",
            "label": "Started",
            "description": "Emits after the queued prompt has been dispatched.",
        },
        {
            "key": "completed",
            "label": "Completed",
            "description": "Emits after the dispatched agent run has completed.",
        },
        {
            "key": "error",
            "label": "Error",
            "description": "Emits when prompt dispatch fails and the node is handled by an error route.",
        },
    ]
    tool_approval = next(
        node
        for node in catalog_payload["nodes"]
        if node["type"] == "effect:session:approve-mcp-tools-for-run"
    )
    assert [field["key"] for field in tool_approval["fields"]] == [
        "target",
        "session_id",
        "vm_name",
        "tools",
        "approval_notification_target_ids",
        "expires_in_seconds",
    ]
    assert any(
        field["key"] == "tools" and field["required"] and field["bindable"]
        for field in tool_approval["fields"]
    )
    approval_notification_target_ids = next(
        field
        for field in tool_approval["fields"]
        if field["key"] == "approval_notification_target_ids"
    )
    assert approval_notification_target_ids["kind"] == "string_list"
    assert approval_notification_target_ids["options"] == []
    ado_pipeline = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:ado:pipeline-run"
    )
    assert [field["key"] for field in ado_pipeline["fields"]] == [
        "directory_path",
        "pipeline_id",
        "project",
        "branch",
        "variables",
        "template_parameters",
        "timeout_seconds",
        "poll_interval_seconds",
        "check",
    ]
    http_request = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:http:request"
    )
    assert [field["key"] for field in http_request["fields"]] == [
        "method",
        "url",
        "allowed_url_patterns",
        "allow_private_networks",
        "payload",
        "headers",
        "timeout_seconds",
        "check",
    ]
    diagnostics = next(
        node
        for node in catalog_payload["nodes"]
        if node["type"] == "effect:runtime:diagnostics-snapshot"
    )
    assert [field["key"] for field in diagnostics["fields"]] == [
        "since",
        "units",
        "include_failed_units",
        "include_journal",
        "include_runtime_files",
        "max_journal_lines_per_unit",
        "max_runtime_file_bytes",
        "command_timeout_seconds",
    ]
    http_request_fields = {field["key"]: field for field in http_request["fields"]}
    assert [option["value"] for option in http_request_fields["method"]["options"]] == [
        "GET",
        "POST",
        "PUT",
    ]
    assert http_request_fields["method"]["default_value"] == "GET"
    assert http_request_fields["url"]["required"] and http_request_fields["url"]["bindable"]
    assert http_request_fields["allowed_url_patterns"]["kind"] == "string_list"
    assert http_request_fields["allowed_url_patterns"]["default_value"] == ["*"]
    assert http_request_fields["allow_private_networks"]["kind"] == "boolean"
    assert http_request_fields["allow_private_networks"]["default_value"] is False
    assert http_request_fields["payload"]["json_value_type"] == "object"
    assert http_request_fields["headers"]["json_value_type"] == "object"
    assert http_request_fields["check"]["default_value"] is False
    ticket_created = next(
        node for node in catalog_payload["nodes"] if node["type"] == "trigger:ticket:created"
    )
    assert all(field["key"] != "outputs" for field in ticket_created["fields"])
    review_request = next(
        node for node in catalog_payload["nodes"] if node["type"] == "trigger:review:new-request"
    )
    assert review_request["fields"] == []
    approval_request = next(
        node
        for node in catalog_payload["nodes"]
        if node["type"] == "trigger:approval:pending-created"
    )
    assert approval_request["label"] == "Pending approval"
    assert approval_request["description"] == "Start when a pending approval request is created."
    assert [field["key"] for field in approval_request["fields"]] == [
        "directory_path",
        "action_types",
        "tool_names",
    ]
    approval_request_fields = {field["key"]: field for field in approval_request["fields"]}
    assert approval_request_fields["directory_path"]["default_value"] == "root"
    assert approval_request_fields["action_types"]["default_value"] == []
    assert approval_request_fields["tool_names"]["default_value"] == []
    script_run_request = next(
        node for node in catalog_payload["nodes"] if node["type"] == "trigger:script-run-request"
    )
    assert script_run_request["fields"] == []
    internal_event = next(
        node for node in catalog_payload["nodes"] if node["type"] == "trigger:event"
    )
    assert [field["key"] for field in internal_event["fields"]] == [
        "event_type",
        "correlation_key",
    ]
    webhook_trigger = next(
        node for node in catalog_payload["nodes"] if node["type"] == "trigger:http:webhook"
    )
    assert webhook_trigger["description"] == (
        "Start from a signed HTTP webhook delivered to a public endpoint."
    )
    assert [field["key"] for field in webhook_trigger["fields"]] == [
        "slug",
        "secret_env",
        "event_type",
    ]
    assert [field["required"] for field in webhook_trigger["fields"]] == [
        True,
        True,
        False,
    ]
    issue_comment = next(
        node for node in catalog_payload["nodes"] if node["type"] == "trigger:issue:comment-created"
    )
    assert [field["key"] for field in issue_comment["fields"]] == [
        "provider",
        "organization_url",
        "project",
        "directories",
    ]
    ado_comment = next(
        node
        for node in catalog_payload["nodes"]
        if node["type"] == "trigger:ado:work-item-comment-created"
    )
    assert ado_comment["label"] == "ADO work item comment"
    assert ado_comment["description"] == (
        "Start when a matching Azure DevOps work item receives a new matching comment."
    )
    assert [field["key"] for field in ado_comment["fields"]] == [
        "directory_path",
        "project",
        "query_mode",
        "wiql",
        "current_iteration",
        "team",
        "iteration",
        "area_path",
        "states",
        "types",
        "tags_any",
        "text_search",
        "top",
        "comment_contains",
        "comment_contains_any",
        "comment_author_exclude_names",
        "comment_author_exclude_ids",
        "comment_case_sensitive",
        "include_existing",
    ]
    remote_issue = next(
        node for node in catalog_payload["nodes"] if node["type"] == "trigger:remote-issue-matching"
    )
    assert remote_issue["label"] == "Remote issue matching"
    assert [field["key"] for field in remote_issue["fields"]] == [
        "provider",
        "directory_path",
        "project",
        "repo",
        "query_mode",
        "wiql",
        "current_iteration",
        "team",
        "iteration",
        "area_path",
        "states",
        "types",
        "tags_any",
        "state",
        "labels_any",
        "labels_all",
        "assignee",
        "author",
        "mentioned",
        "milestone",
        "since",
        "text_search",
        "top",
    ]
    remote_issue_comment = next(
        node
        for node in catalog_payload["nodes"]
        if node["type"] == "trigger:remote-issue-comment-created"
    )
    assert [field["key"] for field in remote_issue_comment["fields"]][-6:] == [
        "comment_contains",
        "comment_contains_any",
        "comment_author_exclude_names",
        "comment_author_exclude_ids",
        "comment_case_sensitive",
        "include_existing",
    ]
    ado_comment_fields = {field["key"]: field for field in ado_comment["fields"]}
    assert ado_comment_fields["query_mode"]["default_value"] == "structured_filters"
    assert ado_comment_fields["wiql"]["multiline"] is True
    assert ado_comment_fields["top"]["default_value"] == 50
    assert ado_comment_fields["comment_contains_any"]["default_value"] == []
    assert ado_comment_fields["comment_case_sensitive"]["default_value"] is False
    assert ado_comment_fields["include_existing"]["default_value"] is False
    pr_trigger_field_keys = [
        "provider",
        "repo",
        "directory_path",
        "organization_url",
        "project",
        "status",
        "creator_id",
        "reviewer_id",
        "source_ref_name",
        "target_ref_name",
        "source_repository_id",
        "repository_id",
        "min_time",
        "max_time",
        "query_time_range_type",
        "include_links",
        "max_comment_length",
        "tags",
        "top",
        "skip",
        "cursor",
    ]
    pr_feedback_trigger = next(
        node
        for node in catalog_payload["nodes"]
        if node["type"] == "trigger:pull-request:new-feedback"
    )
    assert [field["key"] for field in pr_feedback_trigger["fields"]] == [
        *pr_trigger_field_keys,
        "agent_author_names",
        "agent_mention_names",
        "agent_author_ids",
        "agent_mention_ids",
        "suppress_agent_authored_feedback",
    ]
    pr_completed_trigger = next(
        node
        for node in catalog_payload["nodes"]
        if node["type"] == "trigger:pull-request:completed"
    )
    assert [field["key"] for field in pr_completed_trigger["fields"]] == pr_trigger_field_keys
    teams_message = next(
        node for node in catalog_payload["nodes"] if node["type"] == "trigger:teams:new-message"
    )
    assert [field["key"] for field in teams_message["fields"]] == ["targets", "user_ids"]
    ci_run_completed = next(
        node for node in catalog_payload["nodes"] if node["type"] == "trigger:ci:run:completed"
    )
    assert [field["key"] for field in ci_run_completed["fields"]] == [
        "provider",
        "repo",
        "workflow",
        "organization_url",
        "project",
        "pipeline_id",
        "pipeline_name",
        "branches",
        "conclusions",
        "top",
        "include_existing",
    ]
    assert next(field for field in ci_run_completed["fields"] if field["key"] == "provider")[
        "options"
    ] == [
        {"value": "github_actions", "label": "GitHub Actions"},
        {"value": "ado_pipelines", "label": "Azure DevOps Pipelines"},
    ]
    assert (
        next(field for field in ci_run_completed["fields"] if field["key"] == "include_existing")[
            "kind"
        ]
        == "boolean"
    )
    communication_message = next(
        node
        for node in catalog_payload["nodes"]
        if node["type"] == "trigger:communication:new-message"
    )
    assert [field["key"] for field in communication_message["fields"]] == [
        "communication_target_ids",
        "directory_path",
        "author_ids",
        "content_contains",
        "case_sensitive",
    ]
    assert (
        next(
            field
            for field in communication_message["fields"]
            if field["key"] == "communication_target_ids"
        )["kind"]
        == "string_list"
    )
    teams_direct_message = next(
        node for node in catalog_payload["nodes"] if node["type"] == "trigger:teams:direct-message"
    )
    assert [field["key"] for field in teams_direct_message["fields"]] == [
        "sender_user_ids",
        "message_filter",
    ]
    sender_user_ids_field = next(
        field for field in teams_direct_message["fields"] if field["key"] == "sender_user_ids"
    )
    assert sender_user_ids_field["label"] == "Sender user IDs or emails"
    assert "UPNs, or mail addresses" in sender_user_ids_field["description"]
    deserialize_gate = next(
        node for node in catalog_payload["nodes"] if node["type"] == "gate:deserialize"
    )
    assert [field["key"] for field in deserialize_gate["fields"]] == [
        "content",
        "format",
        "outputs",
    ]
    assert deserialize_gate["fields"][0]["bindable"]
    assert [option["value"] for option in deserialize_gate["fields"][1]["options"]] == [
        "auto",
        "json",
        "yaml",
    ]
    serialize_gate = next(
        node for node in catalog_payload["nodes"] if node["type"] == "gate:serialize"
    )
    assert [field["key"] for field in serialize_gate["fields"]] == [
        "value",
        "format",
        "outputs",
    ]
    assert serialize_gate["fields"][0]["bindable"]
    assert [option["value"] for option in serialize_gate["fields"][1]["options"]] == [
        "json",
        "yaml",
    ]
    aggregate_gate = next(
        node for node in catalog_payload["nodes"] if node["type"] == "gate:aggregate"
    )
    aggregate_fields = {field["key"]: field for field in aggregate_gate["fields"]}
    assert [field["key"] for field in aggregate_gate["fields"]] == [
        "items",
        "metrics",
        "outputs",
    ]
    assert aggregate_fields["items"]["kind"] == "json"
    assert aggregate_fields["items"]["required"]
    assert aggregate_fields["items"]["bindable"]
    assert aggregate_fields["items"]["json_value_type"] == "array"
    assert aggregate_fields["metrics"]["kind"] == "json"
    assert aggregate_fields["metrics"]["required"]
    assert aggregate_fields["metrics"]["json_value_type"] == "object"
    assert aggregate_fields["metrics"]["default_value"] == {
        "total": {"operation": "sum", "path": "value"}
    }
    assert aggregate_fields["outputs"]["read_only"]
    buffer_gate = next(node for node in catalog_payload["nodes"] if node["type"] == "gate:buffer")
    buffer_fields = {field["key"]: field for field in buffer_gate["fields"]}
    assert buffer_gate["label"] == "Buffer"
    assert [field["key"] for field in buffer_gate["fields"]] == [
        "key",
        "count",
        "timeout",
        "outputs",
    ]
    assert buffer_fields["key"]["required"]
    assert buffer_fields["key"]["bindable"]
    assert buffer_fields["count"]["required"]
    assert buffer_fields["count"]["bindable"]
    assert buffer_fields["count"]["default_value"] == 10
    assert not buffer_fields["timeout"]["required"]
    assert buffer_fields["timeout"]["bindable"]
    assert buffer_fields["timeout"]["default_value"] == "PT10M"
    assert buffer_fields["outputs"]["read_only"]
    assert buffer_fields["outputs"]["default_value"] == {
        "items": {"type": "array"},
        "count": {"type": "integer"},
        "release_reason": {"type": "string"},
    }
    await_agent = next(
        node for node in catalog_payload["nodes"] if node["type"] == "gate:await-agent"
    )
    assert [field["key"] for field in await_agent["fields"]] == ["session_id", "vm_name", "outputs"]
    assert all(field["bindable"] for field in await_agent["fields"][:2])
    for_gate = next(node for node in catalog_payload["nodes"] if node["type"] == "gate:for")
    for_gate_fields = {field["key"]: field for field in for_gate["fields"]}
    assert for_gate_fields["items"]["kind"] == "json"
    assert for_gate_fields["items"]["required"]
    assert for_gate_fields["items"]["bindable"]
    assert for_gate_fields["items"]["json_value_type"] == "array"
    assert for_gate_fields["items"]["default_value"] == []
    assert for_gate["output_ports"] == [
        {"key": "body", "label": "Body", "description": "Loop body path while items remain."},
        {"key": "done", "label": "Done", "description": "Exit path after the final item."},
    ]
    while_gate = next(node for node in catalog_payload["nodes"] if node["type"] == "gate:while")
    while_gate_fields = {field["key"]: field for field in while_gate["fields"]}
    assert while_gate_fields["when"]["kind"] == "json"
    assert while_gate_fields["when"]["required"]
    assert while_gate_fields["when"]["bindable"]
    assert while_gate_fields["max_iterations"]["kind"] == "number"
    assert while_gate_fields["max_iterations"]["required"]
    assert while_gate_fields["max_iterations"]["bindable"]
    assert while_gate_fields["max_iterations"]["default_value"] == 3
    assert while_gate["output_ports"] == [
        {
            "key": "body",
            "label": "Body",
            "description": "Loop body path while the predicate is true.",
        },
        {"key": "done", "label": "Done", "description": "Exit path when the predicate is false."},
        {
            "key": "exhausted",
            "label": "Exhausted",
            "description": "Exit path when Max iterations is reached while the predicate is still true.",
        },
    ]
    switch_gate = next(node for node in catalog_payload["nodes"] if node["type"] == "gate:switch")
    assert switch_gate["output_ports"] == []
    assert [field["key"] for field in switch_gate["fields"]] == [
        "cases",
        "default_port",
        "outputs",
    ]
    switch_case_fields = {field["key"]: field for field in switch_gate["fields"][0]["item_fields"]}
    assert switch_gate["fields"][0]["kind"] == "object_list"
    assert switch_gate["fields"][0]["required"]
    assert [field["key"] for field in switch_gate["fields"][0]["item_fields"]] == [
        "port",
        "label",
        "when",
        "match",
    ]
    assert switch_case_fields["port"]["required"]
    assert switch_case_fields["when"]["bindable"]
    assert switch_case_fields["match"]["bindable"]
    assert not switch_gate["fields"][1]["required"]
    assert switch_gate["fields"][2]["read_only"]
    issue_get = next(node for node in catalog_payload["nodes"] if node["type"] == "query:issue:get")
    assert [field["key"] for field in issue_get["fields"]] == ["issue_id"]
    semantic_status = next(
        node for node in catalog_payload["nodes"] if node["type"] == "query:semantic:store-status"
    )
    assert [field["key"] for field in semantic_status["fields"]] == [
        "collection_name",
        "directory_path",
    ]
    semantic_search = next(
        node for node in catalog_payload["nodes"] if node["type"] == "query:semantic:search"
    )
    assert [field["key"] for field in semantic_search["fields"]] == [
        "collection_name",
        "query_text",
        "filter",
        "limit",
        "score_threshold",
        "directory_path",
    ]
    semantic_get = next(
        node for node in catalog_payload["nodes"] if node["type"] == "query:semantic:get"
    )
    assert [field["key"] for field in semantic_get["fields"]] == [
        "collection_name",
        "point_ids",
        "directory_path",
    ]
    artifact_text = next(
        node for node in catalog_payload["nodes"] if node["type"] == "query:agent:artifact-text"
    )
    assert [field["key"] for field in artifact_text["fields"]] == [
        "session_id",
        "path",
        "vm_user",
    ]
    ado_work_items = next(
        node for node in catalog_payload["nodes"] if node["type"] == "query:ado:work-items"
    )
    assert [field["key"] for field in ado_work_items["fields"]] == [
        "directory_path",
        "project",
        "query_mode",
        "wiql",
        "current_iteration",
        "team",
        "iteration",
        "area_path",
        "states",
        "types",
        "tags_any",
        "text_search",
        "top",
        "skip",
    ]
    remote_issues = next(
        node for node in catalog_payload["nodes"] if node["type"] == "query:remote-issues"
    )
    assert [field["key"] for field in remote_issues["fields"]] == [
        "provider",
        "directory_path",
        "project",
        "repo",
        "query_mode",
        "wiql",
        "current_iteration",
        "team",
        "iteration",
        "area_path",
        "states",
        "types",
        "tags_any",
        "state",
        "labels_any",
        "labels_all",
        "assignee",
        "author",
        "mentioned",
        "milestone",
        "since",
        "text_search",
        "top",
        "skip",
    ]
    pr_list = next(
        node for node in catalog_payload["nodes"] if node["type"] == "query:pull-request:list"
    )
    assert [field["key"] for field in pr_list["fields"]] == [
        "provider",
        "repo",
        "directory_path",
        "organization_url",
        "project",
        "status",
        "creator_id",
        "reviewer_id",
        "source_ref_name",
        "target_ref_name",
        "source_repository_id",
        "repository_id",
        "min_time",
        "max_time",
        "query_time_range_type",
        "include_links",
        "max_comment_length",
        "tags",
        "top",
        "skip",
        "cursor",
    ]
    pr_get = next(
        node for node in catalog_payload["nodes"] if node["type"] == "query:pull-request:get"
    )
    assert [field["key"] for field in pr_get["fields"]] == [
        "provider",
        "repo",
        "pr_id",
        "directory_path",
        "organization_url",
        "project",
        "include_reviewers",
        "include_commits",
    ]
    assert [option["value"] for option in pr_get["fields"][0]["options"]] == [
        "azure_devops",
        "ado",
        "github",
    ]
    session_list = next(
        node for node in catalog_payload["nodes"] if node["type"] == "query:session:list"
    )
    assert [field["key"] for field in session_list["fields"]] == [
        "directory_path",
        "metadata_equals",
        "metadata_exists",
        "metadata_prefix",
        "state",
        "active_only",
        "limit",
    ]
    session_list_fields = {field["key"]: field for field in session_list["fields"]}
    assert all(field["description"] for field in session_list_fields.values())
    assert "descendants" in session_list_fields["directory_path"]["description"]
    assert "dot-path selectors" in session_list_fields["metadata_equals"]["description"]
    assert "presence" in session_list_fields["metadata_exists"]["description"]
    assert "string prefixes" in session_list_fields["metadata_prefix"]["description"]
    assert session_list_fields["state"]["kind"] == "enum"
    assert session_list_fields["state"]["bindable"] is True
    assert [option["value"] for option in session_list_fields["state"]["options"]] == [
        "active",
        "expired",
        "inactive",
        "all",
        "any",
    ]
    assert "active, expired, inactive, all, or any" in session_list_fields["state"]["description"]
    assert "expires_at" in session_list_fields["active_only"]["description"]
    assert "positive integer" in session_list_fields["limit"]["description"]
    model_usage_list = next(
        node for node in catalog_payload["nodes"] if node["type"] == "query:model-usage:list"
    )
    assert [field["key"] for field in model_usage_list["fields"]] == [
        "session_id",
        "workflow_run_id",
        "effect_id",
        "dispatch_id",
        "directory_path",
        "include_directory_descendants",
        "provider",
        "model",
        "agent_harness",
        "execution_backend",
        "normalized_usage_status",
        "request_status",
        "occurred_after",
        "occurred_before",
        "limit",
        "include_costs",
        "cost_currency",
    ]
    model_usage_list_fields = {field["key"]: field for field in model_usage_list["fields"]}
    assert all(field["description"] for field in model_usage_list_fields.values())
    assert "system.execution.trace_id" in model_usage_list_fields["workflow_run_id"]["description"]
    assert "every run" in model_usage_list_fields["workflow_run_id"]["description"]
    assert "all effects" in model_usage_list_fields["effect_id"]["description"]
    assert "all dispatches" in model_usage_list_fields["dispatch_id"]["description"]
    assert model_usage_list_fields["directory_path"]["description"]
    assert (
        "Leave blank to include all directories"
        in model_usage_list_fields["directory_path"]["description"]
    )
    assert "descendants" in model_usage_list_fields["directory_path"]["description"]
    assert model_usage_list_fields["include_directory_descendants"]["kind"] == "boolean"
    assert model_usage_list_fields["include_directory_descendants"]["default_value"] is False
    assert [option["value"] for option in model_usage_list_fields["provider"]["options"]] == [
        "openai"
    ]
    assert [option["value"] for option in model_usage_list_fields["agent_harness"]["options"]] == [
        "codex"
    ]
    assert [
        option["value"] for option in model_usage_list_fields["execution_backend"]["options"]
    ] == [
        "libvirt_vm",
        "container_worker_vm",
    ]
    assert model_usage_list_fields["normalized_usage_status"]["kind"] == "enum"
    assert [
        option["value"] for option in model_usage_list_fields["normalized_usage_status"]["options"]
    ] == [
        "available",
        "usage_unavailable",
    ]
    assert model_usage_list_fields["request_status"]["kind"] == "enum"
    assert [option["value"] for option in model_usage_list_fields["request_status"]["options"]] == [
        "success",
        "failed",
        "error",
        "cancelled",
    ]
    assert "ISO-8601" in model_usage_list_fields["occurred_after"]["description"]
    assert "positive integer" in model_usage_list_fields["limit"]["description"]
    assert model_usage_list_fields["include_costs"]["kind"] == "boolean"
    assert model_usage_list_fields["include_costs"]["default_value"] is True
    assert model_usage_list_fields["cost_currency"]["kind"] == "enum"
    assert model_usage_list_fields["cost_currency"]["default_value"] == "usd"
    assert [option["value"] for option in model_usage_list_fields["cost_currency"]["options"]] == [
        "usd"
    ]
    pr_feedback = next(
        node for node in catalog_payload["nodes"] if node["type"] == "query:pull-request:feedback"
    )
    assert [field["key"] for field in pr_feedback["fields"]] == [
        "provider",
        "repo",
        "pr_id",
        "directory_path",
        "organization_url",
        "project",
        "cursor",
    ]
    communication_messages = next(
        node for node in catalog_payload["nodes"] if node["type"] == "query:communication:messages"
    )
    assert [field["key"] for field in communication_messages["fields"]] == [
        "communication_target_id",
        "limit",
        "cursor",
        "before_cursor",
        "thread_id",
        "directory_path",
    ]
    communication_messages_fields = {
        field["key"]: field for field in communication_messages["fields"]
    }
    assert communication_messages_fields["communication_target_id"]["kind"] == "enum"
    assert (
        "read-capable Teams or Discord"
        in communication_messages_fields["communication_target_id"]["description"]
    )
    assert communication_messages_fields["limit"]["default_value"] == 50
    assert "Maximum 200" in communication_messages_fields["limit"]["description"]
    assert (
        "Discord targets reject thread_id"
        in communication_messages_fields["thread_id"]["description"]
    )
    vm_create = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:vm:create"
    )
    assert any(field["key"] == "allowed_script_names" for field in vm_create["fields"])
    assert any(field["key"] == "metadata" for field in vm_create["fields"])
    agent_create = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:agent:create"
    )
    assert {field["key"] for field in agent_create["fields"]} == {
        "execution_backend",
        "agent_provider",
        "provider",
        "vm_name",
        "session_id",
        "existing_agent_behavior",
        "labels",
        "model",
        "model_provider_mode",
        "vm_user",
        "vm_image",
        "ttl_hours",
        "allowed_tools",
        "connector_grants",
        "tool_argument_policies",
        "allowed_script_names",
        "communication_target_ids",
        "discord_channels",
        "teams_channel_allowlist",
        "teams_chat_allowlist",
        "teams_user_allowlist",
        "email_mailboxes",
        "email_folders",
        "communication_file_receive_enabled",
        "communication_file_send_enabled",
        "communication_file_require_approval_for_send",
        "communication_file_max_bytes",
        "web_search_enabled",
        "dotnet",
        "nuget",
        "mssql",
        "mssql_init_root",
        "playwright",
        "codex_skills",
        "container_image",
        "npm_registry_url",
        "directory_path",
        "metadata",
    }
    assert [field["key"] for field in agent_create["fields"][:3]] == [
        "execution_backend",
        "agent_provider",
        "provider",
    ]
    agent_create_fields = {field["key"]: field for field in agent_create["fields"]}
    assert agent_create_fields["execution_backend"]["required"] is True
    assert agent_create_fields["agent_provider"]["kind"] == "enum"
    assert agent_create_fields["agent_provider"]["options"] == [
        {"value": "codex", "label": "Codex"},
        {"value": "claude", "label": "Claude"},
        {
            "value": "copilot",
            "label": "Copilot",
            "metadata": {"execution_backends": ["libvirt_vm"]},
        },
    ]
    assert agent_create_fields["model_provider_mode"]["options"] == [
        {"value": "openai_public", "label": "Public OpenAI"},
        {"value": "local_openai", "label": "Local OpenAI"},
        {"value": "azure_openai", "label": "Azure OpenAI"},
    ]
    assert agent_create_fields["provider"]["label"] == "VM provider"
    assert agent_create_fields["provider"]["metadata"] == {"execution_backends": ["libvirt_vm"]}
    assert agent_create_fields["vm_name"]["metadata"] == {"execution_backends": ["libvirt_vm"]}
    assert agent_create_fields["vm_user"]["metadata"] == {"execution_backends": ["libvirt_vm"]}
    assert agent_create_fields["mssql"]["metadata"] == {"execution_backends": ["libvirt_vm"]}
    assert agent_create_fields["mssql_init_root"]["metadata"] == {
        "execution_backends": ["libvirt_vm"]
    }
    assert agent_create_fields["container_image"]["metadata"] == {
        "execution_backends": ["container_worker_vm"]
    }
    assert agent_create_fields["npm_registry_url"]["metadata"] == {
        "execution_backends": ["container_worker_vm"]
    }
    assert agent_create_fields["labels"]["kind"] == "string_map"
    assert agent_create_fields["labels"]["description"]
    assert agent_create_fields["communication_target_ids"]["kind"] == "string_list"
    assert "communication target" in agent_create_fields["communication_target_ids"]["description"]
    assert agent_create_fields["communication_target_ids"]["metadata"] == {"selection_only": True}
    assert agent_create_fields["metadata"]["kind"] == "json"
    assert agent_create_fields["metadata"]["json_value_type"] == "object"
    assert agent_create_fields["metadata"]["description"]
    assert agent_create_fields["directory_path"]["description"]
    assert agent_create_fields["container_image"]["bindable"]
    assert "approved container image" in agent_create_fields["container_image"]["description"]
    assert agent_create_fields["vm_image"]["bindable"]
    assert "libvirt-only selector" in agent_create_fields["vm_image"]["description"]
    assert "approved libvirt VM template" in agent_create_fields["vm_image"]["description"]
    assert "base-image" in agent_create_fields["vm_image"]["description"]
    assert agent_create_fields["npm_registry_url"]["bindable"]
    assert agent_create_fields["web_search_enabled"]["kind"] == "boolean"
    assert agent_create_fields["web_search_enabled"]["bindable"] is True
    assert "web_search" in agent_create_fields["web_search_enabled"]["description"]
    assert agent_create_fields["allowed_tools"]["options"]
    assert {
        "value": ToolName.RUN_SCRIPT.value,
        "label": ToolName.RUN_SCRIPT.value.replace("_", " "),
    } in agent_create_fields["allowed_tools"]["options"]
    assert vm_create["fields"]
    vm_create_fields = {field["key"]: field for field in vm_create["fields"]}
    assert vm_create_fields["tool_argument_policies"]["options"]
    assert {
        "value": ToolName.RUN_SCRIPT.value,
        "label": ToolName.RUN_SCRIPT.value.replace("_", " "),
    } in vm_create_fields["tool_argument_policies"]["options"]
    assert agent_create_fields["tool_argument_policies"]["options"]
    assert {
        "value": ToolName.RUN_SCRIPT.value,
        "label": ToolName.RUN_SCRIPT.value.replace("_", " "),
    } in agent_create_fields["tool_argument_policies"]["options"]
    execution_backend = next(
        field for field in agent_create["fields"] if field["key"] == "execution_backend"
    )
    assert execution_backend["options"] == [
        {"value": "libvirt_vm", "label": "Libvirt VM"},
        {"value": "container_worker_vm", "label": "Container worker VM"},
    ]
    existing_agent_behavior = next(
        field for field in agent_create["fields"] if field["key"] == "existing_agent_behavior"
    )
    assert existing_agent_behavior["options"] == [
        {"value": "upsert", "label": "Upsert existing agent"},
        {"value": "noop", "label": "No-op if existing"},
    ]
    agent_destroy = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:agent:destroy"
    )
    assert [field["key"] for field in agent_destroy["fields"]] == [
        "target",
        "session_id",
        "vm_name",
        "execution_backend",
        "directory_path",
    ]
    session_renew = next(
        node
        for node in catalog_payload["nodes"]
        if node["type"] == "effect:session:renew-if-expired"
    )
    assert [field["key"] for field in session_renew["fields"]] == [
        "target",
        "session_id",
        "vm_name",
        "extend_by",
        "renew_if_expires_within",
        "directory_path",
    ]
    session_renew_fields = {field["key"]: field for field in session_renew["fields"]}
    assert "from now" in session_renew_fields["extend_by"]["description"]
    assert "8h" in session_renew_fields["extend_by"]["description"]
    assert "PT8H" in session_renew_fields["extend_by"]["description"]
    assert "pre-expiry window" in session_renew_fields["renew_if_expires_within"]["description"]
    assert "30m" in session_renew_fields["renew_if_expires_within"]["description"]
    assert "4h" in session_renew_fields["renew_if_expires_within"]["description"]
    artifact_stage = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:artifact:stage"
    )
    assert [field["key"] for field in artifact_stage["fields"]] == [
        "target",
        "session_id",
        "vm_name",
        "path",
        "destination_path",
        "vm_user",
    ]
    artifact_publish = next(
        node
        for node in catalog_payload["nodes"]
        if node["type"] == "effect:artifact:publish-artifacts"
    )
    assert [field["key"] for field in artifact_publish["fields"]] == [
        "target",
        "session_id",
        "vm_name",
        "key",
        "source_paths",
        "vm_user",
    ]
    artifact_import = next(
        node
        for node in catalog_payload["nodes"]
        if node["type"] == "effect:artifact:import-published"
    )
    assert [field["key"] for field in artifact_import["fields"]] == [
        "target",
        "session_id",
        "vm_name",
        "key",
        "destination_path",
        "overwrite",
        "vm_user",
    ]
    agent_clone_repo = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:agent:clone-repo"
    )
    assert [field["key"] for field in agent_clone_repo["fields"]] == [
        "target",
        "session_id",
        "vm_name",
        "managed_repo_id",
        "clone_branch",
            "vm_user",
            "git_access_mode",
            "pinned_ref",
            "pinned_commit_sha",
            "git_author_name",
        "git_author_email",
        "git_committer_name",
        "git_committer_email",
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
        "allowed_source_roots",
        "files",
    ]
    vm_clone_repo = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:vm:clone-repo"
    )
    assert [field["key"] for field in vm_clone_repo["fields"]] == [
        "target",
        "session_id",
        "vm_name",
        "managed_repo_id",
        "clone_branch",
        "vm_user",
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
    assert {field["key"] for field in vm_create["fields"]} == {
        "vm_name",
        "session_id",
        "labels",
        "model",
        "vm_user",
        "ttl_hours",
        "allowed_tools",
        "connector_grants",
        "tool_argument_policies",
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
        "metadata",
    }


def test_admin_web_workflow_catalog_lists_directory_communication_send_targets(
    tmp_path: Path,
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    created = service.directory_admin.create_child(parent="root", name="ops")
    assert created.ok is True and created.data is not None
    root_write = service.operations_admin.write_directory_domain_config(
        "root",
        COMMUNICATION_TARGETS_DOMAIN,
        {
            "targets": [
                {
                    "id": "root-chat",
                    "provider": "teams",
                    "kind": "teams",
                    "address": "team/channel",
                    "policy": {"capabilities": ["write", "read"]},
                },
                {
                    "id": "root-notify",
                    "provider": "teams",
                    "kind": "teams",
                    "address": "team/notify",
                    "policy": {"capabilities": ["notify"]},
                },
                {
                    "id": "root-read-chat",
                    "provider": "teams",
                    "kind": "teams",
                    "address": "team/read",
                    "policy": {"capabilities": ["read"]},
                },
                {
                    "id": "root-read-discord",
                    "provider": "discord",
                    "kind": "discord",
                    "address": "123456789012345682",
                    "policy": {"capabilities": ["read"]},
                },
                {
                    "id": "root-read-user",
                    "provider": "teams",
                    "kind": "teams",
                    "address": "user:operator@example.com",
                    "policy": {"capabilities": ["read"]},
                },
                {
                    "id": "root-email-notify",
                    "provider": "email",
                    "kind": "email",
                    "address": "root@example.com",
                    "policy": {"capabilities": ["notify"], "auth_profile_ref": "email.root"},
                },
                {
                    "id": "mail-read",
                    "provider": "email",
                    "kind": "email",
                    "address": "read@example.com",
                    "policy": {"capabilities": ["read"], "auth_profile_ref": "email.read"},
                },
                {
                    "id": "root-discord-reply-only",
                    "provider": "discord",
                    "kind": "discord",
                    "address": "123456789012345681",
                    "policy": {"capabilities": ["reply", "read"]},
                },
                {
                    "id": "mail-write",
                    "provider": "email",
                    "kind": "email",
                    "address": "ops@example.com",
                    "policy": {"capabilities": ["write"], "auth_profile_ref": "email.ops"},
                },
                {
                    "id": "mail-read-write",
                    "provider": "email",
                    "kind": "email",
                    "address": "full@example.com",
                    "policy": {
                        "capabilities": ["read", "write"],
                        "auth_profile_ref": "email.full",
                    },
                },
            ]
        },
    )
    child_write = service.operations_admin.write_directory_domain_config(
        "root/ops",
        COMMUNICATION_TARGETS_DOMAIN,
        {
            "targets": [
                {"id": "root-chat", "disabled": True},
                {
                    "id": "ops-chat",
                    "provider": "teams",
                    "kind": "teams",
                    "address": "team/ops",
                    "policy": {"capabilities": ["reply", "read"]},
                },
                {
                    "id": "ops-discord",
                    "provider": "discord",
                    "kind": "discord",
                    "address": "123456789012345678",
                    "policy": {"capabilities": ["write", "read"]},
                },
                {
                    "id": "ops-read-chat",
                    "provider": "teams",
                    "kind": "teams",
                    "address": "team/ops-read",
                    "policy": {"capabilities": ["read"]},
                },
                {
                    "id": "ops-read-discord",
                    "provider": "discord",
                    "kind": "discord",
                    "address": "123456789012345683/123456789012345684",
                    "policy": {"capabilities": ["read"]},
                },
                {
                    "id": "ops-email-notify",
                    "provider": "email",
                    "kind": "email",
                    "address": "ops@example.com",
                    "policy": {
                        "capabilities": ["notify"],
                        "auth_profile_ref": "email.ops",
                    },
                },
                {
                    "id": "discord-reply-only",
                    "provider": "discord",
                    "kind": "discord",
                    "address": "123456789012345679",
                    "policy": {"capabilities": ["reply"]},
                },
            ]
        },
    )
    assert root_write.ok is True
    assert child_write.ok is True
    client = TestClient(create_app(service))

    root_response = client.get("/api/workflow-editor/catalog?directory_path=root")
    ops_response = client.get("/api/workflow-editor/catalog?directory_path=root/ops")

    assert root_response.status_code == 200
    assert ops_response.status_code == 200

    def get_node(payload: dict[str, object], node_type: str) -> dict[str, object]:
        nodes = payload["nodes"]
        assert isinstance(nodes, list)
        return next(
            candidate
            for candidate in nodes
            if isinstance(candidate, dict) and candidate["type"] == node_type
        )

    def get_field(payload: dict[str, object], node_type: str, field_key: str) -> dict[str, object]:
        node = get_node(payload, node_type)
        field = next(candidate for candidate in node["fields"] if candidate["key"] == field_key)
        return field

    def get_options(
        payload: dict[str, object],
        node_type: str,
        field_key: str = "communication_target_id",
    ) -> dict[str, dict[str, object]]:
        field = get_field(payload, node_type, field_key)
        assert field["kind"] in {"enum", "string", "string_list"}
        options = field["options"]
        assert isinstance(options, list)
        return {
            option["value"]: option
            for option in options
            if isinstance(option, dict) and isinstance(option.get("value"), str)
        }

    root_payload = root_response.json()
    ops_payload = ops_response.json()

    send_root = get_options(root_payload, "effect:communication:send-message")
    send_ops = get_options(ops_payload, "effect:communication:send-message")
    notify_root = get_options(root_payload, "effect:notification:send")
    notify_ops = get_options(ops_payload, "effect:notification:send")
    tool_approval_root = get_options(
        root_payload,
        "effect:session:approve-mcp-tools-for-run",
        "approval_notification_target_ids",
    )
    tool_approval_ops = get_options(
        ops_payload,
        "effect:session:approve-mcp-tools-for-run",
        "approval_notification_target_ids",
    )
    message_read_root = get_options(root_payload, "query:communication:messages")
    message_read_ops = get_options(ops_payload, "query:communication:messages")
    trigger_read_root = get_options(
        root_payload,
        "trigger:communication:new-message",
        "communication_target_ids",
    )
    trigger_read_ops = get_options(
        ops_payload,
        "trigger:communication:new-message",
        "communication_target_ids",
    )
    agent_create_root = get_options(
        root_payload,
        "effect:agent:create",
        "communication_target_ids",
    )
    agent_create_ops = get_options(
        ops_payload,
        "effect:agent:create",
        "communication_target_ids",
    )

    send_target_description = get_field(
        root_payload, "effect:communication:send-message", "communication_target_id"
    )["description"]
    notify_target_description = get_field(
        root_payload, "effect:notification:send", "communication_target_id"
    )["description"]
    send_thread_description = get_field(
        root_payload, "effect:communication:send-message", "thread_id"
    )["description"]
    notify_thread_description = get_field(root_payload, "effect:notification:send", "thread_id")[
        "description"
    ]

    assert "write or reply capability" in send_target_description
    assert "notify capability" in notify_target_description
    assert "top-level send" in send_thread_description
    assert "reply hint" in notify_thread_description

    assert set(send_root) == {"root-chat", "mail-write", "mail-read-write"}
    assert set(send_ops) == {"ops-chat", "ops-discord", "mail-write", "mail-read-write"}
    assert set(notify_root) == {"root-notify", "root-email-notify"}
    assert set(notify_ops) == {"root-notify", "root-email-notify", "ops-email-notify"}
    assert set(tool_approval_root) == {"root-notify", "root-email-notify"}
    assert set(tool_approval_ops) == {
        "root-notify",
        "root-email-notify",
        "ops-email-notify",
    }
    assert set(message_read_root) == {
        "root-chat",
        "root-discord-reply-only",
        "root-read-chat",
        "root-read-discord",
        "mail-read",
        "mail-read-write",
    }
    assert set(message_read_ops) == {
        "root-discord-reply-only",
        "root-read-chat",
        "root-read-discord",
        "mail-read",
        "mail-read-write",
        "ops-chat",
        "ops-discord",
        "ops-read-chat",
        "ops-read-discord",
    }
    assert set(trigger_read_root) == set(message_read_root)
    assert set(trigger_read_ops) == set(message_read_ops)
    assert set(agent_create_root) == {
        "mail-read-write",
        "root-chat",
        "root-discord-reply-only",
        "root-read-chat",
        "root-read-discord",
    }
    assert set(agent_create_ops) == {
        "mail-read-write",
        "root-discord-reply-only",
        "root-read-chat",
        "root-read-discord",
        "ops-chat",
        "ops-discord",
        "ops-read-chat",
        "ops-read-discord",
        "discord-reply-only",
    }
    agent_create_targets_field = get_field(
        root_payload,
        "effect:agent:create",
        "communication_target_ids",
    )
    assert agent_create_targets_field["kind"] == "string_list"
    assert "directory communication target catalogue" in agent_create_targets_field["description"]

    root_chat = send_root["root-chat"]
    assert root_chat["metadata"]["provider"] == "teams"
    assert root_chat["metadata"]["address"] == "team/channel"
    assert root_chat["metadata"]["target_shape"] == "teams_channel"
    read_root_chat = message_read_root["root-chat"]
    assert read_root_chat["metadata"]["policy_capabilities"] == ["read", "write"]
    assert root_chat["metadata"]["policy_capabilities"] == ["read", "write"]
    assert root_chat["metadata"]["thread_id"]["top_level"]["supported"] is True
    assert root_chat["metadata"]["thread_id"]["reply"]["supported"] is False
    assert root_chat["metadata"]["thread_id"]["reply"]["supported_target_shapes"] == [
        "teams_channel"
    ]

    ops_chat = send_ops["ops-chat"]
    assert ops_chat["metadata"]["provider"] == "teams"
    assert ops_chat["metadata"]["target_shape"] == "teams_channel"
    assert ops_chat["metadata"]["policy_capabilities"] == ["read", "reply"]
    assert ops_chat["metadata"]["thread_id"]["top_level"]["supported"] is False
    assert ops_chat["metadata"]["thread_id"]["reply"]["supported"] is True

    ops_discord = send_ops["ops-discord"]
    assert ops_discord["metadata"]["provider"] == "discord"
    assert ops_discord["metadata"]["target_shape"] == "discord_channel"
    assert ops_discord["metadata"]["policy_capabilities"] == ["read", "write"]
    assert ops_discord["metadata"]["thread_id"]["top_level"]["supported"] is True
    assert ops_discord["metadata"]["thread_id"]["reply"]["supported"] is False

    root_notify = notify_root["root-notify"]
    assert root_notify["metadata"]["provider"] == "teams"
    assert root_notify["metadata"]["target_shape"] == "teams_channel"
    assert root_notify["metadata"]["policy_capabilities"] == ["notify"]
    assert root_notify["metadata"]["thread_id"]["reply_hint"]["supported"] is True

    root_email_notify = notify_root["root-email-notify"]
    assert root_email_notify["metadata"]["provider"] == "email"
    assert root_email_notify["metadata"]["target_shape"] == "email_recipient"
    assert root_email_notify["metadata"]["policy_capabilities"] == ["notify"]
    assert root_email_notify["metadata"]["thread_id"]["reply_hint"]["supported"] is False

    ops_email_notify = notify_ops["ops-email-notify"]
    assert ops_email_notify["metadata"]["provider"] == "email"
    assert ops_email_notify["metadata"]["target_shape"] == "email_recipient"
    assert ops_email_notify["metadata"]["policy_capabilities"] == ["notify"]
    assert ops_email_notify["metadata"]["thread_id"]["reply_hint"]["supported"] is False

    root_read_chat = trigger_read_root["root-read-chat"]
    assert root_read_chat["metadata"]["provider"] == "teams"
    assert root_read_chat["metadata"]["address"] == "team/read"
    assert root_read_chat["metadata"]["target_shape"] == "teams_channel"
    assert root_read_chat["metadata"]["required_capability"] == "read"
    assert root_read_chat["metadata"]["policy_capabilities"] == ["read"]

    ops_read_discord = trigger_read_ops["ops-read-discord"]
    assert ops_read_discord["metadata"]["provider"] == "discord"
    assert ops_read_discord["metadata"]["target_shape"] == "discord_channel"
    assert ops_read_discord["metadata"]["required_capability"] == "read"


def test_workflow_editor_default_dedupe_node_uses_supported_event_binding() -> None:
    app_source = (
        Path(__file__).resolve().parents[1] / "frontend" / "workflow-editor" / "src" / "App.tsx"
    ).read_text(encoding="utf-8")

    assert "dedupe:${{system.execution.event_id}}" in app_source
    assert "dedupe:${{system.event_id}}" not in app_source


def test_admin_web_exposes_deployment_status_from_runtime(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    status_path = service.config.runtime_dir / "deployment-status.json"
    status_path.write_text(
        json.dumps(
            {
                "active": True,
                "message": "Control plane update in progress.",
                "source": "ansible",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "ttl_seconds": 3600,
            }
        ),
        encoding="utf-8",
    )
    client = TestClient(create_app(service))

    response = client.get("/api/admin/deployment-status")
    state_response = client.get("/api/admin/state")

    assert response.status_code == 200
    assert response.json()["active"] is True
    assert response.json()["message"] == "Control plane update in progress."
    assert state_response.status_code == 200
    assert state_response.json()["deployment"]["active"] is True


def test_admin_web_deployment_status_is_public_when_auth_required(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AUTH_MODE", "local_password")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_PASSWORD", "secret")
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    (service.config.runtime_dir / "deployment-status.json").write_text(
        json.dumps({"active": True, "message": "Deploying", "ttl_seconds": 3600}),
        encoding="utf-8",
    )
    client = TestClient(create_app(service))

    public_response = client.get("/api/admin/deployment-status")
    protected_response = client.get("/api/admin/state")

    assert public_response.status_code == 200
    assert public_response.json()["active"] is True
    assert protected_response.status_code == 401


def test_admin_web_release_info_is_authenticated_and_returns_project_metadata(
    monkeypatch, tmp_path: Path
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    monkeypatch.setenv("EVANDER_REPO_VERSION", "build-abc123")
    monkeypatch.setattr(
        release_metadata_module.importlib_metadata,
        "version",
        lambda name: (_ for _ in ()).throw(
            release_metadata_module.importlib_metadata.PackageNotFoundError(name)
        ),
    )
    (service.project_dir / "pyproject.toml").write_text(
        '[project]\nname = "evander"\nversion = "9.8.7"\n',
        encoding="utf-8",
    )
    (service.project_dir / "CHANGELOG.md").write_text(
        "# Changelog\n\n"
        "## 2026-05-21 - Release Metadata\n\n"
        "- Added release metadata reader.\n"
        "- Fixed missing build identity fallback.\n",
        encoding="utf-8",
    )
    news_asset_dir = service.project_dir / "docs" / "assets" / "news"
    news_asset_dir.mkdir(parents=True)
    (news_asset_dir / "truman-signature.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"></svg>',
        encoding="utf-8",
    )
    (news_asset_dir / "truman-signature-inverse.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"></svg>',
        encoding="utf-8",
    )
    (service.project_dir / "NEWS.md").write_text(
        "# News\n\n"
        "## 2026-05-21 - Control Plane Confidence\n\n"
        "Operators now get release context and deployment hints in one place.\n\n"
        "### Practical tips\n\n"
        "- Open the dashboard first.\n\n"
        "- Download the changelog when you need the full record.\n\n"
        '![Truman signature](docs/assets/news/truman-signature.svg "Signed by Truman")\n\n'
        "The dashboard news feed explains what changed and how to use it.\n"
        "\n"
        "## 2026-05-20 - Earlier Update\n\n"
        "Older posts remain available without loading the whole news archive.\n",
        encoding="utf-8",
    )
    client = TestClient(create_app(service))

    response = client.get("/api/admin/release-info")
    older_response = client.get("/api/admin/release-info?news_index=1")
    invalid_response = client.get("/api/admin/release-info?news_index=-1")
    asset_response = client.get("/api/admin/news/assets/truman-signature.svg")
    blocked_asset_response = client.get("/api/admin/news/assets/%2e%2e/NEWS.md")

    assert response.status_code == 200
    assert response.json() == {
        "version": "9.8.7",
        "version_source": str(service.project_dir / "pyproject.toml"),
        "build": "build-abc123",
        "build_source": "env:EVANDER_REPO_VERSION",
        "changelog": {
            "source": "CHANGELOG.md",
            "path": str(service.project_dir / "CHANGELOG.md"),
            "heading": "2026-05-21 - Release Metadata",
            "entries": [
                {"text": "Added release metadata reader.", "kind": "feature"},
                {"text": "Fixed missing build identity fallback.", "kind": "bugfix"},
            ],
        },
        "news": {
            "source": "NEWS.md",
            "path": str(service.project_dir / "NEWS.md"),
            "index": 0,
            "heading": "2026-05-21 - Control Plane Confidence",
            "blocks": [
                {
                    "kind": "paragraph",
                    "text": "Operators now get release context and deployment hints in one place.",
                    "level": None,
                    "items": [],
                    "src": None,
                    "dark_src": None,
                    "alt": None,
                    "title": None,
                },
                {
                    "kind": "heading",
                    "text": "Practical tips",
                    "level": 3,
                    "items": [],
                    "src": None,
                    "dark_src": None,
                    "alt": None,
                    "title": None,
                },
                {
                    "kind": "list",
                    "text": None,
                    "level": None,
                    "items": [
                        "Open the dashboard first.",
                        "Download the changelog when you need the full record.",
                    ],
                    "src": None,
                    "dark_src": None,
                    "alt": None,
                    "title": None,
                },
                {
                    "kind": "image",
                    "text": None,
                    "level": None,
                    "items": [],
                    "src": "/api/admin/news/assets/truman-signature.svg",
                    "dark_src": "/api/admin/news/assets/truman-signature-inverse.svg",
                    "alt": "Truman signature",
                    "title": "Signed by Truman",
                },
                {
                    "kind": "paragraph",
                    "text": "The dashboard news feed explains what changed and how to use it.",
                    "level": None,
                    "items": [],
                    "src": None,
                    "dark_src": None,
                    "alt": None,
                    "title": None,
                },
            ],
            "has_previous": False,
            "has_next": True,
            "previous_index": None,
            "next_index": 1,
        },
        "missing_reasons": [],
    }
    assert "Older posts remain available" not in json.dumps(response.json())
    assert older_response.status_code == 200
    assert older_response.json()["news"] == {
        "source": "NEWS.md",
        "path": str(service.project_dir / "NEWS.md"),
        "index": 1,
        "heading": "2026-05-20 - Earlier Update",
        "blocks": [
            {
                "kind": "paragraph",
                "text": "Older posts remain available without loading the whole news archive.",
                "level": None,
                "items": [],
                "src": None,
                "dark_src": None,
                "alt": None,
                "title": None,
            },
        ],
        "has_previous": True,
        "has_next": False,
        "previous_index": 0,
        "next_index": None,
    }
    assert invalid_response.status_code == 400
    assert invalid_response.json()["error"] == "news_index must be greater than or equal to 0."
    assert asset_response.status_code == 200
    assert asset_response.content.startswith(b'<svg xmlns="http://www.w3.org/2000/svg"')
    assert blocked_asset_response.status_code == 404

    download_response = client.get("/api/admin/release-info/changelog")
    assert download_response.status_code == 200
    assert download_response.content.startswith(b"# Changelog\n")
    assert "attachment" in download_response.headers["content-disposition"]
    assert "CHANGELOG.md" in download_response.headers["content-disposition"]


def test_admin_web_release_info_is_protected_when_auth_required(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AUTH_MODE", "local_password")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_PASSWORD", "secret")
    service = build_admin_runtime(write_config(tmp_path))
    client = TestClient(create_app(service))

    protected_response = client.get("/api/admin/release-info")
    protected_download_response = client.get("/api/admin/release-info/changelog")
    protected_asset_response = client.get("/api/admin/news/assets/truman-signature.svg")
    state_response = client.get("/api/admin/state")

    assert protected_response.status_code == 401
    assert protected_download_response.status_code == 401
    assert protected_asset_response.status_code == 401
    assert state_response.status_code == 401


def test_admin_web_ignores_stale_deployment_status(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    stale_started_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    (service.config.runtime_dir / "deployment-status.json").write_text(
        json.dumps(
            {
                "active": True,
                "message": "Deploying",
                "started_at": stale_started_at.isoformat(),
                "ttl_seconds": 60,
            }
        ),
        encoding="utf-8",
    )
    client = TestClient(create_app(service))

    response = client.get("/api/admin/deployment-status")

    assert response.status_code == 200
    assert response.json()["active"] is False
    assert response.json()["stale"] is True


def test_admin_web_accepts_naive_deployment_status_timestamps(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    naive_timestamp = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    (service.config.runtime_dir / "deployment-status.json").write_text(
        json.dumps(
            {
                "active": True,
                "message": "Deploying",
                "started_at": naive_timestamp,
                "updated_at": naive_timestamp,
                "ttl_seconds": 3600,
            }
        ),
        encoding="utf-8",
    )
    client = TestClient(create_app(service))

    response = client.get("/api/admin/deployment-status")
    state_response = client.get("/api/admin/state")

    assert response.status_code == 200
    assert response.json()["active"] is True
    assert response.json()["updated_at"].endswith(("Z", "+00:00"))
    assert state_response.status_code == 200
    assert state_response.json()["deployment"]["active"] is True


def test_admin_web_exposes_capacity_health_endpoint(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    payload = CapacityHealthPayload(
        generated_at=datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc),
        control_plane=CapacityControlPlanePayload(
            disk=DiskUsageTelemetry(
                scope="control_plane_runtime",
                status=TelemetryStatus.AVAILABLE,
                health=HealthState.OK,
                thresholds=DiskThresholds(source="test_override"),
                total_bytes=10_000,
                used_bytes=4_000,
                free_bytes=6_000,
                used_percent=40.0,
                free_percent=60.0,
            ),
            storage=CapacityControlPlaneStoragePayload(
                total=DirectorySizeTelemetry(
                    scope="control_plane_known_storage",
                    status=TelemetryStatus.AVAILABLE,
                    health=HealthState.OK,
                    size_bytes=2_048,
                    file_count=2,
                    directory_count=1,
                    symlink_count=0,
                ),
                areas=[
                    DirectorySizeTelemetry(
                        scope="control_plane_session_store",
                        status=TelemetryStatus.AVAILABLE,
                        health=HealthState.OK,
                        size_bytes=1_024,
                        file_count=1,
                        directory_count=1,
                        symlink_count=0,
                    )
                ],
            ),
        ),
        sessions=CapacitySessionSummaryPayload(
            total_sessions=2,
            lifecycle_counts={"active": 1, "retained": 1},
            status_counts={"running": 1, "stopped": 1},
            backend_counts={"libvirt_vm": 1, "container_worker_vm": 1},
            directory_counts={"root": 2},
            age_buckets={"lt_1h": 1, "lt_1d": 1},
            storage=CapacitySessionStoragePayload(
                measured_total=DirectorySizeTelemetry(
                    scope="session_storage:container_worker_vm",
                    status=TelemetryStatus.AVAILABLE,
                    health=HealthState.OK,
                    size_bytes=512,
                    file_count=1,
                    directory_count=1,
                    symlink_count=0,
                ),
                measured_session_count=1,
                largest_sessions=[
                    SessionStorageSample(
                        session_id="session-a",
                        size_bytes=512,
                        backend="container_worker_vm",
                        directory_path="sessions/session-a",
                        created_at=datetime(2026, 5, 21, 11, 0, tzinfo=timezone.utc),
                        updated_at=datetime(2026, 5, 21, 11, 30, tzinfo=timezone.utc),
                    )
                ],
                oldest_sessions=[
                    SessionStorageSample(
                        session_id="session-a",
                        size_bytes=512,
                        backend="container_worker_vm",
                        directory_path="sessions/session-a",
                        created_at=datetime(2026, 5, 21, 11, 0, tzinfo=timezone.utc),
                        updated_at=datetime(2026, 5, 21, 11, 30, tzinfo=timezone.utc),
                    )
                ],
                missing_telemetry=[
                    DirectorySizeTelemetry(
                        scope="session_storage:libvirt_vm",
                        status=TelemetryStatus.UNSUPPORTED,
                        health=HealthState.UNKNOWN,
                        error_count=1,
                        errors=[
                            TelemetryError(
                                status=TelemetryStatus.UNSUPPORTED,
                                message="VM-local session storage is not host-readable",
                            )
                        ],
                    )
                ],
            ),
        ),
        workers=CapacityWorkerTelemetrySummaryPayload(
            total_workers=1,
            reachable_workers=0,
            telemetry_available_workers=0,
            entries=[
                CapacityWorkerTelemetryEntryPayload(
                    worker_url="http://worker.example.test",
                    directory_paths=["root"],
                    discovered_from=["configured:root"],
                    reachable=False,
                    healthz_status=TelemetryStatus.MISSING,
                    telemetry_status=TelemetryStatus.MISSING,
                    errors=[
                        TelemetryError(
                            status=TelemetryStatus.MISSING,
                            message="worker session storage telemetry is unavailable",
                        )
                    ],
                )
            ],
        ),
        queue=CapacityQueueTelemetryPayload(
            status=TelemetryStatus.AVAILABLE,
            health=HealthState.OK,
            backend="json",
            active_record_count=1,
            pending_count=1,
            inflight_count=0,
            oldest_event_id="evt-1",
            oldest_occurred_at=datetime(2026, 5, 21, 11, 59, tzinfo=timezone.utc),
            oldest_age_seconds=60.0,
        ),
        prompts=CapacityPromptTelemetryPayload(
            status=TelemetryStatus.MISSING,
            health=HealthState.UNKNOWN,
            unread_count=0,
            errors=[
                TelemetryError(
                    status=TelemetryStatus.MISSING,
                    message="agent prompt telemetry is unavailable",
                )
            ],
        ),
    )
    service.capacity_health_admin.read_capacity_health = MethodType(
        lambda self: payload, service.capacity_health_admin
    )
    client = TestClient(create_app(service))

    response = client.get("/api/admin/capacity-health")

    assert response.status_code == 200
    response_payload = response.json()
    assert response_payload["control_plane"]["disk"]["scope"] == "control_plane_runtime"
    assert response_payload["sessions"]["directory_counts"] == {"root": 2}
    assert response_payload["workers"]["entries"][0]["telemetry_status"] == "missing"
    assert response_payload["queue"]["oldest_event_id"] == "evt-1"
    assert response_payload["prompts"]["status"] == "missing"


def test_admin_web_capacity_health_requires_auth_when_enabled(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AUTH_MODE", "local_password")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_PASSWORD", "secret")
    service = build_admin_runtime(write_config(tmp_path))
    client = TestClient(create_app(service))

    response = client.get("/api/admin/capacity-health")

    assert response.status_code == 401


def test_admin_web_release_info_uses_project_metadata(monkeypatch, tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    monkeypatch.setenv("EVANDER_REPO_VERSION", "build-abc123")
    monkeypatch.setattr(
        release_metadata_module.importlib_metadata,
        "version",
        lambda name: (_ for _ in ()).throw(
            release_metadata_module.importlib_metadata.PackageNotFoundError(name)
        ),
    )
    (service.project_dir / "pyproject.toml").write_text(
        '[project]\nname = "evander"\nversion = "9.8.7"\n',
        encoding="utf-8",
    )
    (service.project_dir / "CHANGELOG.md").write_text(
        "# Changelog\n\n"
        "## 2026-05-21 - Release Metadata\n\n"
        "- Added release metadata reader.\n"
        "- Fixed missing build identity fallback.\n"
        "\n"
        "## 2026-05-20 - Older Entry\n\n"
        "- Added something else.\n",
        encoding="utf-8",
    )
    (service.project_dir / "NEWS.md").write_text(
        "# News\n\n"
        "## 2026-05-21 - Control Plane Confidence\n\n"
        "Operators now get release context and deployment hints in one place.\n\n"
        "The dashboard news feed explains what changed and how to use it.\n"
        "\n"
        "## 2026-05-20 - Earlier Update\n\n"
        "Older posts remain available without loading the whole news archive.\n",
        encoding="utf-8",
    )

    release_info = service.operations_admin.read_release_info()
    older_release_info = service.operations_admin.read_release_info(news_index=1)

    assert release_info.version == "9.8.7"
    assert release_info.version_source == str(service.project_dir / "pyproject.toml")
    assert release_info.build == "build-abc123"
    assert release_info.build_source == "env:EVANDER_REPO_VERSION"
    assert release_info.missing_reasons == []
    assert release_info.changelog is not None
    assert release_info.changelog.source == "CHANGELOG.md"
    assert release_info.changelog.path == str(service.project_dir / "CHANGELOG.md")
    assert release_info.changelog.heading == "2026-05-21 - Release Metadata"
    assert [entry.text for entry in release_info.changelog.entries] == [
        "Added release metadata reader.",
        "Fixed missing build identity fallback.",
    ]
    assert [entry.kind for entry in release_info.changelog.entries] == [
        "feature",
        "bugfix",
    ]
    assert release_info.news is not None
    assert release_info.news.source == "NEWS.md"
    assert release_info.news.path == str(service.project_dir / "NEWS.md")
    assert release_info.news.index == 0
    assert release_info.news.heading == "2026-05-21 - Control Plane Confidence"
    assert [block.kind for block in release_info.news.blocks] == ["paragraph", "paragraph"]
    assert [block.text for block in release_info.news.blocks] == [
        "Operators now get release context and deployment hints in one place.",
        "The dashboard news feed explains what changed and how to use it.",
    ]
    assert release_info.news.has_previous is False
    assert release_info.news.has_next is True
    assert release_info.news.previous_index is None
    assert release_info.news.next_index == 1
    assert older_release_info.news is not None
    assert older_release_info.news.index == 1
    assert older_release_info.news.heading == "2026-05-20 - Earlier Update"
    assert [block.kind for block in older_release_info.news.blocks] == ["paragraph"]
    assert [block.text for block in older_release_info.news.blocks] == [
        "Older posts remain available without loading the whole news archive.",
    ]
    assert older_release_info.news.has_previous is True
    assert older_release_info.news.has_next is False
    assert older_release_info.news.previous_index == 0
    assert older_release_info.news.next_index is None


def test_admin_web_release_info_reports_missing_sources(monkeypatch, tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    monkeypatch.delenv("EVANDER_REPO_VERSION", raising=False)
    monkeypatch.delenv("evander_repo_version", raising=False)
    monkeypatch.delenv("EVANDER_BUILD_VERSION", raising=False)
    monkeypatch.delenv("evander_build_version", raising=False)
    monkeypatch.setattr(
        release_metadata_module.importlib_metadata,
        "version",
        lambda name: (_ for _ in ()).throw(
            release_metadata_module.importlib_metadata.PackageNotFoundError(name)
        ),
    )

    release_info = service.operations_admin.read_release_info()

    assert release_info.version is None
    assert release_info.version_source is None
    assert release_info.build is None
    assert release_info.build_source is None
    assert release_info.changelog is None
    assert release_info.news is None
    assert any("Version not found" in reason for reason in release_info.missing_reasons)
    assert any("Build identity not found" in reason for reason in release_info.missing_reasons)
    assert any("CHANGELOG.md is missing" in reason for reason in release_info.missing_reasons)
    assert any("NEWS.md is missing" in reason for reason in release_info.missing_reasons)


def test_admin_web_skills_api_and_catalog_options(tmp_path: Path) -> None:
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))

    locked_create = client.post(
        "/api/admin/skills",
        json={"name": "locked.skill", "content": "# Locked\n", "replace": False},
    )
    unlock_response = client.post("/api/admin/privileged/unlock")
    create_response = client.post(
        "/api/admin/skills",
        json={"name": "demo.skill", "content": "# Demo\n", "replace": False},
    )

    assert locked_create.status_code == 403
    assert locked_create.json()["error"] == "Privileged operations are locked."
    assert unlock_response.status_code == 200
    assert create_response.status_code == 201
    assert create_response.json()["skill"]["name"] == "demo.skill"
    duplicate_response = client.post(
        "/api/admin/skills",
        json={"name": "demo.skill", "content": "# Duplicate\n"},
    )
    assert duplicate_response.status_code == 409

    list_response = client.get("/api/admin/skills")
    assert list_response.status_code == 200
    assert [item["name"] for item in list_response.json()["items"]] == ["demo.skill"]
    detail_response = client.get("/api/admin/skills/demo.skill")
    assert detail_response.status_code == 200
    assert detail_response.json()["skill"]["content"] == "# Demo\n"

    replace_response = client.post(
        "/api/admin/skills",
        json={"name": "demo.skill", "content": "# Updated\n", "replace": True},
    )
    assert replace_response.status_code == 201
    assert client.get("/api/admin/skills/demo.skill").json()["skill"]["content"] == "# Updated\n"

    catalog_payload = client.get("/api/workflow-editor/catalog").json()
    vm_create = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:vm:create"
    )
    agent_create = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:agent:create"
    )
    for node in (vm_create, agent_create):
        codex_skills = next(field for field in node["fields"] if field["key"] == "codex_skills")
        assert codex_skills["kind"] == "string_list"
        assert codex_skills["options"] == [{"value": "demo.skill", "label": "demo.skill"}]

    delete_response = client.delete("/api/admin/skills/demo.skill")
    assert delete_response.status_code == 200
    assert client.get("/api/admin/skills/demo.skill").status_code == 404


def test_admin_web_delete_skill_blocks_inline_workflow_references(
    tmp_path: Path,
) -> None:
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))
    _unlock_privileged_operations(client)
    assert (
        client.post(
            "/api/admin/skills",
            json={"name": "demo.skill", "content": "# Demo\n"},
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/api/admin/skills",
            json={"name": "keep.skill", "content": "# Keep\n"},
        ).status_code
        == 201
    )
    payload = {
        "config": {
            "runtime_dir": str(tmp_path / "runtime"),
            "effects": [
                {
                    "id": "create-agent",
                    "display_name": "Create agent",
                    "type": "effect:agent:create",
                    "execution_backend": "container_worker_vm",
                    "session_id": "agent-session",
                    "codex_skills": ["demo.skill", "keep.skill"],
                },
                {
                    "id": "create-vm",
                    "display_name": "Create VM",
                    "type": "effect:vm:create",
                    "vm_name": "vm-skill-test",
                    "codex_skills": ["demo.skill"],
                },
            ],
            "edges": [],
        },
        "layout": {"nodes": {}},
        "expected_revision": _workflow_expected_revision(client),
    }
    save_response = client.put("/api/workflow-editor/document", json=payload)
    assert save_response.status_code == 200
    config_path = tmp_path / "config" / "trigger-orchestrator" / "config.json"
    saved_config_before_delete = config_path.read_text(encoding="utf-8")

    delete_response = client.delete("/api/admin/skills/demo.skill")

    assert delete_response.status_code == 409
    assert (
        delete_response.json()["error"]
        == "Cannot delete Codex skill demo.skill: it is referenced by workflows."
    )
    assert config_path.read_text(encoding="utf-8") == saved_config_before_delete
    assert client.get("/api/admin/skills/demo.skill").status_code == 200
    details = delete_response.json()["details"]
    assert details["skill_name"] == "demo.skill"
    references_by_node = {reference["node_id"]: reference for reference in details["references"]}
    assert set(references_by_node) == {"create-agent", "create-vm"}
    assert references_by_node["create-agent"] == {
        "scope": "inline",
        "path": str(config_path),
        "node_id": "create-agent",
        "node_type": "effect:agent:create",
        "display_name": "Create agent",
        "field_path": "config.codex_skills",
        "reference_count": 1,
    }
    assert references_by_node["create-vm"]["field_path"] == "config.codex_skills"
    assert references_by_node["create-vm"]["scope"] == "inline"
    saved_config = json.loads(saved_config_before_delete)
    effects_by_id = {effect["id"]: effect for effect in saved_config["effects"]}
    assert effects_by_id["create-agent"]["config"]["codex_skills"] == [
        "demo.skill",
        "keep.skill",
    ]
    assert effects_by_id["create-vm"]["config"]["codex_skills"] == ["demo.skill"]
    catalog_payload = client.get("/api/workflow-editor/catalog").json()
    agent_create = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:agent:create"
    )
    codex_skills = next(
        field for field in agent_create["fields"] if field["key"] == "codex_skills"
    )
    assert codex_skills["options"] == [
        {"value": "demo.skill", "label": "demo.skill"},
        {"value": "keep.skill", "label": "keep.skill"},
    ]


def test_admin_web_delete_skill_blocks_workflow_pack_references(
    tmp_path: Path,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    packs_dir = tmp_path / "config" / "workflow-packs"
    packs_dir.mkdir(parents=True, exist_ok=True)
    pack_path = packs_dir / "pack-a.v1.json"
    pack_path.write_text(
        json.dumps(
            {
                "pack_id": "pack-a",
                "pack_version": "v1",
                "graphs": [],
                "ir": {
                    "version": "v1",
                    "nodes": [
                        {
                            "node": {
                                "id": "create-agent",
                                "display_name": "Create agent",
                                "type": "effect:agent:create",
                                "config": {
                                    "execution_backend": "container_worker_vm",
                                    "session_id": "agent-session",
                                    "codex_skills": ["demo.skill", "keep.skill"],
                                },
                            }
                        },
                        {
                            "id": "create-vm",
                            "display_name": "Create VM",
                            "type": "effect:vm:create",
                            "vm_name": "vm-skill-test",
                            "codex_skills": "demo.skill",
                        },
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
                "workflow_packs": ["pack-a.v1.json"],
            }
        ),
        encoding="utf-8",
    )
    client = TestClient(create_app(service))
    _unlock_privileged_operations(client)
    assert (
        client.post(
            "/api/admin/skills",
            json={"name": "demo.skill", "content": "# Demo\n"},
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/api/admin/skills",
            json={"name": "keep.skill", "content": "# Keep\n"},
        ).status_code
        == 201
    )
    pack_payload_before_delete = pack_path.read_text(encoding="utf-8")

    delete_response = client.delete("/api/admin/skills/demo.skill")

    assert delete_response.status_code == 409
    assert (
        delete_response.json()["error"]
        == "Cannot delete Codex skill demo.skill: it is referenced by workflows."
    )
    assert pack_path.read_text(encoding="utf-8") == pack_payload_before_delete
    assert client.get("/api/admin/skills/demo.skill").status_code == 200
    details = delete_response.json()["details"]
    assert details["skill_name"] == "demo.skill"
    references_by_node = {reference["node_id"]: reference for reference in details["references"]}
    assert references_by_node["create-agent"] == {
        "scope": "pack",
        "path": str(pack_path),
        "node_id": "create-agent",
        "node_type": "effect:agent:create",
        "display_name": "Create agent",
        "field_path": "config.codex_skills",
        "reference_count": 1,
        "workflow_pack": "pack-a.v1.json",
    }
    assert references_by_node["create-vm"] == {
        "scope": "pack",
        "path": str(pack_path),
        "node_id": "create-vm",
        "node_type": "effect:vm:create",
        "display_name": "Create VM",
        "field_path": "codex_skills",
        "reference_count": 1,
        "workflow_pack": "pack-a.v1.json",
    }
    pack_payload = json.loads(pack_path.read_text(encoding="utf-8"))
    nodes = pack_payload["ir"]["nodes"]
    assert nodes[0]["node"]["config"]["codex_skills"] == ["demo.skill", "keep.skill"]
    assert nodes[1]["codex_skills"] == "demo.skill"


def test_admin_web_skills_api_rejects_json_errors(tmp_path: Path) -> None:
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))
    assert client.post("/api/admin/privileged/unlock").status_code == 200

    assert client.post("/api/admin/skills", json=[]).status_code == 400
    assert (
        client.post(
            "/api/admin/skills",
            json={"name": "../escape", "content": "# Demo\n"},
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/admin/skills",
            json={"name": "empty", "content": "  \n"},
        ).status_code
        == 400
    )


def test_admin_web_skills_mutations_require_owner_and_privileged_unlock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AUTH_MODE", "local_password")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_USERNAME", "admin")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_PASSWORD", "admin-pass")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_SESSION_SECRET", "test-secret")
    app = create_app(build_admin_runtime(write_config(tmp_path)))
    app.state.admin_user_store.upsert_user(
        login="operator@example.com",
        role="regular",
        display_name="Operator",
    )
    app.state.admin_user_store.set_local_password(
        login="operator@example.com",
        password="operator-pass",
    )
    admin_client = TestClient(app)
    operator_client = TestClient(app)
    assert (
        admin_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "admin-pass"},
        ).status_code
        == 200
    )
    assert (
        operator_client.post(
            "/api/auth/login",
            json={"username": "operator@example.com", "password": "operator-pass"},
        ).status_code
        == 200
    )

    locked_owner = admin_client.post(
        "/api/admin/skills",
        json={"name": "owner.skill", "content": "# Owner\n"},
    )
    assert operator_client.post("/api/admin/privileged/unlock").status_code == 200
    non_owner = operator_client.post(
        "/api/admin/skills",
        json={"name": "operator.skill", "content": "# Operator\n"},
    )
    assert admin_client.post("/api/admin/privileged/unlock").status_code == 200
    owner_create = admin_client.post(
        "/api/admin/skills",
        json={"name": "owner.skill", "content": "# Owner\n"},
    )
    owner_delete = admin_client.delete("/api/admin/skills/owner.skill")

    assert locked_owner.status_code == 403
    assert locked_owner.json()["error"] == "Privileged operations are locked."
    assert non_owner.status_code == 403
    assert non_owner.json()["error"] == "Privileged operations are locked."
    assert owner_create.status_code == 201
    assert owner_delete.status_code == 200


def test_admin_web_exposes_workflow_run_stream(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))

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


def test_admin_web_exposes_recent_workflow_node_activity_from_json_stores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    now = datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(workflow_reports_service, "_utc_now", lambda: now)

    service = build_admin_runtime(write_config(tmp_path))
    workflow_admin = service.workflow_reports_admin
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

    trigger_queue = TriggerEventQueue(stores["event_queue_store_path"])
    trigger_queue.publish(
        TriggerEvent(
            event_id="evt-trigger-recent",
            trigger_id="trigger-recent",
            trigger_type="trigger:test",
            occurred_at=now - timedelta(seconds=20),
            payload={"kind": "json"},
            trace_id="trace-trigger-recent",
        )
    )
    trigger_queue.mark_delivered("evt-trigger-recent", delivered_at=now - timedelta(seconds=1))
    trigger_queue.publish(
        TriggerEvent(
            event_id="evt-trigger-old",
            trigger_id="trigger-old",
            trigger_type="trigger:test",
            occurred_at=now - timedelta(seconds=30),
            payload={"kind": "json"},
            trace_id="trace-trigger-old",
        )
    )
    trigger_queue.mark_delivered("evt-trigger-old", delivered_at=now - timedelta(seconds=20))

    effect_store = JsonExecutionStore(stores["effect_execution_store_path"])
    gate_store = JsonGateExecutionStore(stores["gate_execution_store_path"])

    gate_store.record_execution(
        _gate_execution_record(
            execution_id="exec-gate-recent",
            idempotency_key="evt-gate-recent::gate-recent",
            event_id="evt-gate-recent",
            gate_id="gate-recent",
            status="succeeded",
            started_at=now - timedelta(seconds=5),
            finished_at=now - timedelta(seconds=2),
        )
    )
    effect_store.record_execution(
        _effect_execution_record(
            execution_id="exec-effect-old",
            idempotency_key="evt-effect-old::effect-old",
            event_id="evt-effect-old",
            effect_id="effect-old",
            status="succeeded",
            started_at=now - timedelta(seconds=40),
            finished_at=now - timedelta(seconds=35),
        )
    )
    gate_store.record_execution(
        _gate_execution_record(
            execution_id="exec-gate-old",
            idempotency_key="evt-gate-old::gate-old",
            event_id="evt-gate-old",
            gate_id="gate-old",
            status="succeeded",
            started_at=now - timedelta(seconds=40),
            finished_at=now - timedelta(seconds=35),
        )
    )

    for index in range(201):
        effect_store.record_execution(
            _effect_execution_record(
                execution_id=f"exec-effect-{index}",
                idempotency_key=f"evt-effect-{index}::effect-{index}",
                event_id=f"evt-effect-{index}",
                effect_id=f"effect-{index}",
                status="succeeded",
                started_at=now - timedelta(seconds=4, microseconds=index),
                finished_at=now - timedelta(seconds=3, microseconds=index),
            )
        )

    client = TestClient(create_app(service))
    response = client.get("/api/workflow-editor/runs")

    assert response.status_code == 200
    payload = response.json()
    assert payload["runs"] == []
    assert payload["node_activity"] == {}
    assert len(payload["recent_node_activity"]) == 200
    assert [item["node_id"] for item in payload["recent_node_activity"][:3]] == [
        "trigger-recent",
        "gate-recent",
        "effect-0",
    ]
    assert payload["recent_node_activity"][0]["source"] == "trigger_event"
    assert payload["recent_node_activity"][0]["status"] == "delivered"
    assert payload["recent_node_activity"][0]["activity_at"] == "2026-05-21T11:59:59Z"
    assert payload["recent_node_activity"][-1]["node_id"] == "effect-197"
    assert payload["recent_node_activity"][-1]["source"] == "effect_execution"


def test_admin_web_exposes_recent_workflow_node_activity_from_sqlite_stores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    _write_trigger_orchestrator_sqlite_config(tmp_path)
    now = datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(workflow_reports_service, "_utc_now", lambda: now)

    service = build_admin_runtime(write_config(tmp_path))
    workflow_admin = service.workflow_reports_admin
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

    trigger_queue = SqliteTriggerEventQueue(stores["orchestration_store_path"])
    trigger_queue.publish(
        TriggerEvent(
            event_id="evt-trigger-sqlite",
            trigger_id="trigger-sqlite",
            trigger_type="trigger:test",
            occurred_at=now - timedelta(seconds=20),
            payload={"kind": "sqlite"},
            trace_id="trace-trigger-sqlite",
        )
    )
    trigger_queue.mark_delivered("evt-trigger-sqlite", delivered_at=now - timedelta(seconds=3))

    effect_store = SqliteExecutionStore(stores["orchestration_store_path"])
    gate_store = SqliteGateExecutionStore(stores["orchestration_store_path"])
    effect_store.record_execution(
        _effect_execution_record(
            execution_id="exec-effect-sqlite",
            idempotency_key="evt-effect-sqlite::effect-sqlite",
            event_id="evt-effect-sqlite",
            effect_id="effect-sqlite",
            status="succeeded",
            started_at=now - timedelta(seconds=4),
            finished_at=now - timedelta(seconds=2),
        )
    )
    effect_store.record_execution(
        _effect_execution_record(
            execution_id="exec-effect-sqlite-old",
            idempotency_key="evt-effect-sqlite-old::effect-old",
            event_id="evt-effect-sqlite-old",
            effect_id="effect-sqlite-old",
            status="succeeded",
            started_at=now - timedelta(seconds=30),
            finished_at=now - timedelta(seconds=25),
        )
    )
    gate_store.record_execution(
        _gate_execution_record(
            execution_id="exec-gate-sqlite",
            idempotency_key="evt-gate-sqlite::gate-sqlite",
            event_id="evt-gate-sqlite",
            gate_id="gate-sqlite",
            status="succeeded",
            started_at=now - timedelta(seconds=2),
            finished_at=now - timedelta(seconds=1),
        )
    )

    client = TestClient(create_app(service))
    response = client.get("/api/workflow-editor/runs")

    assert response.status_code == 200
    payload = response.json()
    assert payload["runs"] == []
    assert payload["node_activity"] == {}
    assert [item["node_id"] for item in payload["recent_node_activity"]] == [
        "gate-sqlite",
        "effect-sqlite",
        "trigger-sqlite",
    ]
    assert payload["recent_node_activity"][2]["source"] == "trigger_event"
    assert payload["recent_node_activity"][2]["activity_at"] == "2026-05-21T11:59:57Z"
    assert payload["recent_node_activity"][2]["status"] == "delivered"


def test_admin_web_workflow_run_stream_includes_recent_node_activity_in_state_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    now = datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)
    snapshots = iter(
        [
            WorkflowRunSnapshotPayload(
                generated_at=now,
                runs=[],
                node_activity={},
                recent_node_activity=[
                    WorkflowRunRecentNodeActivityPayload(
                        activity_key="trigger_event:evt-1:trigger-a:delivered:2026-05-21T11:59:59Z",
                        event_id="evt-1",
                        node_id="trigger-a",
                        status="delivered",
                        source="trigger_event",
                        activity_at=now - timedelta(seconds=1),
                    )
                ],
            ),
            WorkflowRunSnapshotPayload(
                generated_at=now,
                runs=[],
                node_activity={},
                recent_node_activity=[
                    WorkflowRunRecentNodeActivityPayload(
                        activity_key="trigger_event:evt-2:trigger-a:delivered:2026-05-21T11:59:58Z",
                        event_id="evt-2",
                        node_id="trigger-a",
                        status="delivered",
                        source="trigger_event",
                        activity_at=now - timedelta(seconds=2),
                    )
                ],
            ),
        ]
    )

    class FakeJournal:
        def cursor(self) -> object:
            return object()

        async def wait_after(
            self, cursor: object, timeout_seconds: float
        ) -> tuple[list[str], object]:
            return (["workflow.run.snapshot"], object())

    class RequestStub:
        async def is_disconnected(self) -> bool:
            return False

    monkeypatch.setattr(
        service.workflow_config_admin, "workflow_event_journal", lambda: FakeJournal()
    )
    monkeypatch.setattr(
        service.workflow_config_admin, "read_workflow_run_snapshot", lambda: next(snapshots)
    )

    async def read_two_events() -> tuple[str, str]:
        stream = _workflow_run_events(service, RequestStub())  # type: ignore[arg-type]
        try:
            first = await asyncio.wait_for(anext(stream), timeout=1.0)
            second = await asyncio.wait_for(anext(stream), timeout=1.0)
            return first, second
        finally:
            await stream.aclose()

    first_event, second_event = asyncio.run(read_two_events())

    assert first_event.startswith("event: snapshot\n")
    assert second_event.startswith("event: snapshot\n")
    assert '"event_id": "evt-1"' in first_event
    assert '"event_id": "evt-2"' in second_event


def test_admin_web_validates_and_saves_workflow_editor_document(tmp_path: Path) -> None:
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))
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
        "graphs": [
            {
                "id": "graph-cron-15m",
                "name": "Scheduled prompt",
                "description": "Prompts an agent on the cron schedule.",
                "directory_paths": ["root"],
                "user_ids": ["admin"],
                "node_ids": ["cron-15m", "prompt-agent"],
            }
        ],
    }

    validate_response = client.post("/api/workflow-editor/validate", json=payload)
    _unlock_privileged_operations(client)
    payload["expected_revision"] = _workflow_expected_revision(client)
    save_response = client.put("/api/workflow-editor/document", json=payload)

    assert validate_response.status_code == 200
    assert save_response.status_code == 200
    assert save_response.json()["layout"]["nodes"]["prompt-agent"] == {"x": 300.0, "y": 50.0}
    assert save_response.json()["graphs"][0]["name"] == "Scheduled prompt"
    assert save_response.json()["graphs"][0]["directory_paths"] == ["root"]
    assert save_response.json()["graphs"][0]["user_ids"] == ["admin"]
    assert isinstance(save_response.json().get("revision"), str)
    assert isinstance(save_response.json().get("saved_at"), str)
    assert (tmp_path / "config" / "trigger-orchestrator" / "layout.json").exists()


def test_admin_web_workflow_editor_accepts_revision_returned_from_previous_save(
    tmp_path: Path,
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    service.directory_admin.create_child(parent="root", name="ops")
    client = TestClient(create_app(service))
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
        "graphs": [
            {
                "id": "graph-cron-15m",
                "name": "Scheduled prompt",
                "description": "Prompts an agent on the cron schedule.",
                "directory_paths": ["root"],
                "user_ids": ["admin"],
                "node_ids": ["cron-15m", "prompt-agent"],
            }
        ],
    }

    _unlock_privileged_operations(client)
    payload["expected_revision"] = _workflow_expected_revision(client)
    first_save = client.put("/api/workflow-editor/document", json=payload)
    assert first_save.status_code == 200
    first_revision = first_save.json()["revision"]
    assert isinstance(first_revision, str)

    payload["config"]["effects"][0]["display_name"] = "Prompt agent v2"
    payload["layout"]["nodes"]["prompt-agent"]["x"] = 320
    payload["expected_revision"] = first_revision
    second_save = client.put("/api/workflow-editor/document", json=payload)

    assert second_save.status_code == 200
    assert second_save.json()["revision"] == _workflow_expected_revision(client)


def test_admin_web_saves_disconnected_workflow_editor_document(tmp_path: Path) -> None:
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))
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

    _unlock_privileged_operations(client)
    payload["expected_revision"] = _workflow_expected_revision(client)
    save_response = client.put("/api/workflow-editor/document", json=payload)

    assert save_response.status_code == 200
    assert save_response.json()["config"]["edges"] == []


def test_admin_web_workflow_editor_save_requires_matching_revision(tmp_path: Path) -> None:
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))
    _unlock_privileged_operations(client)
    payload = {
        "config": {
            "runtime_dir": str(tmp_path / "runtime"),
            "triggers": [],
            "gates": [],
            "effects": [],
            "queries": [],
            "edges": [],
        },
        "layout": {"nodes": {}},
    }

    missing = client.put("/api/workflow-editor/document", json=payload)
    stale = client.put(
        "/api/workflow-editor/document",
        json={**payload, "expected_revision": "stale"},
    )
    current_revision = _workflow_expected_revision(client)
    saved = client.put(
        "/api/workflow-editor/document",
        json={**payload, "expected_revision": current_revision},
    )

    assert missing.status_code == 428
    assert stale.status_code == 409
    assert stale.json()["details"]["current_revision"] == current_revision
    assert saved.status_code == 200
    assert saved.json()["revision"] == _workflow_expected_revision(client)


def test_admin_web_workflow_editor_document_filters_unreadable_graphs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.directory_admin.create_child(parent="root", name="ops")
    service.workflow_config_admin.save_workflow_editor_document(
        config={
            "runtime_dir": str(tmp_path / "runtime"),
            "triggers": [
                {
                    "id": "root-start",
                    "display_name": "Root start",
                    "type": "trigger:cron",
                    "expression": "0 * * * *",
                },
                {
                    "id": "ops-start",
                    "display_name": "Ops start",
                    "type": "trigger:cron",
                    "expression": "15 * * * *",
                },
            ],
            "gates": [
                {
                    "id": "root-filter",
                    "display_name": "Root filter",
                    "type": "gate:filter",
                    "when": {"all": [{"left": "root", "equals": "root"}]},
                },
                {
                    "id": "ops-filter",
                    "display_name": "Ops filter",
                    "type": "gate:filter",
                    "when": {"all": [{"left": "ops", "equals": "ops"}]},
                },
            ],
            "effects": [],
            "queries": [],
            "edges": [
                {"source_id": "root-start", "target_id": "root-filter"},
                {"source_id": "ops-start", "target_id": "ops-filter"},
            ],
        },
        layout={
            "nodes": {
                "root-start": {"x": 100, "y": 50},
                "root-filter": {"x": 320, "y": 50},
                "ops-start": {"x": 100, "y": 250},
                "ops-filter": {"x": 320, "y": 250},
            }
        },
        graphs=[
            {
                "id": "graph-root",
                "name": "Root workflow",
                "directory_paths": ["root"],
                "user_ids": ["owner@example.com"],
                "node_ids": ["root-start", "root-filter"],
            },
            {
                "id": "graph-ops",
                "name": "Ops workflow",
                "directory_paths": ["root/ops"],
                "user_ids": ["viewer@example.com"],
                "node_ids": ["ops-start", "ops-filter"],
            },
        ],
    )
    store = _admin_web_user_store(tmp_path)
    store.upsert_user(login="owner@example.com", role="admin", display_name="Owner")
    store.upsert_user(login="viewer@example.com", role="regular", display_name="Viewer")
    store.remove_user_group_assignment(
        login="viewer@example.com", group_id="operator", directory_path="root"
    )
    store.assign_user_group(
        login="viewer@example.com", group_id="workflow-viewer", directory_path="root/ops"
    )
    _set_azure_sso_env(monkeypatch, allowed_users=None)
    app = create_app(service)
    client = TestClient(app)

    callback = _complete_azure_login(app, client, login="viewer@example.com", display_name="Viewer")
    response = client.get("/api/workflow-editor/document")
    payload = response.json()
    full_revision = service.workflow_config_admin.read_workflow_editor_document().revision

    assert callback.status_code == 303
    assert response.status_code == 200
    assert payload["revision"] == full_revision
    assert [graph["id"] for graph in payload["graphs"]] == ["graph-ops"]
    assert [node["id"] for node in payload["config"]["triggers"]] == ["ops-start"]
    assert [node["id"] for node in payload["config"]["gates"]] == ["ops-filter"]
    assert [edge["source_id"] for edge in payload["config"]["edges"]] == ["ops-start"]
    assert set(payload["layout"]["nodes"]) == {"ops-start", "ops-filter"}
    assert set(payload["output_schemas"]) == {"ops-start", "ops-filter"}
    assert payload["config"]["workflow_graphs"] == [
        {
            "id": "graph-ops",
            "name": "Ops workflow",
            "directory_path": "root/ops",
            "directory_paths": ["root/ops"],
            "user_ids": ["viewer@example.com"],
            "node_ids": ["ops-start", "ops-filter"],
        }
    ]


def test_admin_web_workflow_editor_routes_enforce_directory_permissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    service.directory_admin.create_child(parent="root", name="ops")
    service.operations_admin.write_directory_domain_config(
        "root",
        COMMUNICATION_TARGETS_DOMAIN,
        {
            "targets": [
                {
                    "id": "root-chat",
                    "provider": "teams",
                    "kind": "teams",
                    "address": "team/root",
                    "policy": {"capabilities": ["read"]},
                }
            ]
        },
    )
    service.operations_admin.write_directory_domain_config(
        "root/ops",
        COMMUNICATION_TARGETS_DOMAIN,
        {
            "targets": [
                {
                    "id": "ops-chat",
                    "provider": "teams",
                    "kind": "teams",
                    "address": "team/ops",
                    "policy": {"capabilities": ["read"]},
                }
            ]
        },
    )
    service.workflow_config_admin.save_workflow_editor_document(
        config={
            "runtime_dir": str(tmp_path / "runtime"),
            "triggers": [
                {
                    "id": "root-start",
                    "display_name": "Root start",
                    "type": "trigger:cron",
                    "expression": "0 * * * *",
                },
                {
                    "id": "ops-start",
                    "display_name": "Ops start",
                    "type": "trigger:cron",
                    "expression": "15 * * * *",
                },
            ],
            "gates": [
                {
                    "id": "root-filter",
                    "display_name": "Root filter",
                    "type": "gate:filter",
                    "when": {"all": [{"left": "root", "equals": "root"}]},
                },
                {
                    "id": "ops-filter",
                    "display_name": "Ops filter",
                    "type": "gate:filter",
                    "when": {"all": [{"left": "ops", "equals": "ops"}]},
                },
            ],
            "effects": [],
            "queries": [],
            "edges": [
                {"source_id": "root-start", "target_id": "root-filter"},
                {"source_id": "ops-start", "target_id": "ops-filter"},
            ],
        },
        layout={
            "nodes": {
                "root-start": {"x": 100, "y": 50},
                "root-filter": {"x": 320, "y": 50},
                "ops-start": {"x": 100, "y": 250},
                "ops-filter": {"x": 320, "y": 250},
            }
        },
        graphs=[
            {
                "id": "graph-root",
                "name": "Root workflow",
                "directory_paths": ["root"],
                "user_ids": ["owner@example.com"],
                "node_ids": ["root-start", "root-filter"],
            },
            {
                "id": "graph-ops",
                "name": "Ops workflow",
                "directory_paths": ["root/ops"],
                "user_ids": ["viewer@example.com"],
                "node_ids": ["ops-start", "ops-filter"],
            },
        ],
    )
    store = _admin_web_user_store(tmp_path)
    store.upsert_user(login="owner@example.com", role="admin", display_name="Owner")
    store.upsert_user(login="viewer@example.com", role="regular", display_name="Viewer")
    store.remove_user_group_assignment(
        login="viewer@example.com", group_id="operator", directory_path="root"
    )
    store.assign_user_group(
        login="viewer@example.com", group_id="workflow-viewer", directory_path="root/ops"
    )
    _set_azure_sso_env(monkeypatch, allowed_users=None)
    app = create_app(service)
    client = TestClient(app)
    _complete_azure_login(app, client, login="viewer@example.com", display_name="Viewer")

    document_response = client.get("/api/workflow-editor/document")
    validate_response = client.post(
        "/api/workflow-editor/validate",
        json={
            "config": {
                "runtime_dir": str(tmp_path / "runtime"),
                "triggers": [
                    {
                        "id": "ops-start",
                        "display_name": "Ops start",
                        "type": "trigger:cron",
                        "expression": "15 * * * *",
                    }
                ],
                "gates": [
                    {
                        "id": "ops-filter",
                        "display_name": "Ops filter",
                        "type": "gate:filter",
                        "when": {"all": [{"left": "ops", "equals": "ops"}]},
                    }
                ],
                "edges": [{"source_id": "ops-start", "target_id": "ops-filter"}],
            },
            "layout": {
                "nodes": {
                    "ops-start": {"x": 100, "y": 250},
                    "ops-filter": {"x": 320, "y": 250},
                }
            },
            "graphs": [
                {
                    "id": "graph-ops",
                    "name": "Ops workflow",
                    "directory_paths": ["root/ops"],
                    "user_ids": ["viewer@example.com"],
                    "node_ids": ["ops-start", "ops-filter"],
                }
            ],
        },
    )
    catalog_allowed = client.get("/api/workflow-editor/catalog?directory_path=root/ops")
    catalog_denied = client.get("/api/workflow-editor/catalog?directory_path=root")
    generate_denied = client.post(
        "/api/workflow-editor/generate-draft",
        json={"description": "Draft ops workflow", "directory_path": "root/ops"},
    )
    save_denied = client.put(
        "/api/workflow-editor/document",
        json={
            "config": {
                "runtime_dir": str(tmp_path / "runtime"),
                "triggers": [
                    {
                        "id": "ops-start",
                        "display_name": "Ops start",
                        "type": "trigger:cron",
                        "expression": "15 * * * *",
                    }
                ],
                "gates": [
                    {
                        "id": "ops-filter",
                        "display_name": "Ops filter",
                        "type": "gate:filter",
                        "when": {"all": [{"left": "ops", "equals": "ops"}]},
                    }
                ],
                "edges": [{"source_id": "ops-start", "target_id": "ops-filter"}],
            },
            "layout": {
                "nodes": {
                    "ops-start": {"x": 100, "y": 250},
                    "ops-filter": {"x": 320, "y": 250},
                }
            },
            "graphs": [
                {
                    "id": "graph-ops",
                    "name": "Ops workflow",
                    "directory_paths": ["root/ops"],
                    "user_ids": ["viewer@example.com"],
                    "node_ids": ["ops-start", "ops-filter"],
                }
            ],
            "expected_revision": document_response.json()["revision"],
        },
    )

    assert document_response.status_code == 200
    assert validate_response.status_code == 200
    assert catalog_allowed.status_code == 200
    assert catalog_denied.status_code == 403
    assert generate_denied.status_code == 403
    assert generate_denied.json()["error"] == "Permission required: workflows.write"
    assert save_denied.status_code == 403
    assert save_denied.json()["error"] == "Permission required: workflows.write"


def test_admin_web_workflow_run_routes_enforce_directory_permissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    config_root = tmp_path / "config" / "trigger-orchestrator"
    config_root.mkdir(parents=True, exist_ok=True)
    _write_json_payload(
        config_root / "config.json",
        {
            "runtime_dir": str(runtime_dir),
            "triggers": [
                {
                    "id": "root-trigger",
                    "display_name": "Root trigger",
                    "type": "trigger:cron",
                    "expression": "0 * * * *",
                },
                {
                    "id": "ops-trigger",
                    "display_name": "Ops trigger",
                    "type": "trigger:cron",
                    "expression": "15 * * * *",
                },
            ],
            "effects": [
                {
                    "id": "root-effect",
                    "display_name": "Root effect",
                    "type": "effect:agent:prompt",
                    "session_id": "session-root",
                    "prompt": "root",
                },
                {
                    "id": "ops-effect",
                    "display_name": "Ops effect",
                    "type": "effect:agent:prompt",
                    "session_id": "session-ops",
                    "prompt": "ops",
                },
            ],
            "gates": [],
            "queries": [],
            "edges": [
                {"source_id": "root-trigger", "target_id": "root-effect"},
                {"source_id": "ops-trigger", "target_id": "ops-effect"},
            ],
            "workflow_graphs": [
                {
                    "id": "graph-root",
                    "name": "Root workflow",
                    "directory_path": "root",
                    "user_ids": ["owner@example.com"],
                    "node_ids": ["root-trigger", "root-effect"],
                },
                {
                    "id": "graph-ops",
                    "name": "Ops workflow",
                    "directory_path": "root/ops",
                    "user_ids": ["viewer@example.com"],
                    "node_ids": ["ops-trigger", "ops-effect"],
                },
            ],
        },
    )
    _write_json_payload(
        config_root / "layout.json",
        {
            "nodes": {
                "root-trigger": {"x": 100, "y": 50},
                "root-effect": {"x": 320, "y": 50},
                "ops-trigger": {"x": 100, "y": 250},
                "ops-effect": {"x": 320, "y": 250},
            }
        },
    )
    _write_json_payload(
        runtime_dir / "trigger-orchestrator-events.json",
        {
            "evt-root": {
                "event_id": "evt-root",
                "trigger_id": "root-trigger",
                "trigger_type": "trigger:cron",
                "trace_id": "trace-root",
                "occurred_at": "2026-05-24T10:00:00Z",
                "payload": {"scope": "root"},
                "delivery_state": "inflight",
                "inflight_at": "2026-05-24T10:00:05Z",
                "delivered_at": None,
                "idempotency_key": None,
            },
            "evt-ops": {
                "event_id": "evt-ops",
                "trigger_id": "ops-trigger",
                "trigger_type": "trigger:cron",
                "trace_id": "trace-ops",
                "occurred_at": "2026-05-24T11:00:00Z",
                "payload": {"scope": "ops"},
                "delivery_state": "delivered",
                "inflight_at": "2026-05-24T11:00:05Z",
                "delivered_at": "2026-05-24T11:00:06Z",
                "idempotency_key": None,
            },
        },
    )
    _write_json_payload(
        runtime_dir / "effect-orchestrator-executions.json",
        {
            "exec-root": {
                "execution_id": "exec-root",
                "idempotency_key": "evt-root::root-effect",
                "event_id": "evt-root",
                "trace_id": "trace-root",
                "source_node_id": "root-trigger",
                "source_trigger_type": "trigger:cron",
                "effect_id": "root-effect",
                "effect_type": "effect:agent:prompt",
                "status": "running",
                "started_at": "2026-05-24T10:01:00Z",
                "finished_at": "2026-05-24T10:01:01Z",
                "resolved_config_hash": "hash-root",
                "resolved_config": {},
                "input_payload": {},
                "outputs": {},
            },
            "exec-ops": {
                "execution_id": "exec-ops",
                "idempotency_key": "evt-ops::ops-effect",
                "event_id": "evt-ops",
                "trace_id": "trace-ops",
                "source_node_id": "ops-trigger",
                "source_trigger_type": "trigger:cron",
                "effect_id": "ops-effect",
                "effect_type": "effect:agent:prompt",
                "status": "failed",
                "started_at": "2026-05-24T11:01:00Z",
                "finished_at": "2026-05-24T11:01:01Z",
                "resolved_config_hash": "hash-ops",
                "resolved_config": {},
                "input_payload": {"issue_number": "77"},
                "outputs": {},
                "error": "ops failed",
            },
        },
    )
    _write_json_payload(runtime_dir / "await-agent-pending.json", {})

    _set_azure_sso_env(monkeypatch, allowed_users="operator@example.com,viewer@example.com")
    app = create_app(service)
    store = app.state.admin_user_store
    store.update_user(login="viewer@example.com", role="regular", display_name="Viewer")
    store.remove_user_group_assignment(
        login="viewer@example.com", group_id="operator", directory_path="root"
    )
    store.assign_user_group(
        login="viewer@example.com", group_id="workflow-viewer", directory_path="root/ops"
    )
    client = TestClient(app)
    _complete_azure_login(app, client, login="viewer@example.com", display_name="Viewer")

    definition_allowed = client.get("/api/workflow-editor/document")
    runs_without_run_read = client.get("/api/workflow-editor/runs")
    detail_without_run_read = client.get(
        "/api/workflow-editor/run-detail", params={"event_id": "evt-ops"}
    )
    store.assign_user_group(
        login="viewer@example.com", group_id="auditor", directory_path="root/ops"
    )
    runs_response = client.get("/api/workflow-editor/runs")
    graph_allowed = client.get(
        "/api/workflow-editor/run-search", params={"graph_id": "graph-ops", "limit": "5"}
    )
    node_allowed = client.get(
        "/api/workflow-editor/run-search", params={"node_id": "ops-effect", "limit": "5"}
    )
    detail_allowed = client.get("/api/workflow-editor/run-detail", params={"event_id": "evt-ops"})
    graph_denied = client.get(
        "/api/workflow-editor/run-search", params={"graph_id": "graph-root", "limit": "5"}
    )
    detail_denied = client.get("/api/workflow-editor/run-detail", params={"event_id": "evt-root"})

    assert definition_allowed.status_code == 200
    assert [graph["id"] for graph in definition_allowed.json()["graphs"]] == ["graph-ops"]
    assert runs_without_run_read.status_code == 403
    assert runs_without_run_read.json()["error"] == "Permission required: workflow_runs.read"
    assert detail_without_run_read.status_code == 403
    assert detail_without_run_read.json()["error"] == "Permission required: workflow_runs.read"
    assert runs_response.status_code == 200
    runs_payload = runs_response.json()
    assert [run["event_id"] for run in runs_payload["runs"]] == ["evt-ops"]
    assert runs_payload["node_activity"] == {"ops-effect": {"failed": 1}}
    assert graph_allowed.status_code == 200
    assert graph_allowed.json()["filters"]["graph_id"] == "graph-ops"
    assert [run["event_id"] for run in graph_allowed.json()["runs"]] == ["evt-ops"]
    assert node_allowed.status_code == 200
    assert node_allowed.json()["filters"]["node_id"] == "ops-effect"
    assert [run["event_id"] for run in node_allowed.json()["runs"]] == ["evt-ops"]
    assert detail_allowed.status_code == 200
    assert detail_allowed.json()["event_id"] == "evt-ops"
    assert detail_allowed.json()["nodes"][0]["node_id"] in {"ops-effect", "ops-trigger"}
    assert graph_denied.status_code == 403
    assert graph_denied.json()["error"] == "Permission required: workflow_runs.read"
    assert detail_denied.status_code == 403
    assert detail_denied.json()["error"] == "Permission required: workflow_runs.read"


@pytest.mark.parametrize(
    ("query_params", "expected_error"),
    [
        ({"start": "not-a-date"}, "start must be an ISO-8601 datetime."),
        ({"limit": "0"}, "limit must be between 1 and 500."),
        ({"recent_seconds": "0"}, "recent_seconds must be greater than 0."),
    ],
)
def test_admin_web_workflow_failure_clusters_rejects_invalid_query_params(
    tmp_path: Path,
    query_params: dict[str, str],
    expected_error: str,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    client = TestClient(create_app(service))

    response = client.get("/api/admin/reports/workflow-failure-clusters", params=query_params)

    assert response.status_code == 400
    assert response.json()["error"] == expected_error


def test_admin_web_workflow_failure_clusters_returns_empty_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    monkeypatch.setattr(
        service.workflow_config_admin,
        "read_composed_workflow_editor_document",
        lambda: SimpleNamespace(graphs=[]),
    )
    monkeypatch.setattr(
        service.workflow_reports_admin,
        "read_workflow_failure_clusters_report",
        lambda **kwargs: _workflow_failure_cluster_report(
            [],
            generated_at=datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc),
            period_start=datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc),
            period_end=datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc),
            recent_threshold_seconds=86400,
            limit=100,
            has_more=False,
        ),
    )
    client = TestClient(create_app(service))

    response = client.get("/api/admin/reports/workflow-failure-clusters")

    assert response.status_code == 200
    payload = response.json()
    assert payload["clusters"] == []
    assert payload["has_more"] is False
    assert payload["limit"] == 100
    assert payload["recent_threshold_seconds"] == 86400


def test_admin_web_workflow_failure_clusters_requires_workflow_runs_read_for_root_without_graphs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    monkeypatch.setattr(
        service.workflow_config_admin,
        "read_composed_workflow_editor_document",
        lambda: SimpleNamespace(graphs=[]),
    )
    monkeypatch.setattr(
        service.workflow_reports_admin,
        "read_workflow_failure_clusters_report",
        lambda **kwargs: pytest.fail("report generation should not be reached"),
    )

    _set_azure_sso_env(monkeypatch, allowed_users="backup@example.com")
    app = create_app(service)
    store = app.state.admin_user_store
    store.upsert_user(login="viewer@example.com", role="regular", display_name="Viewer")
    store.remove_user_group_assignment(
        login="viewer@example.com", group_id="operator", directory_path="root"
    )
    store.assign_user_group(
        login="viewer@example.com",
        group_id=WORKFLOW_VIEWER_GROUP_ID,
        directory_path="root/ops",
    )
    client = TestClient(app)
    _complete_azure_login(app, client, login="viewer@example.com", display_name="Viewer")

    response = client.get("/api/admin/reports/workflow-failure-clusters")

    assert response.status_code == 403
    assert response.json()["error"] == "Permission required: workflow_runs.read"


def test_admin_web_workflow_failure_clusters_filters_to_readable_graphs_and_exposes_example_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    graphs = [
        {
            "id": "graph-ops",
            "name": "Ops workflow",
            "description": "Ops graph",
            "directory_path": "root/ops",
            "node_ids": ["ops-trigger", "ops-effect"],
        },
        {
            "id": "graph-other",
            "name": "Other workflow",
            "description": "Other graph",
            "directory_path": "root/other",
            "node_ids": ["other-trigger", "other-effect"],
        },
    ]
    monkeypatch.setattr(
        service.workflow_config_admin,
        "read_composed_workflow_editor_document",
        lambda: SimpleNamespace(graphs=graphs),
    )
    captured_kwargs: dict[str, object] = {}

    visible_cluster = WorkflowFailureClusterPayload(
        cluster_id="cluster-visible",
        graph_id="graph-ops",
        graph_name="Ops workflow",
        graph_description="Ops graph",
        directory_path="root/ops",
        node_id="ops-effect",
        node_display_name="Ops effect",
        node_type="effect:agent:prompt",
        effect_type="effect:agent:prompt",
        source_kind="effect",
        error_signature="Branch update rejected for refs/heads/agents/abc123",
        error_signature_key="branch update rejected for refs/heads/agents/<suffix>",
        count=2,
        first_seen_at=datetime(2026, 5, 18, 8, 0, tzinfo=timezone.utc),
        last_seen_at=datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc),
        latest_error_summary="Branch update rejected for refs/heads/agents/abc123",
        state="recent",
        event_ids=["evt-visible-new", "evt-visible-old"],
        trace_ids=["trace-visible"],
        session_ids=["session-visible"],
        pr_ids=["42"],
        ado_item_ids=["100"],
        issue_ids=["200"],
        artifact_ids=["artifact-visible"],
        examples=[
            _workflow_failure_cluster_example(
                event_id="evt-visible-new",
                trace_id="trace-visible-new",
                session_id="session-visible",
                occurred_at=datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc),
                started_at=datetime(2026, 5, 18, 8, 59, tzinfo=timezone.utc),
                finished_at=datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc),
                error_summary="Branch update rejected for refs/heads/agents/abc123",
                run_state="errored",
                artifact_ids=["artifact-visible"],
            )
        ],
    )
    hidden_cluster = WorkflowFailureClusterPayload(
        cluster_id="cluster-hidden",
        graph_id="graph-other",
        graph_name="Other workflow",
        graph_description="Other graph",
        directory_path="root/other",
        node_id="other-effect",
        node_display_name="Other effect",
        node_type="effect:agent:prompt",
        effect_type="effect:agent:prompt",
        source_kind="effect",
        error_signature="Branch update rejected for refs/heads/agents/xyz789",
        error_signature_key="branch update rejected for refs/heads/agents/<suffix>",
        count=1,
        first_seen_at=datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc),
        last_seen_at=datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc),
        latest_error_summary="Branch update rejected for refs/heads/agents/xyz789",
        state="active",
        event_ids=["evt-hidden"],
        trace_ids=["trace-hidden"],
        session_ids=["session-hidden"],
        pr_ids=["43"],
        ado_item_ids=["101"],
        issue_ids=["201"],
        artifact_ids=["artifact-hidden"],
        examples=[
            _workflow_failure_cluster_example(
                event_id="evt-hidden",
                trace_id="trace-hidden",
                session_id="session-hidden",
                occurred_at=datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc),
                started_at=datetime(2026, 5, 18, 9, 59, tzinfo=timezone.utc),
                finished_at=datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc),
                error_summary="Branch update rejected for refs/heads/agents/xyz789",
                run_state="errored",
                artifact_ids=["artifact-hidden"],
            )
        ],
    )
    monkeypatch.setattr(
        service.workflow_reports_admin,
        "read_workflow_failure_clusters_report",
        lambda **kwargs: (
            captured_kwargs.update(kwargs)
            or _workflow_failure_cluster_report(
                [visible_cluster, hidden_cluster],
                generated_at=datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc),
                period_start=datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc),
                period_end=datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc),
                recent_threshold_seconds=7200,
                limit=7,
                has_more=True,
            )
        ),
    )

    _set_azure_sso_env(monkeypatch, allowed_users="backup@example.com")
    app = create_app(service)
    store = app.state.admin_user_store
    store.upsert_user(login="viewer@example.com", role="regular", display_name="Viewer")
    store.remove_user_group_assignment(
        login="viewer@example.com", group_id="operator", directory_path="root"
    )
    store.assign_user_group(
        login="viewer@example.com", group_id=AUDITOR_GROUP_ID, directory_path="root/ops"
    )
    client = TestClient(app)
    _complete_azure_login(app, client, login="viewer@example.com", display_name="Viewer")

    response = client.get(
        "/api/admin/reports/workflow-failure-clusters",
        params={
            "start": "2026-05-17T12:00:00Z",
            "end": "2026-05-18T12:00:00Z",
            "limit": "7",
            "recent_seconds": "7200",
        },
    )

    assert response.status_code == 200
    assert captured_kwargs["period_start"] == datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc)
    assert captured_kwargs["period_end"] == datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc)
    assert captured_kwargs["limit"] == 7
    assert captured_kwargs["recent_threshold_seconds"] == 7200

    payload = response.json()
    assert [cluster["cluster_id"] for cluster in payload["clusters"]] == ["cluster-visible"]
    cluster = payload["clusters"][0]
    assert cluster["graph_id"] == "graph-ops"
    assert cluster["node_id"] == "ops-effect"
    assert cluster["event_ids"] == ["evt-visible-new", "evt-visible-old"]
    assert cluster["examples"][0]["run_detail_href"] == "#workflow?event_id=evt-visible-new"
    assert (
        cluster["examples"][0]["api_run_detail_href"]
        == "/api/workflow-editor/run-detail?event_id=evt-visible-new"
    )


def test_admin_web_workflow_failure_clusters_keeps_unassigned_root_cluster_for_root_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    monkeypatch.setattr(
        service.workflow_config_admin,
        "read_composed_workflow_editor_document",
        lambda: SimpleNamespace(
            graphs=[
                {
                    "id": "graph-ops",
                    "name": "Ops workflow",
                    "description": "Ops graph",
                    "directory_path": "root/ops",
                    "node_ids": ["ops-trigger", "ops-effect"],
                }
            ]
        ),
    )

    unassigned_cluster = WorkflowFailureClusterPayload(
        cluster_id="cluster-unassigned",
        graph_id="root",
        graph_name="Unassigned",
        graph_description=None,
        directory_path="root",
        node_id="root",
        node_display_name="Unassigned",
        node_type=None,
        source_kind="trigger",
        error_signature="Deleted workflow node failed",
        error_signature_key="deleted workflow node failed",
        count=1,
        first_seen_at=datetime(2026, 5, 18, 8, 0, tzinfo=timezone.utc),
        last_seen_at=datetime(2026, 5, 18, 8, 0, tzinfo=timezone.utc),
        latest_error_summary="Deleted workflow node failed",
        state="historical",
        event_ids=["evt-unassigned"],
        examples=[
            _workflow_failure_cluster_example(
                event_id="evt-unassigned",
                occurred_at=datetime(2026, 5, 18, 8, 0, tzinfo=timezone.utc),
                error_summary="Deleted workflow node failed",
                run_state="errored",
            )
        ],
    )
    monkeypatch.setattr(
        service.workflow_reports_admin,
        "read_workflow_failure_clusters_report",
        lambda **kwargs: _workflow_failure_cluster_report(
            [unassigned_cluster],
            generated_at=datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc),
            period_start=datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc),
            period_end=datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc),
        ),
    )

    _set_azure_sso_env(monkeypatch, allowed_users="backup@example.com")
    app = create_app(service)
    store = app.state.admin_user_store
    store.upsert_user(login="viewer@example.com", role="regular", display_name="Viewer")
    store.remove_user_group_assignment(
        login="viewer@example.com", group_id="operator", directory_path="root"
    )
    store.assign_user_group(
        login="viewer@example.com", group_id=AUDITOR_GROUP_ID, directory_path="root"
    )
    client = TestClient(app)
    _complete_azure_login(app, client, login="viewer@example.com", display_name="Viewer")

    response = client.get("/api/admin/reports/workflow-failure-clusters")

    assert response.status_code == 200
    payload = response.json()
    assert [cluster["cluster_id"] for cluster in payload["clusters"]] == ["cluster-unassigned"]
    cluster = payload["clusters"][0]
    assert cluster["graph_id"] == "root"
    assert cluster["graph_name"] == "Unassigned"
    assert cluster["node_id"] == "root"


def test_admin_web_workflow_run_controls_require_workflow_runs_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    config_root = tmp_path / "config" / "trigger-orchestrator"
    config_root.mkdir(parents=True, exist_ok=True)
    _write_json_payload(
        config_root / "config.json",
        {
            "runtime_dir": str(runtime_dir),
            "triggers": [
                {
                    "id": "ops-trigger",
                    "display_name": "Ops trigger",
                    "type": "trigger:cron",
                    "expression": "15 * * * *",
                }
            ],
            "effects": [
                {
                    "id": "ops-effect",
                    "display_name": "Ops effect",
                    "type": "effect:agent:prompt",
                    "session_id": "session-ops",
                    "prompt": "ops",
                }
            ],
            "gates": [],
            "queries": [],
            "edges": [{"source_id": "ops-trigger", "target_id": "ops-effect"}],
            "workflow_graphs": [
                {
                    "id": "graph-ops",
                    "name": "Ops workflow",
                    "directory_path": "root/ops",
                    "user_ids": ["viewer@example.com"],
                    "node_ids": ["ops-trigger", "ops-effect"],
                }
            ],
        },
    )
    _write_json_payload(
        config_root / "layout.json",
        {
            "nodes": {
                "ops-trigger": {"x": 100, "y": 50},
                "ops-effect": {"x": 320, "y": 50},
            }
        },
    )
    _write_json_payload(
        runtime_dir / "trigger-orchestrator-events.json",
        {
            "evt-ops": {
                "event_id": "evt-ops",
                "trigger_id": "ops-trigger",
                "trigger_type": "trigger:cron",
                "trace_id": "trace-ops",
                "occurred_at": "2026-05-24T11:00:00Z",
                "payload": {"scope": "ops"},
                "delivery_state": "delivered",
                "inflight_at": "2026-05-24T11:00:05Z",
                "delivered_at": "2026-05-24T11:00:06Z",
                "idempotency_key": None,
            }
        },
    )
    _write_json_payload(
        runtime_dir / "effect-orchestrator-executions.json",
        {
            "exec-ops": {
                "execution_id": "exec-ops",
                "idempotency_key": "evt-ops::ops-effect",
                "event_id": "evt-ops",
                "trace_id": "trace-ops",
                "source_node_id": "ops-trigger",
                "source_trigger_type": "trigger:cron",
                "effect_id": "ops-effect",
                "effect_type": "effect:agent:prompt",
                "status": "failed",
                "started_at": "2026-05-24T11:01:00Z",
                "finished_at": "2026-05-24T11:01:01Z",
                "resolved_config_hash": "hash-ops",
                "resolved_config": {},
                "input_payload": {"issue_number": "77"},
                "outputs": {},
                "error": "ops failed",
            }
        },
    )
    _write_json_payload(runtime_dir / "await-agent-pending.json", {})

    _set_azure_sso_env(monkeypatch, allowed_users="operator@example.com,viewer@example.com")
    app = create_app(service)
    store = app.state.admin_user_store
    store.update_user(login="viewer@example.com", role="regular", display_name="Viewer")
    store.remove_user_group_assignment(
        login="viewer@example.com", group_id="operator", directory_path="root"
    )
    store.assign_user_group(
        login="viewer@example.com", group_id="auditor", directory_path="root/ops"
    )
    client = TestClient(app)
    _complete_azure_login(app, client, login="viewer@example.com", display_name="Viewer")

    before_snapshot = _snapshot_runtime_files(runtime_dir)
    clear_denied = client.delete("/api/workflow-editor/runs/evt-ops")
    resume_denied = client.post("/api/workflow-editor/runs/evt-ops/resume")
    dismiss_denied = client.delete("/api/workflow-editor/node-failures/ops-effect")
    after_snapshot = _snapshot_runtime_files(runtime_dir)
    relevant_keys = {
        "trigger-orchestrator-events.json",
        "effect-orchestrator-executions.json",
        "await-agent-pending.json",
    }
    relevant_before = {
        path: payload for path, payload in before_snapshot.items() if path in relevant_keys
    }
    relevant_after = {
        path: payload for path, payload in after_snapshot.items() if path in relevant_keys
    }

    assert clear_denied.status_code == 403
    assert clear_denied.json()["error"] == "Permission required: workflow_runs.control"
    assert resume_denied.status_code == 403
    assert resume_denied.json()["error"] == "Permission required: workflow_runs.control"
    assert dismiss_denied.status_code == 403
    assert dismiss_denied.json()["error"] == "Permission required: workflow_runs.control"
    assert relevant_after == relevant_before


def _workflow_binding_validation_payload(tmp_path: Path, prompt: str) -> dict[str, object]:
    return {
        "config": {
            "runtime_dir": str(tmp_path / "runtime"),
            "triggers": [
                {
                    "id": "trigger-ticket-created-main",
                    "display_name": "Ticket created",
                    "type": "trigger:ticket:created",
                    "directories": ["root"],
                }
            ],
            "effects": [
                {
                    "id": "prompt-agent",
                    "display_name": "Prompt agent",
                    "type": "effect:agent:prompt",
                    "session_id": "session-1",
                    "prompt": prompt,
                }
            ],
            "edges": [{"source_id": "trigger-ticket-created-main", "target_id": "prompt-agent"}],
        },
        "layout": {
            "nodes": {
                "trigger-ticket-created-main": {"x": 100, "y": 50},
                "prompt-agent": {"x": 300, "y": 50},
            }
        },
    }


def _agent_create_clone_validation_payload(tmp_path: Path) -> dict[str, object]:
    return {
        "config": {
            "runtime_dir": str(tmp_path / "runtime"),
            "triggers": [
                {
                    "id": "trigger-ticket-created-main",
                    "display_name": "Ticket created",
                    "type": "trigger:ticket:created",
                    "directories": ["root"],
                }
            ],
            "effects": [
                {
                    "id": "effect-agent-create-1",
                    "display_name": "Create agent",
                    "type": "effect:agent:create",
                    "execution_backend": "container_worker_vm",
                    "session_id": "session-1",
                },
                {
                    "id": "effect-agent-clone-repo-1",
                    "display_name": "Clone repo",
                    "type": "effect:agent:clone-repo",
                    "target": "${{effect-agent-create-1.session_id}}",
                    "managed_repo_id": "mr_0123456789abcdefabcd",
                },
            ],
            "edges": [
                {"source_id": "trigger-ticket-created-main", "target_id": "effect-agent-create-1"},
                {"source_id": "effect-agent-create-1", "target_id": "effect-agent-clone-repo-1"},
            ],
        },
        "layout": {
            "nodes": {
                "trigger-ticket-created-main": {"x": 100, "y": 50},
                "effect-agent-create-1": {"x": 300, "y": 50},
                "effect-agent-clone-repo-1": {"x": 500, "y": 50},
            }
        },
    }


def test_admin_web_validate_reports_deleted_binding_node_without_rejecting_save(
    tmp_path: Path,
) -> None:
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))
    payload = _workflow_binding_validation_payload(
        tmp_path,
        "Review ${{deleted-ticket-node.ticket.title}}",
    )

    validate_response = client.post("/api/workflow-editor/validate", json=payload)
    _unlock_privileged_operations(client)
    payload["expected_revision"] = _workflow_expected_revision(client)
    save_response = client.put("/api/workflow-editor/document", json=payload)

    assert validate_response.status_code == 200
    assert save_response.status_code == 200
    diagnostics = validate_response.json()["validation_diagnostics"]
    assert {
        "code": "invalid_binding_node",
        "severity": "error",
        "node_id": "prompt-agent",
        "node_type": "effect:agent:prompt",
        "field_path": "prompt",
        "reference": "deleted-ticket-node.ticket.title",
        "message": "prompt references deleted node deleted-ticket-node",
    } in diagnostics
    assert save_response.json()["validation_diagnostics"] == diagnostics


def test_admin_web_validate_selected_workflow_pack_checks_sibling_pack_ids(
    tmp_path: Path,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
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
    document = service.workflow_config_admin.read_workflow_editor_document(
        workflow_pack="pack-b.v1.json"
    )
    config = dict(document.config)
    effects = [dict(effect) for effect in config["effects"]]
    effects[0]["id"] = "shared-id"
    config["effects"] = effects
    graphs = [graph.model_dump(mode="json") for graph in document.graphs]
    graphs[0]["node_ids"] = ["shared-id"]
    client = TestClient(create_app(service))

    response = client.post(
        "/api/workflow-editor/validate",
        json={
            "workflow_pack": "pack-b.v1.json",
            "config": config,
            "layout": document.layout,
            "graphs": graphs,
        },
    )

    assert response.status_code == 400
    assert "Node IDs must be globally unique" in response.json()["error"]


def test_admin_web_manages_workflow_packs_with_privileged_revision_checks(
    tmp_path: Path,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
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
    client = TestClient(create_app(service))
    pack_list_revision = _workflow_pack_list_revision(client, workflow_pack="pack-a.v1.json")
    _unlock_privileged_operations(client)

    create_response = client.post(
        "/api/workflow-editor/packs",
        json={
            "pack_id": "custom-pack",
            "pack_version": "v1",
            "expected_revision": pack_list_revision,
        },
    )
    assert create_response.status_code == 200
    create_payload = create_response.json()
    assert create_payload["selected_workflow_pack"]["reference"] == "custom-pack.v1.json"
    custom_pack_path = packs_dir / "custom-pack.v1.json"
    assert json.loads(custom_pack_path.read_text(encoding="utf-8"))["ir"] == {
        "edges": [],
        "nodes": [],
        "version": "v1",
    }
    assert json.loads(config_path.read_text(encoding="utf-8"))["workflow_packs"] == [
        "pack-a.v1.json",
        "custom-pack.v1.json",
    ]

    metadata_revision = create_payload["selected_workflow_pack"]["revision"]
    update_response = client.put(
        "/api/workflow-editor/packs",
        json={
            "workflow_pack": "custom-pack.v1.json",
            "pack_id": "renamed-pack",
            "pack_version": "v2",
            "description": "Renamed through Admin Web.",
            "expected_revision": metadata_revision,
        },
    )
    assert update_response.status_code == 200
    updated_pack = json.loads(custom_pack_path.read_text(encoding="utf-8"))
    assert updated_pack["pack_id"] == "renamed-pack"
    assert updated_pack["pack_version"] == "v2"
    assert updated_pack["description"] == "Renamed through Admin Web."

    stale_update_response = client.put(
        "/api/workflow-editor/packs",
        json={
            "workflow_pack": "custom-pack.v1.json",
            "pack_id": "stale-name",
            "pack_version": "v3",
            "expected_revision": metadata_revision,
        },
    )
    assert stale_update_response.status_code == 409

    remove_revision = update_response.json()["document"]["pack_list_revision"]
    remove_response = client.request(
        "DELETE",
        "/api/workflow-editor/packs",
        json={
            "workflow_pack": "pack-a.v1.json",
            "expected_revision": remove_revision,
        },
    )
    assert remove_response.status_code == 200
    assert json.loads(config_path.read_text(encoding="utf-8"))["workflow_packs"] == [
        "custom-pack.v1.json"
    ]
    assert pack_a_path.exists()
    assert (packs_dir / "pack-a.v1.layout.json").exists()

    history_response = client.get("/api/workflow-editor/packs/history?limit=10")
    assert history_response.status_code == 200
    history_payload = history_response.json()
    assert [entry["operation"] for entry in history_payload["entries"]] == [
        "remove",
        "update_metadata",
        "create",
    ]
    assert history_payload["entries"][0]["workflow_pack"] == "pack-a.v1.json"
    assert history_payload["entries"][0]["actor"]["login"] == "admin"
    custom_history_response = client.get(
        "/api/workflow-editor/packs/history?workflow_pack=custom-pack.v1.json"
    )
    assert custom_history_response.status_code == 200
    assert [entry["operation"] for entry in custom_history_response.json()["entries"]] == [
        "update_metadata",
        "create",
    ]
    detail_response = client.get(
        f"/api/workflow-editor/packs/history/{history_payload['entries'][-1]['change_id']}"
    )
    assert detail_response.status_code == 200
    detail_payload = detail_response.json()
    assert detail_payload["entry"]["operation"] == "create"
    assert (
        next(file for file in detail_payload["before_files"] if file["label"] == "pack")["exists"]
        is False
    )
    assert (
        next(file for file in detail_payload["after_files"] if file["label"] == "pack")["content"][
            "pack_id"
        ]
        == "custom-pack"
    )


def test_admin_web_workflow_pack_mutations_require_root_pack_management_permission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    service.directory_admin.create_child(parent="root", name="ops")
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
    (packs_dir / "pack-a.v1.layout.json").write_text(json.dumps({"nodes": {}}), encoding="utf-8")
    config_root = tmp_path / "config" / "trigger-orchestrator"
    config_root.mkdir(parents=True, exist_ok=True)
    (config_root / "config.json").write_text(
        json.dumps(
            {"runtime_dir": str(tmp_path / "runtime"), "workflow_packs": ["pack-a.v1.json"]}
        ),
        encoding="utf-8",
    )
    store = _admin_web_user_store(tmp_path)
    store.upsert_user(login="owner@example.com", role="admin")
    store.upsert_user(login="scoped-writer@example.com", role="regular")
    store.remove_user_group_assignment(
        login="scoped-writer@example.com", group_id="operator", directory_path="root"
    )
    store.grant_user_workflow_permission(
        login="scoped-writer@example.com",
        permission=WORKFLOWS_WRITE_PERMISSION,
        directory_path="root/ops",
    )
    store.upsert_user(login="pack-manager@example.com", role="regular")
    store.remove_user_group_assignment(
        login="pack-manager@example.com", group_id="operator", directory_path="root"
    )
    store.upsert_group(
        group_id="pack-managers",
        display_name="Pack Managers",
        description="Manage workflow pack metadata and active pack list.",
    )
    store.set_group_permissions("pack-managers", [WORKFLOW_PACKS_MANAGE_PERMISSION])
    store.assign_user_group(
        login="pack-manager@example.com", group_id="pack-managers", directory_path="root"
    )
    pack_list_revision = (
        service.workflow_config_admin.read_composed_workflow_editor_document().pack_list_revision
    )
    assert isinstance(pack_list_revision, str)
    _set_azure_sso_env(monkeypatch, allowed_users=None)
    app = create_app(service)
    client = TestClient(app)

    scoped_login = _complete_azure_login(
        app, client, login="scoped-writer@example.com", display_name="Scoped Writer"
    )
    _unlock_privileged_operations(client)
    scoped_response = client.post(
        "/api/workflow-editor/packs",
        json={
            "directory_path": "root/ops",
            "pack_id": "ops-pack",
            "pack_version": "v1",
            "expected_revision": pack_list_revision,
        },
    )

    assert scoped_login.status_code == 303
    assert scoped_response.status_code == 403
    assert scoped_response.json()["error"] == "Permission required: workflow_packs.manage"

    manager_login = _complete_azure_login(
        app, client, login="pack-manager@example.com", display_name="Pack Manager"
    )
    _unlock_privileged_operations(client)
    manager_response = client.post(
        "/api/workflow-editor/packs",
        json={
            "directory_path": "root/ops",
            "pack_id": "managed-pack",
            "pack_version": "v1",
            "expected_revision": pack_list_revision,
        },
    )

    assert manager_login.status_code == 303
    assert manager_response.status_code == 200
    assert manager_response.json()["selected_workflow_pack"]["reference"] == "managed-pack.v1.json"


def test_admin_web_save_seeds_new_ticket_created_trigger_cursor(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    runtime_dir = tmp_path / "runtime"
    config_root = tmp_path / "config" / "trigger-orchestrator"
    pack_root = tmp_path / "config" / "workflow-packs"
    config_root.mkdir(parents=True, exist_ok=True)
    pack_root.mkdir(parents=True, exist_ok=True)
    existing_trigger = {
        "id": "ticket-existing",
        "display_name": "Ticket created",
        "type": "trigger:ticket:created",
        "directories": ["root/team-a"],
    }
    pack_path = pack_root / "ticket-pack.v1.json"
    pack_path.write_text(
        json.dumps(
            {
                "pack_id": "ticket-pack",
                "pack_version": "v1",
                "ir": {"version": "v1", "nodes": [existing_trigger], "edges": []},
            }
        ),
        encoding="utf-8",
    )
    (config_root / "config.json").write_text(
        json.dumps(
            {
                "runtime_dir": str(runtime_dir),
                "workflow_packs": ["ticket-pack.v1.json"],
            }
        ),
        encoding="utf-8",
    )
    cursor_path = runtime_dir / "trigger-cursors-ticket-created.json"
    cursor_path.write_text(
        json.dumps(
            {
                "preserve-me": {"last_created_at": "2026-05-01T00:00:00Z"},
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    for issue_number, directory_path, created_at in [
        ("1", "root/team-a", datetime(2026, 5, 2, 8, 0, tzinfo=timezone.utc)),
        ("2", "root/team-a/subteam", datetime(2026, 5, 2, 11, 30, tzinfo=timezone.utc)),
        ("3", "root/team-b", datetime(2026, 5, 3, 9, 0, tzinfo=timezone.utc)),
    ]:
        issue = LocalIssueRecord(
            issue_id=f"{directory_path}::local::{issue_number}",
            issue_number=issue_number,
            title=f"Issue {issue_number}",
            body="Body",
            yaml_path=str(tmp_path / f"issue-{issue_number}.yaml"),
            directory_path=directory_path,
            created_at=created_at,
            updated_at=created_at,
        )
        service.local_issue_store.put(issue.issue_id, issue)

    client = TestClient(create_app(service))
    copied_trigger = {
        **existing_trigger,
        "id": "ticket-copy",
        "display_name": "Ticket created Copy",
    }
    _unlock_privileged_operations(client)
    response = client.put(
        "/api/workflow-editor/document",
        json={
            "config": {
                "runtime_dir": str(runtime_dir),
                "triggers": [existing_trigger, copied_trigger],
                "gates": [],
                "effects": [],
                "queries": [],
                "edges": [],
            },
            "layout": {"nodes": {}},
            "expected_revision": _workflow_expected_revision(client),
        },
    )

    assert response.status_code == 200
    cursor_payload = json.loads(cursor_path.read_text(encoding="utf-8"))
    assert cursor_payload["preserve-me"] == {"last_created_at": "2026-05-01T00:00:00Z"}
    assert "ticket-existing" not in cursor_payload
    assert cursor_payload["ticket-copy"]["last_created_at"] == "2026-05-02T11:30:00Z"


def test_admin_web_validate_reports_missing_binding_output(tmp_path: Path) -> None:
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))
    payload = _workflow_binding_validation_payload(
        tmp_path,
        "Review ${{trigger-ticket-created-main.missing_output}}",
    )

    response = client.post("/api/workflow-editor/validate", json=payload)

    assert response.status_code == 200
    assert {
        "code": "missing_binding_output",
        "severity": "error",
        "node_id": "prompt-agent",
        "node_type": "effect:agent:prompt",
        "field_path": "prompt",
        "reference": "trigger-ticket-created-main.missing_output",
        "message": "prompt references missing output trigger-ticket-created-main.missing_output",
    } in response.json()["validation_diagnostics"]


def test_admin_web_validate_reports_agent_create_missing_execution_backend(
    tmp_path: Path,
) -> None:
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))
    payload = {
        "config": {
            "runtime_dir": str(tmp_path / "runtime"),
            "triggers": [],
            "gates": [],
            "effects": [
                {
                    "id": "agent-create",
                    "display_name": "Create agent",
                    "type": "effect:agent:create",
                }
            ],
            "queries": [],
            "edges": [],
        },
        "layout": {"nodes": {"agent-create": {"x": 100, "y": 50}}},
    }

    response = client.post("/api/workflow-editor/validate", json=payload)

    assert response.status_code == 200
    assert {
        "code": "agent_create_missing_execution_backend",
        "severity": "error",
        "node_id": "agent-create",
        "node_type": "effect:agent:create",
        "field_path": "execution_backend",
        "message": "effect:agent:create requires an execution backend.",
    } in response.json()["validation_diagnostics"]


def test_admin_web_validate_preserves_domain_diagnostics_for_for_gate(
    tmp_path: Path,
) -> None:
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))
    payload = {
        "config": {
            "runtime_dir": str(tmp_path / "runtime"),
            "triggers": [
                {
                    "id": "trigger-cron-1",
                    "display_name": "Cron",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "queries": [
                {
                    "id": "query-pull-request-list-1",
                    "display_name": "Pull request list",
                    "type": "query:pull-request:list",
                    "provider": "azure_devops",
                    "repo": "repo-name",
                    "status": "active",
                    "top": 50,
                }
            ],
            "gates": [
                {
                    "id": "gate-for-1",
                    "display_name": "For each item",
                    "type": "gate:for",
                    "items": "${{query-pull-request-list-1.pull_requests}}",
                }
            ],
            "effects": [
                {
                    "id": "effect-invalid-1",
                    "display_name": "Invalid edge target",
                    "type": "effect:event:emit",
                    "event_type": "for_gate_invalid_edge",
                    "payload": {},
                }
            ],
            "edges": [
                {"source_id": "trigger-cron-1", "target_id": "query-pull-request-list-1"},
                {"source_id": "query-pull-request-list-1", "target_id": "gate-for-1"},
                {
                    "source_id": "gate-for-1",
                    "source_port": "other",
                    "target_id": "effect-invalid-1",
                },
            ],
        },
        "layout": {"nodes": {}},
        "graphs": [],
    }

    response = client.post("/api/workflow-editor/validate", json=payload)

    assert response.status_code == 200
    diagnostics = response.json()["validation_diagnostics"]
    assert {
        "code": "node_capability",
        "message": "gate:for gate-for-1 uses invalid source_port value(s): other",
        "severity": "error",
    } in diagnostics


def test_admin_web_validate_requires_while_body_loopback(
    tmp_path: Path,
) -> None:
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))
    payload = {
        "config": {
            "runtime_dir": str(tmp_path / "runtime"),
            "triggers": [
                {
                    "id": "trigger-cron-1",
                    "display_name": "Cron",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "gates": [
                {
                    "id": "gate-while-1",
                    "display_name": "Retry while failing",
                    "type": "gate:while",
                    "when": {"left": 1, "equals": 1},
                    "max_iterations": 3,
                }
            ],
            "effects": [
                {
                    "id": "effect-body-1",
                    "display_name": "Body",
                    "type": "effect:event:emit",
                    "event_type": "while_body",
                    "payload": {},
                }
            ],
            "edges": [
                {"source_id": "trigger-cron-1", "target_id": "gate-while-1"},
                {
                    "source_id": "gate-while-1",
                    "source_port": "body",
                    "target_id": "effect-body-1",
                },
            ],
        },
        "layout": {"nodes": {}},
        "graphs": [],
    }

    response = client.post("/api/workflow-editor/validate", json=payload)

    assert response.status_code == 200
    diagnostics = response.json()["validation_diagnostics"]
    assert {
        "code": "node_capability",
        "message": (
            "gate:while gate-while-1 body branch must have an allow_cycle path back to gate-while-1"
        ),
        "node_id": "gate-while-1",
        "node_type": "gate:while",
        "severity": "error",
    } in diagnostics


def test_admin_web_validate_exposes_backend_output_schemas_for_switch_gate(
    tmp_path: Path,
) -> None:
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))
    payload = {
        "config": {
            "runtime_dir": str(tmp_path / "runtime"),
            "triggers": [
                {
                    "id": "trigger-ticket-created-main",
                    "display_name": "Ticket created",
                    "type": "trigger:ticket:created",
                    "directories": ["root"],
                }
            ],
            "gates": [
                {
                    "id": "gate-switch-1",
                    "display_name": "Route ticket",
                    "type": "gate:switch",
                    "default_port": "fallback",
                    "cases": [
                        {
                            "port": "ready",
                            "label": "Ready route",
                            "when": {
                                "left": "${{trigger-ticket-created-main.ticket.state}}",
                                "equals": "ready",
                            },
                        },
                        {
                            "port": "ready",
                            "label": "Ready match",
                            "match": {"ticket": {"state": "ready"}},
                        },
                    ],
                }
            ],
            "effects": [
                {
                    "id": "prompt-agent",
                    "display_name": "Prompt agent",
                    "type": "effect:agent:prompt",
                    "session_id": "session-1",
                    "prompt": "Review ${{gate-switch-1.ticket.body}}",
                }
            ],
            "edges": [
                {"source_id": "trigger-ticket-created-main", "target_id": "gate-switch-1"},
                {
                    "source_id": "gate-switch-1",
                    "source_port": "ready",
                    "target_id": "prompt-agent",
                },
            ],
        },
        "layout": {"nodes": {}},
        "graphs": [],
    }

    response = client.post("/api/workflow-editor/validate", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["validation_diagnostics"] == []
    assert body["output_schemas"]["gate-switch-1"]["ticket"]["properties"]["body"] == {
        "type": "string"
    }


def test_admin_web_validate_exposes_backend_output_schemas_for_agent_effects(
    tmp_path: Path,
) -> None:
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))
    payload = _agent_create_clone_validation_payload(tmp_path)

    response = client.post("/api/workflow-editor/validate", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["validation_diagnostics"] == []
    assert body["output_schemas"]["effect-agent-create-1"]["session_id"] == {"type": "string"}
    assert body["output_schemas"]["effect-agent-clone-repo-1"]["target_dir"] == {"type": "string"}


def test_admin_web_validate_exposes_backend_output_schemas_for_agent_prompt_effects(
    tmp_path: Path,
) -> None:
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))
    payload = {
        "config": {
            "runtime_dir": str(tmp_path / "runtime"),
            "triggers": [
                {
                    "id": "trigger-cron-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "effects": [
                {
                    "id": "effect-prompt-1",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "session_id": "session-1",
                    "prompt": "Continue",
                },
                {
                    "id": "effect-enqueue-1",
                    "display_name": "Enqueue",
                    "type": "effect:agent:enqueue-prompt",
                    "session_id": "session-1",
                    "prompt": "Continue",
                },
            ],
            "edges": [
                {"source_id": "trigger-cron-1", "target_id": "effect-prompt-1"},
                {
                    "source_id": "effect-prompt-1",
                    "target_id": "effect-enqueue-1",
                    "source_port": "started",
                },
            ],
        }
    }

    response = client.post("/api/workflow-editor/validate", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["validation_diagnostics"] == []
    assert body["output_schemas"]["effect-prompt-1"]["dispatch_id"] == {"type": "string"}
    assert body["output_schemas"]["effect-prompt-1"]["thread_id"] == {"type": "string"}
    assert body["output_schemas"]["effect-prompt-1"]["success"] == {"type": "boolean"}
    assert body["output_schemas"]["effect-prompt-1"]["error_message"] == {"type": "string"}
    assert body["output_schemas"]["effect-prompt-1"]["error_class"] == {"type": "string"}
    assert body["output_schemas"]["effect-prompt-1"]["failed_phase"] == {"type": "string"}
    assert body["output_schemas"]["effect-prompt-1"]["session_id"] == {"type": "string"}
    assert body["output_schemas"]["effect-prompt-1"]["vm_name"] == {"type": "string"}
    assert body["output_schemas"]["effect-enqueue-1"]["dispatch_id"] == {"type": "string"}
    assert body["output_schemas"]["effect-enqueue-1"]["thread_id"] == {"type": "string"}
    assert body["output_schemas"]["effect-enqueue-1"]["success"] == {"type": "boolean"}
    assert body["output_schemas"]["effect-enqueue-1"]["error_message"] == {"type": "string"}
    assert body["output_schemas"]["effect-enqueue-1"]["error_class"] == {"type": "string"}
    assert body["output_schemas"]["effect-enqueue-1"]["failed_phase"] == {"type": "string"}
    assert body["output_schemas"]["effect-enqueue-1"]["queued"] == {"type": "boolean"}
    assert body["output_schemas"]["effect-enqueue-1"]["queue_key"] == {"type": "string"}
    assert body["output_schemas"]["effect-enqueue-1"]["session_id"] == {"type": "string"}
    assert body["output_schemas"]["effect-enqueue-1"]["vm_name"] == {"type": "string"}
    assert body["output_schemas"]["effect-enqueue-1"]["coalesced_count"] == {"type": "integer"}
    assert body["output_schemas"]["effect-enqueue-1"]["retry_count"] == {"type": "integer"}
    assert body["output_schemas"]["effect-enqueue-1"]["max_retries"] == {"type": "integer"}


def test_admin_web_validate_exposes_backend_output_schemas_for_state_nodes(
    tmp_path: Path,
) -> None:
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))
    payload = {
        "config": {
            "runtime_dir": str(tmp_path / "runtime"),
            "triggers": [
                {
                    "id": "trigger-state",
                    "display_name": "State trigger",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "effects": [
                {
                    "id": "effect-state-update-1",
                    "display_name": "Update workflow state",
                    "type": "effect:state:update",
                    "namespace": "workflow",
                    "key": "cursor",
                    "operation": "set",
                    "value": {"step": 1},
                }
            ],
            "queries": [
                {
                    "id": "query-state-get-1",
                    "display_name": "Read workflow state",
                    "type": "query:state:get",
                    "namespace": "workflow",
                    "key": "cursor",
                }
            ],
            "edges": [
                {"source_id": "trigger-state", "target_id": "effect-state-update-1"},
                {"source_id": "effect-state-update-1", "target_id": "query-state-get-1"},
            ],
            "workflow_graphs": [
                {
                    "id": "graph-state",
                    "name": "State graph",
                    "directory_paths": ["root"],
                    "node_ids": [
                        "trigger-state",
                        "effect-state-update-1",
                        "query-state-get-1",
                    ],
                }
            ],
        },
        "layout": {
            "nodes": {
                "trigger-state": {"x": 100, "y": 50},
                "effect-state-update-1": {"x": 300, "y": 50},
                "query-state-get-1": {"x": 500, "y": 50},
            }
        },
    }

    response = client.post("/api/workflow-editor/validate", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["validation_diagnostics"] == []
    assert body["output_schemas"]["effect-state-update-1"]["graph_id"] == {"type": "string"}
    assert body["output_schemas"]["effect-state-update-1"]["value"] == {"type": "object"}
    assert body["output_schemas"]["effect-state-update-1"]["version"] == {"type": "integer"}
    assert body["output_schemas"]["query-state-get-1"]["graph_id"] == {"type": "string"}
    assert body["output_schemas"]["query-state-get-1"]["value"] == {"type": "object"}
    assert body["output_schemas"]["query-state-get-1"]["version"] == {"type": "integer"}


def test_admin_web_validate_scopes_binding_diagnostics_to_workflow_graphs(
    tmp_path: Path,
) -> None:
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))
    payload = _agent_create_clone_validation_payload(tmp_path)
    config = payload["config"]
    assert isinstance(config, dict)
    triggers = config["triggers"]
    effects = config["effects"]
    edges = config["edges"]
    assert isinstance(triggers, list)
    assert isinstance(effects, list)
    assert isinstance(edges, list)
    triggers.append(
        {
            "id": "trigger-ticket-created-copy",
            "display_name": "Ticket created Copy",
            "type": "trigger:ticket:created",
            "directories": ["root"],
        }
    )
    effects.append(
        {
            "id": "effect-agent-clone-repo-copy",
            "display_name": "Clone repo Copy",
            "type": "effect:agent:clone-repo",
            "target": "${{effect-agent-create-1.session_id}}",
            "managed_repo_id": "mr_0123456789abcdefabcd",
        }
    )
    edges.append(
        {"source_id": "trigger-ticket-created-copy", "target_id": "effect-agent-clone-repo-copy"}
    )
    payload["graphs"] = [
        {
            "id": "issue-handling",
            "name": "Issue handling",
            "directory_paths": ["root"],
            "node_ids": [
                "trigger-ticket-created-main",
                "effect-agent-create-1",
                "effect-agent-clone-repo-1",
            ],
        },
        {
            "id": "copied-graph",
            "name": "Copied graph",
            "directory_paths": ["root"],
            "node_ids": [
                "trigger-ticket-created-copy",
                "effect-agent-clone-repo-copy",
            ],
        },
    ]

    response = client.post("/api/workflow-editor/validate", json=payload)

    assert response.status_code == 200
    diagnostics = response.json()["validation_diagnostics"]
    assert all(
        diagnostic.get("node_id") != "effect-agent-clone-repo-1" for diagnostic in diagnostics
    )
    assert {
        "code": "invalid_binding_node",
        "severity": "error",
        "node_id": "effect-agent-clone-repo-copy",
        "node_type": "effect:agent:clone-repo",
        "field_path": "target",
        "reference": "effect-agent-create-1.session_id",
        "message": "target references deleted node effect-agent-create-1",
    } in diagnostics


def test_admin_web_validate_reports_malformed_binding_fallback(tmp_path: Path) -> None:
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))
    payload = _workflow_binding_validation_payload(
        tmp_path,
        "Review ${{trigger-ticket-created-main.ticket ?? {}}}",
    )

    response = client.post("/api/workflow-editor/validate", json=payload)

    assert response.status_code == 200
    assert {
        "code": "malformed_binding_fallback",
        "severity": "error",
        "node_id": "prompt-agent",
        "node_type": "effect:agent:prompt",
        "field_path": "prompt",
        "reference": "${{trigger-ticket-created-main.ticket ?? {}}",
        "message": "prompt has malformed fallback syntax",
    } in response.json()["validation_diagnostics"]


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

    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))
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

    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))

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

    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))

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


def test_admin_web_reports_error_routed_workflow_runs_without_terminal_failure(
    tmp_path: Path,
) -> None:
    fixture = _seed_workflow_run_detail_fixture(tmp_path)
    runtime_dir = tmp_path / "runtime"
    trigger_events_path = runtime_dir / "trigger-orchestrator-events.json"
    effect_executions_path = runtime_dir / "effect-orchestrator-executions.json"

    trigger_events = json.loads(trigger_events_path.read_text(encoding="utf-8"))
    trigger_events["evt-routed"] = {
        "event_id": "evt-routed",
        "trigger_id": "trigger-ticket",
        "trigger_type": "trigger:ticket:created",
        "trace_id": "trace-routed",
        "occurred_at": "2026-05-01T11:00:00Z",
        "payload": {"issue_number": "101"},
        "delivery_state": "delivered",
        "inflight_at": "2026-05-01T11:00:05Z",
        "delivered_at": "2026-05-01T11:00:06Z",
        "idempotency_key": None,
    }
    _write_json_payload(trigger_events_path, trigger_events)

    effect_executions = json.loads(effect_executions_path.read_text(encoding="utf-8"))
    effect_executions["exec-routed"] = {
        "execution_id": "exec-routed",
        "idempotency_key": "evt-routed::effect-prompt",
        "event_id": "evt-routed",
        "trace_id": "trace-routed",
        "source_node_id": "trigger-ticket",
        "source_trigger_type": "trigger:ticket:created",
        "effect_id": "effect-prompt",
        "effect_type": "effect:agent:prompt",
        "status": "error_routed",
        "started_at": "2026-05-01T11:00:10Z",
        "finished_at": "2026-05-01T11:00:11Z",
        "resolved_config_hash": "hash-routed",
        "resolved_config": {
            "session_id": fixture["session_id"],
            "vm_name": "vm-detail",
            "prompt": "Investigate the issue",
            "dispatch_id": "dispatch-routed",
            "thread_id": "thread-routed",
            "execution_id": "execution-routed",
            "artifact_ids": [],
            "request_id": "approval-rich",
        },
        "input_payload": {
            "issue_number": "101",
            "session_id": fixture["session_id"],
            "request_id": "approval-rich",
        },
        "outputs": {
            "success": False,
            "dispatch_id": "dispatch-routed",
            "session_id": fixture["session_id"],
            "vm_name": "vm-detail",
            "error_message": "prompt dispatch failed",
            "error_class": "DispatchError",
            "failed_phase": "dispatch",
            "published_event_id": "evt-routed-published",
            "completion_event_branch_port": "error",
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
                    "resolved_value_preview": '"101"',
                    "value_type": "str",
                    "error": None,
                }
            ],
        },
        "binding_error": None,
        "published_event_id": "evt-routed-published",
        "completion_event_enqueued_at": "2026-05-01T11:00:12Z",
        "config_revision": fixture["rich_revision"],
        "error": "prompt dispatch failed",
    }
    _write_json_payload(effect_executions_path, effect_executions)

    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))

    runs_response = client.get("/api/workflow-editor/runs")
    detail_response = client.get(
        "/api/workflow-editor/run-detail", params={"event_id": "evt-routed"}
    )

    assert runs_response.status_code == 200
    runs_payload = runs_response.json()
    routed_run = next(run for run in runs_payload["runs"] if run["event_id"] == "evt-routed")
    assert routed_run["run_state"] == "error_routed"
    assert routed_run["failed_node_id"] is None
    assert routed_run["failed_node_status"] is None
    assert routed_run["error"] == "prompt dispatch failed"
    assert routed_run["resumable"] is False
    assert runs_payload["node_activity"]["effect-prompt"]["error_routed"] == 1

    assert detail_response.status_code == 200
    detail_payload = detail_response.json()
    assert detail_payload["event_id"] == "evt-routed"
    assert detail_payload["run_state"] == "error_routed"
    assert detail_payload["failed_node_id"] is None
    assert detail_payload["failed_node_status"] is None
    assert detail_payload["error"] == "prompt dispatch failed"
    assert detail_payload["resumable"] is False
    routed_node = next(
        node for node in detail_payload["nodes"] if node["node_id"] == "effect-prompt"
    )
    assert routed_node["status"] == "error_routed"
    assert routed_node["outputs"]["error_message"] == "prompt dispatch failed"
    assert routed_node["outputs"]["error_class"] == "DispatchError"
    assert routed_node["outputs"]["failed_phase"] == "dispatch"
    assert routed_node["outputs"]["published_event_id"] == "evt-routed-published"
    assert routed_node["outputs"]["completion_event_branch_port"] == "error"


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

    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))

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


def test_admin_web_can_dismiss_all_failed_runs_for_workflow_node(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    _write_json_payload(
        runtime_dir / "effect-orchestrator-executions.json",
        {
            "exec-failed-1": {
                "execution_id": "exec-failed-1",
                "idempotency_key": "evt-failed-1::effect-prompt",
                "event_id": "evt-failed-1",
                "trace_id": "trace-failed-1",
                "source_node_id": "trigger-ticket",
                "source_trigger_type": "trigger:ticket:created",
                "effect_id": "effect-prompt",
                "effect_type": "effect:agent:prompt",
                "status": "failed",
                "started_at": "2026-04-08T12:00:05Z",
                "finished_at": "2026-04-08T12:00:07Z",
                "resolved_config_hash": "abc",
                "resolved_config": {},
                "input_payload": {},
                "outputs": {},
                "error": "first failure",
            },
            "exec-failed-2": {
                "execution_id": "exec-failed-2",
                "idempotency_key": "evt-failed-2::effect-prompt",
                "event_id": "evt-failed-2",
                "trace_id": "trace-failed-2",
                "source_node_id": "trigger-ticket",
                "source_trigger_type": "trigger:ticket:created",
                "effect_id": "effect-prompt",
                "effect_type": "effect:agent:prompt",
                "status": "failed",
                "started_at": "2026-04-08T12:01:05Z",
                "finished_at": "2026-04-08T12:01:07Z",
                "resolved_config_hash": "def",
                "resolved_config": {},
                "input_payload": {},
                "outputs": {},
                "error": "second failure",
            },
            "exec-dismissed": {
                "execution_id": "exec-dismissed",
                "idempotency_key": "evt-dismissed::effect-prompt",
                "event_id": "evt-dismissed",
                "trace_id": "trace-dismissed",
                "source_node_id": "trigger-ticket",
                "source_trigger_type": "trigger:ticket:created",
                "effect_id": "effect-prompt",
                "effect_type": "effect:agent:prompt",
                "status": "failed",
                "started_at": "2026-04-08T12:02:05Z",
                "finished_at": "2026-04-08T12:02:07Z",
                "resolved_config_hash": "ghi",
                "resolved_config": {},
                "input_payload": {},
                "outputs": {},
                "dismissed_at": "2026-04-08T12:03:00Z",
                "dismiss_reason": "manual_dismiss_errored_workflow_run",
            },
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
                "started_at": "2026-04-08T12:04:05Z",
                "finished_at": "2026-04-08T12:04:07Z",
                "resolved_config_hash": "jkl",
                "resolved_config": {},
                "input_payload": {},
                "outputs": {},
            },
            "exec-other": {
                "execution_id": "exec-other",
                "idempotency_key": "evt-other::effect-other",
                "event_id": "evt-other",
                "trace_id": "trace-other",
                "source_node_id": "trigger-ticket",
                "source_trigger_type": "trigger:ticket:created",
                "effect_id": "effect-other",
                "effect_type": "effect:agent:prompt",
                "status": "failed",
                "started_at": "2026-04-08T12:05:05Z",
                "finished_at": "2026-04-08T12:05:07Z",
                "resolved_config_hash": "mno",
                "resolved_config": {},
                "input_payload": {},
                "outputs": {},
                "error": "other node failure",
            },
        },
    )
    _write_json_payload(
        runtime_dir / "gate-orchestrator-executions.json",
        {
            "gate-failed": {
                "execution_id": "gate-failed",
                "idempotency_key": "evt-gate::gate-review",
                "event_id": "evt-gate",
                "trace_id": "trace-gate",
                "source_node_id": "trigger-ticket",
                "source_trigger_type": "trigger:ticket:created",
                "gate_id": "gate-review",
                "gate_type": "gate:filter",
                "status": "failed",
                "started_at": "2026-04-08T12:06:05Z",
                "finished_at": "2026-04-08T12:06:07Z",
                "resolved_config_hash": "pqr",
                "resolved_config": {},
                "input_payload": {},
                "outputs": {},
                "inputs_by_source": {},
                "input_event_ids_by_source": {},
                "input_occurred_at_by_source": {},
                "error": "gate failure",
            }
        },
    )

    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))

    before_response = client.get("/api/workflow-editor/runs")
    dismiss_response = client.delete("/api/workflow-editor/node-failures/effect-prompt")
    after_response = client.get("/api/workflow-editor/runs")

    assert before_response.status_code == 200
    assert before_response.json()["node_activity"]["effect-prompt"] == {
        "failed": 2,
        "running": 1,
    }
    assert dismiss_response.status_code == 200
    assert dismiss_response.json()["node_id"] == "effect-prompt"
    assert dismiss_response.json()["effect_records_updated"] == 2
    assert dismiss_response.json()["gate_records_updated"] == 0
    assert after_response.status_code == 200
    assert after_response.json()["node_activity"]["effect-prompt"] == {"running": 1}
    assert after_response.json()["node_activity"]["effect-other"] == {"failed": 1}
    assert after_response.json()["node_activity"]["gate-review"] == {"failed": 1}

    updated_effects = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    assert updated_effects["exec-failed-1"]["dismiss_reason"] == "manual_dismiss_node_failures"
    assert updated_effects["exec-failed-2"]["dismiss_reason"] == "manual_dismiss_node_failures"
    assert updated_effects["exec-running"].get("dismissed_at") is None
    assert updated_effects["exec-other"].get("dismissed_at") is None

    dismiss_gate_response = client.delete("/api/workflow-editor/node-failures/gate-review")
    final_response = client.get("/api/workflow-editor/runs")

    assert dismiss_gate_response.status_code == 200
    assert dismiss_gate_response.json()["effect_records_updated"] == 0
    assert dismiss_gate_response.json()["gate_records_updated"] == 1
    assert final_response.status_code == 200
    assert "gate-review" not in final_response.json()["node_activity"]


def test_admin_web_dismisses_workflow_node_failures_with_sqlite_fast_path(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    _write_trigger_orchestrator_sqlite_config(tmp_path)

    db_path = runtime_dir / "orchestration.db"
    effect_store = SqliteExecutionStore(db_path)
    gate_store = SqliteGateExecutionStore(db_path)

    target_failed_1 = EffectExecutionRecord(
        execution_id="exec-failed-1",
        idempotency_key="idem-failed-1",
        event_id="evt-failed-1",
        trace_id="trace-failed-1",
        source_node_id="trigger-ticket",
        source_trigger_type="trigger:ticket:created",
        effect_id="effect-prompt",
        effect_type="effect:agent:prompt",
        status="failed",
        started_at=datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 4, 23, 12, 1, tzinfo=timezone.utc),
        resolved_config_hash="hash-failed-1",
        resolved_config={},
        input_payload={},
        outputs={},
        error="first failure",
    )
    target_failed_2 = EffectExecutionRecord(
        execution_id="exec-failed-2",
        idempotency_key="idem-failed-2",
        event_id="evt-failed-2",
        trace_id="trace-failed-2",
        source_node_id="trigger-ticket",
        source_trigger_type="trigger:ticket:created",
        effect_id="effect-prompt",
        effect_type="effect:agent:prompt",
        status="failed",
        started_at=datetime(2026, 4, 23, 12, 2, tzinfo=timezone.utc),
        finished_at=datetime(2026, 4, 23, 12, 3, tzinfo=timezone.utc),
        resolved_config_hash="hash-failed-2",
        resolved_config={},
        input_payload={},
        outputs={},
        error="second failure",
    )
    target_running = EffectExecutionRecord(
        execution_id="exec-running",
        idempotency_key="idem-running",
        event_id="evt-running",
        trace_id="trace-running",
        source_node_id="trigger-ticket",
        source_trigger_type="trigger:ticket:created",
        effect_id="effect-prompt",
        effect_type="effect:agent:prompt",
        status="running",
        started_at=datetime(2026, 4, 23, 12, 4, tzinfo=timezone.utc),
        finished_at=datetime(2026, 4, 23, 12, 5, tzinfo=timezone.utc),
        resolved_config_hash="hash-running",
        resolved_config={},
        input_payload={},
        outputs={},
    )
    target_succeeded = EffectExecutionRecord(
        execution_id="exec-succeeded",
        idempotency_key="idem-succeeded",
        event_id="evt-succeeded",
        trace_id="trace-succeeded",
        source_node_id="trigger-ticket",
        source_trigger_type="trigger:ticket:created",
        effect_id="effect-prompt",
        effect_type="effect:agent:prompt",
        status="succeeded",
        started_at=datetime(2026, 4, 23, 12, 6, tzinfo=timezone.utc),
        finished_at=datetime(2026, 4, 23, 12, 7, tzinfo=timezone.utc),
        resolved_config_hash="hash-succeeded",
        resolved_config={},
        input_payload={},
        outputs={},
    )
    target_dismissed = EffectExecutionRecord(
        execution_id="exec-dismissed",
        idempotency_key="idem-dismissed",
        event_id="evt-dismissed",
        trace_id="trace-dismissed",
        source_node_id="trigger-ticket",
        source_trigger_type="trigger:ticket:created",
        effect_id="effect-prompt",
        effect_type="effect:agent:prompt",
        status="failed",
        started_at=datetime(2026, 4, 23, 12, 8, tzinfo=timezone.utc),
        finished_at=datetime(2026, 4, 23, 12, 9, tzinfo=timezone.utc),
        resolved_config_hash="hash-dismissed",
        resolved_config={},
        input_payload={},
        outputs={},
        dismiss_reason="manual_dismiss_errored_workflow_run",
        dismissed_at=datetime(2026, 4, 23, 12, 10, tzinfo=timezone.utc),
    )
    target_gate_failed = GateExecutionRecord(
        execution_id="gate-failed",
        idempotency_key="gate-idem-failed",
        event_id="evt-gate-failed",
        trace_id="trace-gate-failed",
        source_node_id="trigger-ticket",
        source_trigger_type="trigger:ticket:created",
        gate_id="gate-review",
        gate_type="gate:filter",
        status="failed",
        started_at=datetime(2026, 4, 23, 12, 11, tzinfo=timezone.utc),
        finished_at=datetime(2026, 4, 23, 12, 12, tzinfo=timezone.utc),
        resolved_config_hash="hash-gate-failed",
        resolved_config={},
        input_payload={},
        outputs={},
        inputs_by_source={},
        input_event_ids_by_source={},
        input_occurred_at_by_source={},
        error="gate failure",
    )
    target_gate_dismissed = GateExecutionRecord(
        execution_id="gate-dismissed",
        idempotency_key="gate-idem-dismissed",
        event_id="evt-gate-dismissed",
        trace_id="trace-gate-dismissed",
        source_node_id="trigger-ticket",
        source_trigger_type="trigger:ticket:created",
        gate_id="gate-review",
        gate_type="gate:filter",
        status="failed",
        started_at=datetime(2026, 4, 23, 12, 13, tzinfo=timezone.utc),
        finished_at=datetime(2026, 4, 23, 12, 14, tzinfo=timezone.utc),
        resolved_config_hash="hash-gate-dismissed",
        resolved_config={},
        input_payload={},
        outputs={},
        inputs_by_source={},
        input_event_ids_by_source={},
        input_occurred_at_by_source={},
        error="already dismissed gate failure",
        dismiss_reason="manual_dismiss_node_failures",
        dismissed_at=datetime(2026, 4, 23, 12, 15, tzinfo=timezone.utc),
    )
    effect_store.record_execution(target_failed_1)
    effect_store.record_execution(target_failed_2)
    effect_store.record_execution(target_running)
    effect_store.record_execution(target_succeeded)
    effect_store.record_execution(target_dismissed)
    gate_store.record_execution(target_gate_failed)
    gate_store.record_execution(target_gate_dismissed)

    for index in range(180):
        status = ("failed", "running", "succeeded")[index % 3]
        record = _effect_execution_record(
            execution_id=f"exec-unrelated-{index}",
            idempotency_key=f"idem-unrelated-{index}",
            event_id=f"evt-unrelated-{index}",
            effect_id=f"effect-unrelated-{index % 12}",
            status=status,
            started_at=datetime(2026, 4, 23, 12, 20, tzinfo=timezone.utc),
            finished_at=datetime(2026, 4, 23, 12, 21, tzinfo=timezone.utc),
        )
        effect_store.record_execution(record)
    for index in range(120):
        status = ("failed", "running", "succeeded")[index % 3]
        record = _gate_execution_record(
            execution_id=f"gate-unrelated-{index}",
            idempotency_key=f"gate-idem-unrelated-{index}",
            event_id=f"evt-gate-unrelated-{index}",
            gate_id=f"gate-unrelated-{index % 8}",
            status=status,
            started_at=datetime(2026, 4, 23, 12, 22, tzinfo=timezone.utc),
            finished_at=datetime(2026, 4, 23, 12, 23, tzinfo=timezone.utc),
        )
        gate_store.record_execution(record)

    effect_before_payloads = _sqlite_payload_json_by_execution_id(db_path, "effect_executions")
    gate_before_payloads = _sqlite_payload_json_by_execution_id(db_path, "gate_executions")
    effect_unchanged_before = {
        execution_id: payload
        for execution_id, payload in effect_before_payloads.items()
        if execution_id not in {"exec-failed-1", "exec-failed-2", "exec-dismissed"}
    }
    gate_unchanged_before = {
        execution_id: payload
        for execution_id, payload in gate_before_payloads.items()
        if execution_id != "gate-dismissed"
    }

    def fail_update_all(*_: object, **__: object) -> None:
        raise AssertionError("SQLite fast path should not call update_all()")

    monkeypatch.setattr(SqliteExecutionStore, "update_all", fail_update_all, raising=False)
    monkeypatch.setattr(SqliteGateExecutionStore, "update_all", fail_update_all, raising=False)

    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))

    before_response = client.get("/api/workflow-editor/runs")
    dismiss_response = client.delete("/api/workflow-editor/node-failures/effect-prompt")
    after_response = client.get("/api/workflow-editor/runs")

    assert before_response.status_code == 200
    assert before_response.json()["node_activity"]["effect-prompt"] == {
        "failed": 2,
        "running": 1,
    }
    assert before_response.json()["node_activity"]["gate-review"] == {
        "failed": 1,
    }
    assert dismiss_response.status_code == 200
    dismiss_payload = dismiss_response.json()
    assert dismiss_payload["worker_id"] == "trigger-orchestrator"
    assert dismiss_payload["node_id"] == "effect-prompt"
    assert dismiss_payload["effect_records_updated"] == 2
    assert dismiss_payload["gate_records_updated"] == 0
    assert after_response.status_code == 200
    assert after_response.json()["node_activity"]["effect-prompt"] == {"running": 1}
    assert after_response.json()["node_activity"]["gate-review"] == {
        "failed": 1,
    }
    assert (
        after_response.json()["node_activity"]["effect-unrelated-0"]
        == before_response.json()["node_activity"]["effect-unrelated-0"]
    )
    assert (
        after_response.json()["node_activity"]["gate-unrelated-1"]
        == before_response.json()["node_activity"]["gate-unrelated-1"]
    )

    effect_after_payloads = _sqlite_payload_json_by_execution_id(db_path, "effect_executions")
    gate_after_payloads = _sqlite_payload_json_by_execution_id(db_path, "gate_executions")

    assert effect_after_payloads["exec-failed-1"] != effect_before_payloads["exec-failed-1"]
    assert effect_after_payloads["exec-failed-2"] != effect_before_payloads["exec-failed-2"]
    assert gate_after_payloads["gate-failed"] == gate_before_payloads["gate-failed"]
    assert {
        execution_id: effect_after_payloads[execution_id]
        for execution_id in effect_unchanged_before
    } == effect_unchanged_before
    assert {
        execution_id: gate_after_payloads[execution_id] for execution_id in gate_unchanged_before
    } == gate_unchanged_before

    assert effect_store.get_execution("exec-failed-1").dismissed_at is not None
    assert (
        effect_store.get_execution("exec-failed-1").dismiss_reason == "manual_dismiss_node_failures"
    )
    assert effect_store.get_execution("exec-running").dismissed_at is None
    assert effect_store.get_execution("exec-succeeded").dismissed_at is None
    assert (
        effect_store.get_execution("exec-dismissed").dismiss_reason
        == "manual_dismiss_errored_workflow_run"
    )
    assert gate_store.get_execution("gate-failed").dismissed_at is None
    assert gate_store.get_execution("gate-failed").dismiss_reason is None
    assert (
        gate_store.get_execution("gate-dismissed").dismiss_reason == "manual_dismiss_node_failures"
    )


def test_admin_web_lists_resolves_and_downloads_published_artifacts(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    archive = tmp_path / "artifact.tar.gz"
    archive.write_bytes(b"published archive")
    store = PublishedArtifactStore(runtime_dir)
    store.publish_archive(
        key="runs/session-1/report",
        session_id="session-1",
        archive_path=archive,
        source_artifacts_root="/artifacts",
        source_file_count=1,
        source_byte_count=12,
        published_at=datetime(2026, 5, 20, 8, 0, tzinfo=timezone.utc),
    )
    store.publish_archive(
        key="runs/session-2/report",
        session_id="session-2",
        archive_path=archive,
        source_artifacts_root="/artifacts",
        source_file_count=2,
        source_byte_count=24,
        published_at=datetime(2026, 5, 20, 9, 0, tzinfo=timezone.utc),
    )
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))

    list_response = client.get("/api/admin/artifacts?limit=1")
    detail_response = client.get("/api/admin/artifacts/runs%2Fsession-1%2Freport")
    filtered_response = client.get("/api/admin/artifacts?published_after=2026-05-20T08:30:00Z")
    download_response = client.get("/api/admin/artifact-downloads/runs%2Fsession-1%2Freport")

    assert list_response.status_code == 200
    assert list_response.json()["total"] == 2
    assert list_response.json()["items"][0]["key"] == "runs/session-2/report"
    assert list_response.json()["next_cursor"] == "1"
    assert detail_response.status_code == 200
    assert detail_response.json()["key"] == "runs/session-1/report"
    assert detail_response.json()["filename"] == "report.tar.gz"
    assert filtered_response.status_code == 200
    assert [item["key"] for item in filtered_response.json()["items"]] == ["runs/session-2/report"]
    assert download_response.status_code == 200
    assert download_response.content == b"published archive"
    assert (
        download_response.headers["content-disposition"] == 'attachment; filename="report.tar.gz"'
    )


def test_admin_web_filters_published_artifacts_by_artifact_space(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    archive = tmp_path / "artifact.tar.gz"
    archive.write_bytes(b"published archive")
    artifact_store = PublishedArtifactStore(runtime_dir)
    artifact_store.publish_archive(
        key="runs/team/report",
        session_id="session-1",
        archive_path=archive,
        published_at=datetime(2026, 5, 20, 8, 0, tzinfo=timezone.utc),
    )
    artifact_store.publish_archive(
        key="runs/team/backend/report",
        session_id="session-2",
        archive_path=archive,
        published_at=datetime(2026, 5, 20, 9, 0, tzinfo=timezone.utc),
    )
    artifact_store.publish_archive(
        key="runs/other/report",
        session_id="session-3",
        archive_path=archive,
        published_at=datetime(2026, 5, 20, 10, 0, tzinfo=timezone.utc),
    )
    artifact_store.publish_archive(
        key="runs/unspaced/report",
        session_id="session-4",
        archive_path=archive,
        published_at=datetime(2026, 5, 20, 11, 0, tzinfo=timezone.utc),
    )
    space_store = ArtifactSpaceStore(runtime_dir)
    space_store.ensure_space(path="/teams/team")
    space_store.ensure_space(path="/teams/team/projects/backend")
    space_store.ensure_space(path="/teams/other")
    space_store.publish_version(
        space_path="/teams/team",
        artifact_id="team-report",
        artifact_key="runs/team/report",
    )
    space_store.publish_version(
        space_path="/teams/team/projects/backend",
        artifact_id="backend-report",
        artifact_key="runs/team/backend/report",
    )
    space_store.publish_version(
        space_path="/teams/other",
        artifact_id="other-report",
        artifact_key="runs/other/report",
    )
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))

    all_response = client.get("/api/admin/artifacts")
    recursive_response = client.get(
        "/api/admin/artifacts",
        params={"space_path": "/teams/team", "limit": "1"},
    )
    recursive_next_response = client.get(
        "/api/admin/artifacts",
        params={"space_path": "/teams/team", "limit": "1", "cursor": "1"},
    )
    direct_response = client.get(
        "/api/admin/artifacts",
        params={"space_path": "/teams/team", "space_recursive": "false"},
    )
    key_response = client.get(
        "/api/admin/artifacts",
        params={"space_path": "/teams/team", "key": "runs/team/backend/report"},
    )
    unspaced_key_response = client.get(
        "/api/admin/artifacts",
        params={"space_path": "/teams/team", "key": "runs/unspaced/report"},
    )

    assert all_response.status_code == 200
    assert all_response.json()["total"] == 4
    assert recursive_response.status_code == 200
    assert recursive_response.json()["total"] == 2
    assert recursive_response.json()["items"][0]["key"] == "runs/team/backend/report"
    assert recursive_response.json()["next_cursor"] == "1"
    assert recursive_next_response.status_code == 200
    assert recursive_next_response.json()["items"][0]["key"] == "runs/team/report"
    assert recursive_next_response.json()["next_cursor"] is None
    assert direct_response.status_code == 200
    assert [item["key"] for item in direct_response.json()["items"]] == ["runs/team/report"]
    assert key_response.status_code == 200
    assert [item["key"] for item in key_response.json()["items"]] == ["runs/team/backend/report"]
    assert unspaced_key_response.status_code == 200
    assert unspaced_key_response.json()["total"] == 0


def test_admin_web_artifact_key_ending_download_resolves_metadata(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    archive = tmp_path / "artifact.tar.gz"
    archive.write_bytes(b"published archive")
    store = PublishedArtifactStore(runtime_dir)
    store.publish_archive(
        key="runs/foo",
        session_id="session-1",
        archive_path=archive,
    )
    store.publish_archive(
        key="runs/foo/download",
        session_id="session-1",
        archive_path=archive,
    )
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))

    detail_response = client.get("/api/admin/artifacts/runs%2Ffoo%2Fdownload")
    download_response = client.get("/api/admin/artifact-downloads/runs%2Ffoo%2Fdownload")

    assert detail_response.status_code == 200
    assert detail_response.json()["key"] == "runs/foo/download"
    assert download_response.status_code == 200
    assert download_response.content == b"published archive"


def test_admin_web_public_communication_artifact_download_requires_signed_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AUTH_MODE", "local_password")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_PASSWORD", "secret")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_SESSION_SECRET", "admin-secret")
    monkeypatch.setenv("TEAM_LINK_SECRET", "link-secret")
    config_path = write_config(tmp_path)
    service = build_admin_runtime(config_path)
    team_directory = DirectoryService(service.runtime.directories).create(
        parent="root", name="team"
    )
    DirectoryConfigStore(service.runtime.directories, runtime_dir=service.config.runtime_dir).write(
        team_directory.path,
        domain="providers",
        payload={"communication_artifact_link_secret_env": "TEAM_LINK_SECRET"},
    )
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[],
            directory_path=team_directory.path,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    )
    record = CommunicationArtifactStore(service.config.runtime_dir).stage(
        provider="workspace",
        session_id="session-1",
        content=b"report bytes",
        original_filename="report.txt",
        mime_type="text/plain",
    )
    link = issue_communication_artifact_download_link(
        artifact_id=record.artifact_id,
        artifact_expires_at=record.expires_at,
        public_base_url="https://control.example.test",
        secret_env="TEAM_LINK_SECRET",
        ttl_seconds=3600,
    )
    client = TestClient(create_app(service))

    missing_token = client.get(f"/public/communication-artifacts/{record.artifact_id}")
    bad_token = client.get(f"/public/communication-artifacts/{record.artifact_id}?token=tampered")
    downloaded = client.get(
        f"/public/communication-artifacts/{record.artifact_id}?token={link.token}"
    )

    assert missing_token.status_code == 403
    assert bad_token.status_code == 403
    assert downloaded.status_code == 200
    assert downloaded.content == b"report bytes"
    assert downloaded.headers["content-disposition"] == 'attachment; filename="report.txt"'
    assert downloaded.headers["cache-control"] == "no-store"


def test_admin_web_uploads_single_file_as_published_artifact(tmp_path: Path) -> None:
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))

    response = client.post(
        "/api/admin/artifacts/uploads",
        data={"key": "manuals/true-app/reviewed/run-1", "artifact_path": "input/reviewed.docx"},
        files={
            "file": (
                "reviewed.docx",
                b"docx bytes",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    duplicate = client.post(
        "/api/admin/artifacts/uploads",
        data={"key": "manuals/true-app/reviewed/run-1"},
        files={"file": ("reviewed.docx", b"again")},
    )
    download = client.get("/api/admin/artifact-downloads/manuals%2Ftrue-app%2Freviewed%2Frun-1")

    assert response.status_code == 201
    payload = response.json()
    assert payload["key"] == "manuals/true-app/reviewed/run-1"
    assert payload["session_id"] == "manual-upload"
    assert payload["source_file_count"] == 1
    assert payload["metadata"]["source"] == "manual_upload"
    assert payload["metadata"]["original_filename"] == "reviewed.docx"
    assert payload["metadata"]["artifact_path"] == "input/reviewed.docx"
    assert duplicate.status_code == 400
    assert "already exists" in duplicate.json()["error"]
    assert download.status_code == 200
    with tarfile.open(fileobj=io.BytesIO(download.content), mode="r:gz") as archive:
        member = archive.extractfile("input/reviewed.docx")
        assert member is not None
        assert member.read() == b"docx bytes"


def _write_json_payload(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _write_run_detail_audit_log(path: Path, *, session_id: str, vm_id: str) -> None:
    audit_entries = [
        {
            "event_type": "tool_call",
            "session_id": session_id,
            "vm_id": vm_id,
            "directory_path": "root",
            "tool_name": "prompt_agent",
            "request_id": "request-rich-node",
            "correlation_id": "correlation-rich-node",
            "approval_request_id": "approval-rich",
            "workflow_run_id": "evt-detail",
            "trace_id": "trace-detail",
            "decision": "allowed",
            "duration_ms": 17,
            "args": {
                "session_id": session_id,
                "request_id": "request-rich-node",
                "approval_request_id": "approval-rich",
                "prompt": "Investigate the issue",
                "token": "super-secret-token",
            },
            "result": {
                "request_id": "request-rich-node",
                "status": "success",
                "response_token": "response-token-rich",
                "secret": "result-secret",
            },
            "created_at": "2026-05-16T10:05:00+00:00",
        },
        {
            "event_type": "tool_call",
            "session_id": session_id,
            "vm_id": vm_id,
            "directory_path": "root",
            "tool_name": "make_phone_call",
            "correlation_id": "correlation-run-level",
            "workflow_run_id": "evt-detail",
            "trace_id": "trace-detail",
            "decision": "denied",
            "reason": "blocked",
            "args": {
                "session_id": session_id,
                "correlation_id": "correlation-run-level",
                "number": "+1-555-0100",
            },
            "result": {
                "status": "denied",
                "reason": "blocked",
            },
            "created_at": "2026-05-16T10:06:00+00:00",
        },
        {
            "event_type": "tool_call",
            "session_id": session_id,
            "vm_id": vm_id,
            "directory_path": "root",
            "tool_name": "make_phone_call",
            "workflow_run_id": "evt-detail",
            "trace_id": "trace-detail",
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
            "created_at": "2026-05-16T10:05:30+00:00",
        },
        {
            "event_type": "tool_call",
            "session_id": session_id,
            "vm_id": vm_id,
            "directory_path": "root",
            "tool_name": "prompt_agent",
            "request_id": "request-legacy-node",
            "approval_request_id": "approval-rich",
            "workflow_run_id": "evt-legacy",
            "trace_id": "trace-legacy",
            "decision": "allowed",
            "args": {
                "session_id": session_id,
                "request_id": "request-legacy-node",
                "approval_request_id": "approval-rich",
            },
            "result": {"status": "success"},
            "created_at": "2026-05-16T09:05:00+00:00",
        },
        {
            "event_type": "tool_call",
            "session_id": session_id,
            "vm_id": vm_id,
            "directory_path": "root",
            "tool_name": "make_phone_call",
            "workflow_run_id": "evt-other",
            "decision": "allowed",
            "args": {"session_id": session_id},
            "result": {"status": "success"},
            "created_at": "2026-05-16T10:07:00+00:00",
        },
        {
            "event_type": "tool_call",
            "session_id": "session-unrelated",
            "vm_id": "vm-unrelated",
            "directory_path": "root",
            "tool_name": "shell",
            "decision": "allowed",
            "args": {"session_id": "session-unrelated"},
            "result": {"status": "success"},
            "created_at": "2026-05-16T08:00:00+00:00",
        },
        {
            "event_type": "tool_call",
            "session_id": "session-notrace",
            "vm_id": "vm-notrace",
            "directory_path": "root",
            "tool_name": "read_file",
            "workflow_run_id": "evt-notrace",
            "decision": "allowed",
            "args": {"path": "notes.txt"},
            "result": {"status": "success"},
            "created_at": "2026-05-16T08:10:00+00:00",
        },
        {
            "event_type": "tool_call",
            "session_id": "session-session-only",
            "vm_id": "vm-session-only",
            "directory_path": "root",
            "tool_name": "list_files",
            "workflow_run_id": "trace-session-only",
            "trace_id": "trace-session-only",
            "decision": "allowed",
            "args": {
                "session_id": "session-session-only",
                "workflow_run_id": "trace-session-only",
                "workflow_trace_id": "trace-session-only",
                "path": ".",
            },
            "result": {
                "status": "success",
                "items": ["README.md"],
            },
            "created_at": "2026-05-16T08:20:00+00:00",
        },
    ]
    path.write_text(
        "\n".join(json.dumps(entry, sort_keys=True) for entry in audit_entries) + "\n",
        encoding="utf-8",
    )


def _seed_workflow_run_detail_fixture(tmp_path: Path) -> dict[str, str]:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    rich_revision = "rev-detail-rich"
    session_id = "session-detail"
    artifact_store = CommunicationArtifactStore(runtime_dir)
    artifact = artifact_store.stage(
        provider="teams",
        session_id=session_id,
        content=b"hello from the run detail fixture\n",
        original_filename="notes.txt",
        created_at=datetime(2026, 5, 16, 10, 4, tzinfo=timezone.utc),
        expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
        metadata={"source": "fixture"},
    )
    approval_request = ApprovalRequest(
        request_id="approval-rich",
        session_id=session_id,
        action_type="effect:agent:prompt",
        payload_digest="digest-rich",
        human_summary="Prompt the agent after review.",
        payload={
            "request_id": "approval-rich",
            "session_id": session_id,
            "artifact_ids": [artifact.artifact_id],
        },
        directory_path="root",
        tool_name="codex",
        status=ApprovalStatus.APPROVED,
        approver="workflow-admin",
        decision_at=datetime(2026, 5, 1, 10, 2, tzinfo=timezone.utc),
        decision_reason="Approved for the run detail fixture.",
        expires_at=datetime(2026, 5, 2, 10, 0, tzinfo=timezone.utc),
        created_at=datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc),
    )
    session_manifest = SessionManifest(
        session_id=session_id,
        vm_id="vm-detail",
        allowed_tools=[],
        expires_at=datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc),
        metadata={"labels": {"fixture": "run-detail"}},
    )
    _write_json_payload(
        runtime_dir / "sessions.json",
        {session_manifest.session_id: session_manifest.model_dump(mode="json")},
    )
    _write_json_payload(
        runtime_dir / "approvals.json",
        {approval_request.request_id: approval_request.model_dump(mode="json")},
    )
    _write_run_detail_audit_log(
        runtime_dir / "audit.jsonl",
        session_id=session_id,
        vm_id="vm-detail",
    )
    _write_json_payload(
        runtime_dir / "trigger-orchestrator-events.json",
        {
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
            "evt-notrace": {
                "event_id": "evt-notrace",
                "trigger_id": "trigger-ticket",
                "trigger_type": "trigger:ticket:created",
                "occurred_at": "2026-05-01T08:00:00Z",
                "payload": {"issue_number": "13"},
                "delivery_state": "delivered",
                "inflight_at": "2026-05-01T08:00:04Z",
                "delivered_at": "2026-05-01T08:00:05Z",
                "idempotency_key": None,
            },
            "evt-session-only": {
                "event_id": "evt-session-only",
                "trigger_id": "trigger-ticket",
                "trigger_type": "trigger:ticket:created",
                "trace_id": "trace-session-only",
                "occurred_at": "2026-05-01T08:20:00Z",
                "payload": {"issue_number": "14"},
                "delivery_state": "delivered",
                "inflight_at": "2026-05-01T08:20:04Z",
                "delivered_at": "2026-05-01T08:20:05Z",
                "idempotency_key": None,
            },
        },
    )
    _write_json_payload(
        runtime_dir / "effect-orchestrator-executions.json",
        {
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
                    "artifact_ids": [artifact.artifact_id],
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
                "input_payload": {
                    "issue_number": "12",
                    "session_id": session_id,
                    "request_id": "approval-rich",
                    "artifact_ids": [artifact.artifact_id],
                },
                "outputs": {},
                "error": "legacy failure",
            },
            "exec-session-only": {
                "execution_id": "exec-session-only",
                "idempotency_key": "evt-session-only::effect-prompt",
                "event_id": "evt-session-only",
                "trace_id": "trace-session-only",
                "source_node_id": "trigger-ticket",
                "source_trigger_type": "trigger:ticket:created",
                "effect_id": "effect-prompt",
                "effect_type": "effect:agent:prompt",
                "status": "succeeded",
                "started_at": "2026-05-01T08:20:06Z",
                "finished_at": "2026-05-01T08:20:07Z",
                "resolved_config_hash": "hash-session-only",
                "resolved_config": {
                    "session_id": "session-session-only",
                    "prompt": "Inspect the workspace",
                },
                "input_payload": {
                    "issue_number": "14",
                    "session_id": "session-session-only",
                },
                "outputs": {
                    "status": "queued",
                },
                "binding_trace": {
                    "consumed_output_keys": ["trigger-ticket.issue_number"],
                    "entries": [],
                },
                "config_revision": rich_revision,
            },
        },
    )
    _write_json_payload(
        runtime_dir / "gate-orchestrator-executions.json",
        {
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
        },
    )
    _write_json_payload(
        runtime_dir / "await-agent-pending.json",
        {
            "gate-await:dispatch-detail": {
                "gate_id": "gate-await",
                "dispatch_id": "dispatch-detail",
                "prompt_event_id": "evt-detail",
                "prompt_source_node_id": "effect-prompt",
                "trace_id": "trace-detail",
                "session_id": session_id,
                "vm_name": "vm-detail",
                "payload": {"artifact_ids": [artifact.artifact_id]},
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
        },
    )
    (runtime_dir / "providers" / "gates" / "await-agent").mkdir(parents=True, exist_ok=True)
    _write_json_payload(
        runtime_dir / "providers" / "gates" / "await-agent" / "pending.json",
        {
            "gate-await:dispatch-provider": {
                "gate_id": "gate-await",
                "dispatch_id": "dispatch-provider",
                "prompt_event_id": "evt-provider",
                "prompt_source_node_id": "effect-prompt",
                "trace_id": "trace-provider",
                "session_id": "session-provider",
                "vm_name": "vm-provider",
                "payload": {},
                "occurred_at": "2026-05-01T11:00:16Z",
            }
        },
    )
    _write_json_payload(
        runtime_dir / "workflow-config-snapshots.json",
        {
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
                        "directory_paths": ["root"],
                        "user_ids": ["operator"],
                        "node_ids": ["trigger-ticket", "effect-prompt", "gate-await"],
                    }
                ],
            }
        },
    )
    return {
        "runtime_dir": str(runtime_dir),
        "session_id": session_id,
        "artifact_id": artifact.artifact_id,
        "rich_revision": rich_revision,
        "rich_event_id": "evt-detail",
        "legacy_event_id": "evt-legacy",
        "trace_id": "trace-detail",
    }


def test_admin_web_run_detail_resolves_identifiers_and_links(tmp_path: Path) -> None:
    fixture = _seed_workflow_run_detail_fixture(tmp_path)
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))

    event_response = client.get(
        "/api/workflow-editor/run-detail", params={"event_id": fixture["rich_event_id"]}
    )
    trace_response = client.get(
        "/api/workflow-editor/run-detail", params={"trace_id": fixture["trace_id"]}
    )
    session_response = client.get(
        "/api/workflow-editor/run-detail", params={"session_id": fixture["session_id"]}
    )
    artifact_response = client.get(
        "/api/workflow-editor/run-detail", params={"artifact_id": fixture["artifact_id"]}
    )

    assert event_response.status_code == 200
    assert trace_response.status_code == 200
    assert session_response.status_code == 200
    assert artifact_response.status_code == 200

    event_payload = event_response.json()
    session_payload = session_response.json()
    assert event_payload["event_id"] == fixture["rich_event_id"]
    assert event_payload["trace_id"] == fixture["trace_id"]
    assert event_payload["run_state"] == "errored"
    assert event_payload["resumable"] is True
    assert event_payload["graph"]["config_revision"] == fixture["rich_revision"]
    assert event_payload["graph"]["snapshot"]["revision"] == fixture["rich_revision"]
    assert event_payload["graph"]["snapshot"]["snapshot_type"] == "compiled_runtime_graph"
    assert event_payload["graph"]["data_availability"]["graph_snapshot"] == "available"
    assert event_payload["graph"]["data_availability"]["approvals"] == "available"
    assert event_payload["graph"]["data_availability"]["agent_links"] == "available"
    assert event_payload["graph"]["data_availability"]["communication_artifacts"] == "available"
    assert event_payload["graph"]["data_availability"]["tool_calls"] == "available"
    assert event_payload["trigger_event"]["payload"] == {"issue_number": "99"}
    assert event_payload["lookup"]["event_id"] == fixture["rich_event_id"]

    candidate_event_ids = [
        candidate["event_id"] for candidate in session_payload["lookup_candidates"]
    ]
    assert candidate_event_ids == [fixture["rich_event_id"], fixture["legacy_event_id"]]
    assert session_payload["event_id"] == fixture["rich_event_id"]
    assert artifact_response.json()["event_id"] == fixture["rich_event_id"]
    assert trace_response.json()["event_id"] == fixture["rich_event_id"]

    rich_node = next(node for node in event_payload["nodes"] if node["node_id"] == "effect-prompt")
    assert rich_node["status"] == "failed"
    assert rich_node["duration_ms"] == 1000
    assert rich_node["resolved_config"]["prompt"] == "Investigate the issue"
    assert rich_node["input_payload"]["request_id"] == "approval-rich"
    assert rich_node["outputs"]["execution_id"] == "execution-detail"
    assert rich_node["binding_trace"]["entries"][0]["config_path"] == "$.prompt"
    assert rich_node["binding_error"] is None
    assert rich_node["retry_resume_eligible"] is True
    tool_call_link = rich_node["tool_call_links"][0]
    assert tool_call_link["tool_name"] == "prompt_agent"
    assert tool_call_link["decision"] == "allowed"
    assert tool_call_link["status"] == "success"
    assert tool_call_link["request_id"] == "request-rich-node"
    assert tool_call_link["correlation_id"] == "correlation-rich-node"
    assert tool_call_link["approval_request_id"] == "approval-rich"
    assert tool_call_link["workflow_run_id"] == fixture["rich_event_id"]
    assert tool_call_link["trace_id"] == fixture["trace_id"]
    assert tool_call_link["href"] == "#tool-calls?q=request-rich-node"
    assert tool_call_link["session_id"] == fixture["session_id"]
    assert tool_call_link["args"]["token"] == "<redacted>"
    assert tool_call_link["result"]["secret"] == "<redacted>"
    assert tool_call_link["redacted_args"]["token"] == "<redacted>"
    assert tool_call_link["redacted_result"]["secret"] == "<redacted>"
    assert tool_call_link["args_summary"] is not None
    assert tool_call_link["result_summary"] is not None
    assert tool_call_link["result_status"] == "available"
    approval_link = rich_node["approval_links"][0]
    assert approval_link["request_id"] == "approval-rich"
    assert approval_link["payload_digest"] == "digest-rich"
    assert approval_link["status"] == "approved"
    assert approval_link["approver"] == "workflow-admin"
    assert approval_link["decision_at"] == "2026-05-01T10:02:00Z"
    assert approval_link["decision_reason"] == "Approved for the run detail fixture."
    assert approval_link["expires_at"] == "2026-05-02T10:00:00Z"

    agent_link = rich_node["agent_links"][0]
    assert agent_link["session_id"] == fixture["session_id"]
    assert agent_link["prompt"] == "Investigate the issue"
    assert agent_link["thread_id"] == "thread-detail"
    assert agent_link["execution_id"] == "execution-detail"
    assert agent_link["artifact_ids"] == [fixture["artifact_id"]]
    assert agent_link["session_status"] == "available"
    assert agent_link["session_available"] is True
    assert agent_link["agent_available"] is True
    assert agent_link["log_status"] == "log_unavailable"
    assert agent_link["log_available"] is False
    assert agent_link["result_status"] == "available"
    assert agent_link["result_summary"] is not None
    assert agent_link["log_reference"]["source_kind"] == "effect_execution"
    assert agent_link["log_reference"]["vm_name"] == "vm-detail"
    assert agent_link["log_reference"]["session_id"] == fixture["session_id"]
    assert agent_link["log_reference"]["dispatch_id"] == "dispatch-detail"

    run_tool_call_link = event_payload["tool_call_links"][0]
    assert run_tool_call_link["tool_name"] == "make_phone_call"
    assert run_tool_call_link["decision"] == "denied"
    assert run_tool_call_link["status"] == "denied"
    assert run_tool_call_link["correlation_id"] == "correlation-run-level"
    assert run_tool_call_link["workflow_run_id"] == fixture["rich_event_id"]
    assert run_tool_call_link["trace_id"] == fixture["trace_id"]
    assert run_tool_call_link["href"] == "#tool-calls?q=correlation-run-level"
    assert run_tool_call_link["session_id"] == fixture["session_id"]
    assert run_tool_call_link["args"]["number"] == "+1-555-0100"
    trace_run_tool_call_link = event_payload["tool_call_links"][1]
    assert trace_run_tool_call_link["tool_name"] == "make_phone_call"
    assert trace_run_tool_call_link["workflow_run_id"] == fixture["rich_event_id"]
    assert trace_run_tool_call_link["trace_id"] == fixture["trace_id"]
    assert trace_run_tool_call_link["href"] == "#tool-calls?q=trace-detail"
    assert trace_run_tool_call_link["session_id"] == fixture["session_id"]
    assert trace_run_tool_call_link["args"]["note"] == "trace-only"

    artifact_link = rich_node["communication_artifact_links"][0]
    assert artifact_link["artifact_id"] == fixture["artifact_id"]
    assert artifact_link["session_id"] == fixture["session_id"]
    assert artifact_link["provider"] == "teams"
    assert artifact_link["original_filename"] == "notes.txt"
    assert artifact_link["metadata"] == {"storage": "host_artifact_store", "source": "fixture"}
    assert rich_node["records"][0]["record_kind"] == "effect_execution"
    assert rich_node["records"][0]["config_revision"] == fixture["rich_revision"]
    assert (
        rich_node["records"][0]["binding_trace"]["entries"][0]["resolved_value_preview"] == '"99"'
    )


def test_admin_web_run_detail_requires_real_audit_identifiers(tmp_path: Path) -> None:
    _seed_workflow_run_detail_fixture(tmp_path)
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))

    response = client.get("/api/workflow-editor/run-detail", params={"event_id": "evt-notrace"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["event_id"] == "evt-notrace"
    assert payload["trace_id"] is None
    assert [link["tool_name"] for link in payload["tool_call_links"]] == ["read_file"]
    assert payload["tool_call_links"][0]["workflow_run_id"] == "evt-notrace"


def test_admin_web_run_detail_attaches_session_scoped_tool_call_to_single_node(
    tmp_path: Path,
) -> None:
    _seed_workflow_run_detail_fixture(tmp_path)
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))

    response = client.get(
        "/api/workflow-editor/run-detail", params={"event_id": "evt-session-only"}
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["event_id"] == "evt-session-only"
    assert payload["trace_id"] == "trace-session-only"
    assert payload["tool_call_links"] == []
    effect_node = next(node for node in payload["nodes"] if node["node_id"] == "effect-prompt")
    assert len(effect_node["tool_call_links"]) == 1
    tool_call_link = effect_node["tool_call_links"][0]
    assert tool_call_link["tool_name"] == "list_files"
    assert tool_call_link["session_id"] == "session-session-only"
    assert tool_call_link["workflow_run_id"] == "trace-session-only"
    assert tool_call_link["trace_id"] == "trace-session-only"
    assert tool_call_link["request_id"] is None
    assert tool_call_link["approval_request_id"] is None
    assert tool_call_link["node_id"] == "effect-prompt"


def test_admin_web_run_detail_marks_missing_legacy_data(tmp_path: Path) -> None:
    fixture = _seed_workflow_run_detail_fixture(tmp_path)
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))

    response = client.get(
        "/api/workflow-editor/run-detail", params={"event_id": fixture["legacy_event_id"]}
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["event_id"] == fixture["legacy_event_id"]
    assert payload["graph"]["data_availability"]["graph_snapshot"] == "missing_legacy_or_unrecorded"
    assert payload["graph"]["data_availability"]["approvals"] == "available"
    assert payload["graph"]["data_availability"]["agent_links"] == "available"
    assert payload["graph"]["data_availability"]["communication_artifacts"] == "available"
    assert payload["graph"]["data_availability"]["tool_calls"] == "available"
    assert payload["graph"]["snapshot"] is None
    assert payload["tool_call_links"] == []
    legacy_node = next(node for node in payload["nodes"] if node["node_id"] == "effect-prompt")
    assert "binding_trace" in legacy_node["missing_fields"]
    assert legacy_node["binding_trace"] is None
    assert legacy_node["records"][0]["binding_trace"] is None
    assert legacy_node["records"][0]["resolved_config"] == {}
    assert legacy_node["approval_links"][0]["request_id"] == "approval-rich"
    assert legacy_node["agent_links"][0]["session_id"] == fixture["session_id"]
    assert legacy_node["communication_artifact_links"][0]["artifact_id"] == fixture["artifact_id"]
    assert legacy_node["tool_call_links"][0]["request_id"] == "request-legacy-node"


def test_admin_web_run_search_finds_latest_graph_and_node_runs_without_sessions(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    config_root = tmp_path / "config" / "trigger-orchestrator"
    config_root.mkdir(parents=True, exist_ok=True)
    _write_json_payload(
        config_root / "config.json",
        {
            "runtime_dir": str(runtime_dir),
            "triggers": [
                {
                    "id": "trigger-ticket",
                    "display_name": "Ticket Created",
                    "type": "trigger:ticket:created",
                    "directories": ["root"],
                }
            ],
            "effects": [
                {
                    "id": "effect-notify",
                    "display_name": "Notify",
                    "type": "effect:event:emit",
                    "event_type": "operator.notice",
                    "payload": {},
                }
            ],
            "edges": [{"source_id": "trigger-ticket", "target_id": "effect-notify"}],
            "workflow_graphs": [
                {
                    "id": "graph-ticket",
                    "name": "Ticket Graph",
                    "directory_paths": ["root"],
                    "user_ids": [],
                    "node_ids": ["trigger-ticket", "effect-notify"],
                }
            ],
        },
    )
    _write_json_payload(
        runtime_dir / "trigger-orchestrator-events.json",
        {
            "evt-no-session-old": {
                "event_id": "evt-no-session-old",
                "trigger_id": "trigger-ticket",
                "trigger_type": "trigger:ticket:created",
                "trace_id": "trace-no-session-old",
                "occurred_at": "2026-05-01T10:00:00Z",
                "payload": {"issue_number": "10"},
                "delivery_state": "delivered",
                "inflight_at": "2026-05-01T10:00:02Z",
                "delivered_at": "2026-05-01T10:00:03Z",
                "idempotency_key": None,
            },
            "evt-no-session-new": {
                "event_id": "evt-no-session-new",
                "trigger_id": "trigger-ticket",
                "trigger_type": "trigger:ticket:created",
                "trace_id": "trace-no-session-new",
                "occurred_at": "2026-05-01T12:00:00Z",
                "payload": {"issue_number": "12"},
                "delivery_state": "delivered",
                "inflight_at": "2026-05-01T12:00:02Z",
                "delivered_at": "2026-05-01T12:00:03Z",
                "idempotency_key": None,
            },
            "evt-with-session": {
                "event_id": "evt-with-session",
                "trigger_id": "trigger-ticket",
                "trigger_type": "trigger:ticket:created",
                "trace_id": "trace-with-session",
                "occurred_at": "2026-05-01T13:00:00Z",
                "payload": {"issue_number": "13"},
                "delivery_state": "delivered",
                "inflight_at": "2026-05-01T13:00:02Z",
                "delivered_at": "2026-05-01T13:00:03Z",
                "idempotency_key": None,
            },
        },
    )
    _write_json_payload(
        runtime_dir / "effect-orchestrator-executions.json",
        {
            "exec-no-session-old": {
                "execution_id": "exec-no-session-old",
                "idempotency_key": "evt-no-session-old::effect-notify",
                "event_id": "evt-no-session-old",
                "trace_id": "trace-no-session-old",
                "source_node_id": "trigger-ticket",
                "source_trigger_type": "trigger:ticket:created",
                "effect_id": "effect-notify",
                "effect_type": "effect:event:emit",
                "status": "succeeded",
                "started_at": "2026-05-01T10:01:00Z",
                "finished_at": "2026-05-01T10:01:01Z",
                "resolved_config_hash": "hash-old",
                "resolved_config": {},
                "input_payload": {"issue_number": "10"},
                "outputs": {"published": True},
            },
            "exec-no-session-new": {
                "execution_id": "exec-no-session-new",
                "idempotency_key": "evt-no-session-new::effect-notify",
                "event_id": "evt-no-session-new",
                "trace_id": "trace-no-session-new",
                "source_node_id": "trigger-ticket",
                "source_trigger_type": "trigger:ticket:created",
                "effect_id": "effect-notify",
                "effect_type": "effect:event:emit",
                "status": "succeeded",
                "started_at": "2026-05-01T12:01:00Z",
                "finished_at": "2026-05-01T12:01:01Z",
                "resolved_config_hash": "hash-new",
                "resolved_config": {},
                "input_payload": {"issue_number": "12"},
                "outputs": {"published": True},
            },
            "exec-with-session": {
                "execution_id": "exec-with-session",
                "idempotency_key": "evt-with-session::effect-notify",
                "event_id": "evt-with-session",
                "trace_id": "trace-with-session",
                "source_node_id": "trigger-ticket",
                "source_trigger_type": "trigger:ticket:created",
                "effect_id": "effect-notify",
                "effect_type": "effect:event:emit",
                "status": "succeeded",
                "started_at": "2026-05-01T13:01:00Z",
                "finished_at": "2026-05-01T13:01:01Z",
                "resolved_config_hash": "hash-session",
                "resolved_config": {"session_id": "session-agent"},
                "input_payload": {"issue_number": "13"},
                "outputs": {"published": True},
            },
        },
    )

    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))

    node_response = client.get(
        "/api/workflow-editor/run-search",
        params={"node_id": "effect-notify", "without_session": "true"},
    )
    graph_response = client.get(
        "/api/workflow-editor/run-search",
        params={"graph_id": "graph-ticket", "without_session": "false", "limit": "2"},
    )

    assert node_response.status_code == 200
    node_payload = node_response.json()
    assert [run["event_id"] for run in node_payload["runs"]] == [
        "evt-no-session-new",
        "evt-no-session-old",
    ]
    assert node_payload["matched"] == 2
    assert node_payload["filters"]["node_id"] == "effect-notify"
    assert node_payload["runs"][0]["run_state"] == "completed"
    assert node_payload["runs"][0]["has_agent_session"] is False
    assert node_payload["runs"][0]["matched_node_ids"] == ["effect-notify"]

    assert graph_response.status_code == 200
    graph_payload = graph_response.json()
    assert graph_payload["matched"] == 3
    assert graph_payload["returned"] == 2
    assert graph_payload["has_more"] is True
    assert graph_payload["filters"]["graph_name"] == "Ticket Graph"
    assert [run["event_id"] for run in graph_payload["runs"]] == [
        "evt-with-session",
        "evt-no-session-new",
    ]
    assert graph_payload["runs"][0]["has_agent_session"] is True
    assert graph_payload["runs"][0]["session_ids"] == ["session-agent"]


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

    client = TestClient(create_app(build_admin_runtime(config_path)))
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

    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))
    response = client.get("/api/workflow-editor/runs")

    assert response.status_code == 200
    payload = response.json()
    assert [run["event_id"] for run in payload["runs"]] == ["evt-running"]


def test_admin_web_returns_400_for_invalid_workflow_editor_payload(tmp_path: Path) -> None:
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))

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

    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))
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

    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))
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

    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))
    response = client.get("/api/workflow-editor/document")

    assert response.status_code == 200
    gate = response.json()["config"]["gates"][0]
    assert gate["outputs"]["current_index"] == {"type": "integer"}
    assert gate["outputs"]["item_count"] == {"type": "integer"}


def test_admin_web_load_normalizes_switch_gate_outputs(tmp_path: Path) -> None:
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
      "id": "gate-switch-1",
      "display_name": "Route ticket",
      "type": "gate:switch",
      "default_port": "fallback",
      "outputs": {
        "incorrect": {"type": "string"}
      },
      "cases": [
        {
          "port": "ready",
          "when": {
            "left": "${{trigger-ticket-created-1.ticket.state}}",
            "equals": "ready"
          }
        }
      ]
    }
  ],
  "effects": [],
  "queries": [],
  "edges": [
    { "source_id": "trigger-ticket-created-1", "target_id": "gate-switch-1" }
  ]
}
""".strip(),
        encoding="utf-8",
    )

    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))
    response = client.get("/api/workflow-editor/document")

    assert response.status_code == 200
    gate = response.json()["config"]["gates"][0]
    assert gate["outputs"]["ticket"]["properties"]["body"] == {"type": "string"}


def test_admin_web_session_endpoint_reports_auth_disabled_by_default(tmp_path: Path) -> None:
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))

    response = client.get("/api/auth/session")

    assert response.status_code == 200
    assert response.json()["auth_mode"] == "none"
    assert response.json()["auth_required"] is False
    assert response.json()["authenticated"] is True
    assert response.json()["role"] == "admin"
    assert response.json()["can_manage_users"] is True
    assert ADMIN_OWNER_PERMISSION in response.json()["permissions"]
    assert response.json()["permissions"] == response.json()["effective_permissions"]
    assert response.json()["branding"]["page_title"] == "Evander Admin"
    assert response.json()["branding"]["header_title"] == "EVANDER"


def test_admin_web_branding_endpoint_reports_defaults(tmp_path: Path, monkeypatch) -> None:
    dist_dir = tmp_path / "dist-without-manifest"
    dist_dir.mkdir()
    monkeypatch.setattr(admin_web_app, "_dist_dir", lambda: dist_dir)
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))

    response = client.get("/api/admin/branding")
    logo = client.get("/api/admin/branding/assets/evander-logo-light.png")

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "page_title": "Evander Admin",
        "header_title": "EVANDER",
        "header_title_visible": False,
        "header_logo_size": "wordmark",
        "header_logo": {
            "light_url": "/api/admin/branding/assets/evander-logo-light.png",
            "dark_url": "/api/admin/branding/assets/evander-logo-dark.png",
            "alt": "Evander Logo",
        },
        "product_logo": {
            "light_url": "/api/admin/branding/assets/evander-logo-light.png",
            "dark_url": "/api/admin/branding/assets/evander-logo-dark.png",
            "alt": "Evander Logo",
        },
        "company_logo": {
            "light_url": "/api/admin/branding/assets/qss-light.png",
            "dark_url": "/api/admin/branding/assets/qss-dark.png",
            "alt": "Quinth Software Solutions",
        },
    }
    assert logo.status_code == 200
    assert logo.content.startswith(b"\x89PNG\r\n\x1a\n")


def test_admin_web_branding_endpoint_uses_hashed_default_assets(
    tmp_path: Path, monkeypatch
) -> None:
    dist_dir = tmp_path / "dist"
    assets_dir = dist_dir / "assets"
    manifest_dir = dist_dir / ".vite"
    assets_dir.mkdir(parents=True)
    manifest_dir.mkdir()
    hashed_assets = {
        "evander-logo-light.png": "assets/evander-logo-light-a1B2c3D4.png",
        "evander-logo-dark.png": "assets/evander-logo-dark-a1B2c3D4.png",
        "qss-light.png": "assets/qss-light-a1B2c3D4.png",
        "qss-dark.png": "assets/qss-dark-a1B2c3D4.png",
    }
    for asset_path in hashed_assets.values():
        (dist_dir / asset_path).write_bytes(b"hashed-logo")
    (manifest_dir / "manifest.json").write_text(
        json.dumps(
            {
                f"../branding/{source_name}": {
                    "file": hashed_path,
                    "src": f"../branding/{source_name}",
                }
                for source_name, hashed_path in hashed_assets.items()
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(admin_web_app, "_dist_dir", lambda: dist_dir)
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))

    branding = client.get("/api/admin/branding")
    session = client.get("/api/auth/session")
    logo = client.get("/assets/evander-logo-light-a1B2c3D4.png")

    assert branding.status_code == 200
    payload = branding.json()
    assert payload["header_logo"]["light_url"] == "/assets/evander-logo-light-a1B2c3D4.png"
    assert payload["header_logo"]["dark_url"] == "/assets/evander-logo-dark-a1B2c3D4.png"
    assert payload["product_logo"] == payload["header_logo"]
    assert payload["company_logo"]["light_url"] == "/assets/qss-light-a1B2c3D4.png"
    assert payload["company_logo"]["dark_url"] == "/assets/qss-dark-a1B2c3D4.png"
    assert session.status_code == 200
    assert session.json()["branding"] == payload
    assert logo.status_code == 200
    assert logo.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_admin_web_branding_custom_asset_dir_keeps_default_filename_assets(
    tmp_path: Path, monkeypatch
) -> None:
    dist_dir = tmp_path / "dist"
    assets_dir = dist_dir / "assets"
    manifest_dir = dist_dir / ".vite"
    assets_dir.mkdir(parents=True)
    manifest_dir.mkdir()
    (assets_dir / "evander-logo-light-a1B2c3D4.png").write_bytes(b"bundled-logo")
    (manifest_dir / "manifest.json").write_text(
        json.dumps(
            {
                "../branding/evander-logo-light.png": {
                    "file": "assets/evander-logo-light-a1B2c3D4.png",
                    "src": "../branding/evander-logo-light.png",
                }
            }
        ),
        encoding="utf-8",
    )
    asset_dir = tmp_path / "custom-branding"
    asset_dir.mkdir()
    (asset_dir / "evander-logo-light.png").write_bytes(b"custom-light-logo")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_BRANDING_ASSET_DIR", str(asset_dir))
    monkeypatch.setattr(admin_web_app, "_dist_dir", lambda: dist_dir)
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))

    branding = client.get("/api/admin/branding")
    logo = client.get("/api/admin/branding/assets/evander-logo-light.png")

    assert branding.status_code == 200
    payload = branding.json()
    assert payload["header_logo"]["light_url"] == (
        "/api/admin/branding/assets/evander-logo-light.png"
    )
    assert payload["product_logo"]["light_url"] == (
        "/api/admin/branding/assets/evander-logo-light.png"
    )
    assert logo.status_code == 200
    assert logo.content == b"custom-light-logo"


def test_admin_web_branding_reads_env_and_serves_assets_without_login(
    tmp_path: Path, monkeypatch
) -> None:
    asset_dir = tmp_path / "branding"
    (asset_dir / "logos").mkdir(parents=True)
    logo_path = asset_dir / "logos" / "product-light.png"
    logo_path.write_bytes(b"brand-logo")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AUTH_MODE", "local_password")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_PASSWORD", "swordfish")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_SESSION_SECRET", "test-secret")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_PAGE_TITLE", "Factory Alpha Admin")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_HEADER_TITLE", "Factory Alpha")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_HEADER_TITLE_VISIBLE", "false")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_HEADER_LOGO_SIZE", "wordmark")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_BRANDING_ASSET_DIR", str(asset_dir))
    monkeypatch.setenv(
        "EVANDER_ADMIN_WEB_PRODUCT_LOGO_LIGHT_URL",
        "logos/product-light.png",
    )
    monkeypatch.setenv("EVANDER_ADMIN_WEB_PRODUCT_LOGO_ALT", "Factory Alpha logo")
    monkeypatch.setenv(
        "EVANDER_ADMIN_WEB_COMPANY_LOGO_DARK_URL",
        "https://assets.example.test/company-dark.png",
    )

    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))

    branding = client.get("/api/admin/branding")
    index = client.get("/")
    logo = client.get("/api/admin/branding/assets/logos/product-light.png")
    blocked = client.get("/api/admin/branding/assets/%2e%2e/gateway.yaml")
    still_requires_auth = client.get("/api/workflow-editor/document")

    assert branding.status_code == 200
    payload = branding.json()
    assert payload["page_title"] == "Factory Alpha Admin"
    assert payload["header_title"] == "Factory Alpha"
    assert payload["header_title_visible"] is False
    assert payload["header_logo_size"] == "wordmark"
    assert payload["product_logo"] == {
        "light_url": "/api/admin/branding/assets/logos/product-light.png",
        "dark_url": "/api/admin/branding/assets/logos/product-light.png",
        "alt": "Factory Alpha logo",
    }
    assert payload["header_logo"] == payload["product_logo"]
    assert payload["company_logo"] == {
        "light_url": "https://assets.example.test/company-dark.png",
        "dark_url": "https://assets.example.test/company-dark.png",
        "alt": "Quinth Software Solutions",
    }
    assert "<title>Factory Alpha Admin</title>" in index.text
    assert "window.__ADMIN_WEB_BRANDING__=" in index.text
    assert "Factory Alpha" in index.text
    assert logo.status_code == 200
    assert logo.content == b"brand-logo"
    assert blocked.status_code == 404
    assert still_requires_auth.status_code == 401


def test_admin_web_requires_login_when_password_configured(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EVANDER_ADMIN_WEB_USERNAME", "operator")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_PASSWORD", "swordfish")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_SESSION_SECRET", "test-secret")
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))

    unauthorized = client.get("/api/workflow-editor/document")
    login = client.post("/api/auth/login", json={"username": "operator", "password": "swordfish"})
    authorized = client.get("/api/workflow-editor/document")

    assert unauthorized.status_code == 401
    assert login.status_code == 200
    assert login.json()["auth_mode"] == "local_password"
    assert login.json()["authenticated"] is True
    assert login.json()["role"] == "admin"
    assert login.json()["can_manage_users"] is True
    assert ADMIN_OWNER_PERMISSION in login.json()["permissions"]
    assert login.json()["permissions"] == login.json()["effective_permissions"]
    assert authorized.status_code == 200


def test_admin_web_local_password_username_permissions_are_case_insensitive(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("EVANDER_ADMIN_WEB_USERNAME", "Jonathan")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_PASSWORD", "swordfish")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_SESSION_SECRET", "test-secret")
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))

    login = client.post("/api/auth/login", json={"username": "jonathan", "password": "swordfish"})
    session = client.get("/api/auth/session")
    authorized = client.get("/api/workflow-editor/document")

    assert login.status_code == 200
    assert login.json()["username"] == "Jonathan"
    assert login.json()["role"] == "admin"
    assert ADMIN_OWNER_PERMISSION in login.json()["permissions"]
    assert session.status_code == 200
    assert session.json()["role"] == "admin"
    assert WORKFLOWS_READ_PERMISSION in session.json()["effective_permissions"]
    assert authorized.status_code == 200


def test_admin_web_bootstraps_local_password_user_from_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EVANDER_ADMIN_WEB_USERNAME", "Operator")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_PASSWORD", "swordfish")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_SESSION_SECRET", "test-secret")

    app = create_app(build_admin_runtime(write_config(tmp_path)))
    users = app.state.admin_user_store.list_users()
    credential = app.state.admin_user_store.get_local_password_credential("operator")

    assert [user.login for user in users] == ["operator"]
    assert users[0].role == "admin"
    assert users[0].display_name == "Operator"
    assert credential is not None
    assert credential.login == "operator"


def test_admin_web_local_password_supports_multiple_users(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EVANDER_ADMIN_WEB_USERNAME", "admin")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_PASSWORD", "admin-pass")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_SESSION_SECRET", "test-secret")
    app = create_app(build_admin_runtime(write_config(tmp_path)))
    admin_client = TestClient(app)
    regular_client = TestClient(app)

    admin_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "admin-pass"},
    )
    created = admin_client.post(
        "/api/admin/users",
        json={
            "login": "Operator@Example.com",
            "role": "regular",
            "display_name": "Operator",
            "password": "operator-pass",
        },
    )
    login = regular_client.post(
        "/api/auth/login",
        json={"username": "operator@example.com", "password": "operator-pass"},
    )
    session = regular_client.get("/api/auth/session")

    assert created.status_code == 200
    assert created.json()["user"]["login"] == "operator@example.com"
    assert created.json()["user"]["local_password_enabled"] is True
    assert created.json()["user"]["password_last_changed_at"] is not None
    assert "password_hash" not in json.dumps(created.json())
    assert login.status_code == 200
    assert login.json()["username"] == "operator@example.com"
    assert login.json()["display_name"] == "Operator"
    assert login.json()["role"] == "regular"
    assert login.json()["can_manage_users"] is False
    assert session.json()["authenticated"] is True
    assert session.json()["username"] == "operator@example.com"


def test_admin_web_local_password_user_create_requires_password(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("EVANDER_ADMIN_WEB_USERNAME", "admin")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_PASSWORD", "admin-pass")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_SESSION_SECRET", "test-secret")
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))
    client.post("/api/auth/login", json={"username": "admin", "password": "admin-pass"})

    created = client.post(
        "/api/admin/users",
        json={"login": "operator@example.com", "role": "regular"},
    )

    assert created.status_code == 400
    assert created.json()["error"] == "password is required for new local password users"


def test_admin_web_local_password_reset_changes_user_password(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EVANDER_ADMIN_WEB_USERNAME", "admin")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_PASSWORD", "admin-pass")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_SESSION_SECRET", "test-secret")
    app = create_app(build_admin_runtime(write_config(tmp_path)))
    admin_client = TestClient(app)
    regular_client = TestClient(app)

    admin_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "admin-pass"},
    )
    admin_client.post(
        "/api/admin/users",
        json={
            "login": "operator@example.com",
            "role": "regular",
            "password": "old-pass",
        },
    )
    reset = admin_client.patch(
        "/api/admin/users/operator@example.com",
        json={"password": "new-pass"},
    )
    old_login = regular_client.post(
        "/api/auth/login",
        json={"username": "operator@example.com", "password": "old-pass"},
    )
    new_login = regular_client.post(
        "/api/auth/login",
        json={"username": "operator@example.com", "password": "new-pass"},
    )

    assert reset.status_code == 200
    assert reset.json()["user"]["local_password_enabled"] is True
    assert old_login.status_code == 401
    assert new_login.status_code == 200


def test_admin_web_deleted_local_password_user_loses_existing_session(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("EVANDER_ADMIN_WEB_USERNAME", "admin")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_PASSWORD", "admin-pass")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_SESSION_SECRET", "test-secret")
    app = create_app(build_admin_runtime(write_config(tmp_path)))
    admin_client = TestClient(app)
    regular_client = TestClient(app)

    admin_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "admin-pass"},
    )
    admin_client.post(
        "/api/admin/users",
        json={
            "login": "operator@example.com",
            "role": "regular",
            "password": "operator-pass",
        },
    )
    regular_client.post(
        "/api/auth/login",
        json={"username": "operator@example.com", "password": "operator-pass"},
    )

    before_delete = regular_client.get("/api/workflow-editor/document")
    deleted = admin_client.delete("/api/admin/users/operator@example.com")
    session = regular_client.get("/api/auth/session")
    after_delete = regular_client.get("/api/workflow-editor/document")

    assert before_delete.status_code == 200
    assert deleted.status_code == 200
    assert session.status_code == 200
    assert session.json()["authenticated"] is False
    assert session.json()["permissions"] == []
    assert after_delete.status_code == 401


def test_workflow_event_api_mints_token_and_publishes_external_event(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("EVANDER_ADMIN_WEB_USERNAME", "operator")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_PASSWORD", "swordfish")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_SESSION_SECRET", "test-secret")
    service = build_admin_runtime(write_config(tmp_path))
    client = TestClient(create_app(service))

    unauthorized_mint = client.post("/api/workflow-events/token")
    login = client.post("/api/auth/login", json={"username": "operator", "password": "swordfish"})
    minted = client.post("/api/workflow-events/token")
    token = minted.json()["access_token"]
    published = client.post(
        "/api/workflow-events",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "event_type": "deploy_requested",
            "payload": {"target": "demo"},
            "correlation_key": "deploy:demo",
            "idempotency_key": "request-1",
        },
    )
    repeated = client.post(
        "/api/workflow-events",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "event_type": "deploy_requested",
            "payload": {"target": "demo"},
            "correlation_key": "deploy:demo",
            "idempotency_key": "request-1",
        },
    )

    assert unauthorized_mint.status_code == 401
    assert login.status_code == 200
    assert minted.status_code == 200
    assert minted.json()["token_type"] == "Bearer"
    assert minted.json()["expires_in_seconds"] == 3600
    assert minted.json()["allowed_event_prefix"] == "external."
    assert published.status_code == 202
    assert repeated.status_code == 202
    assert published.json()["event_id"] == repeated.json()["event_id"]
    assert published.json()["event_type"] == "external.deploy_requested"
    assert published.json()["payload"] == {"target": "demo"}
    assert published.json()["correlation_key"] == "deploy:demo"
    assert published.json()["source"]["source_type"] == "workflow_event_api"
    assert published.json()["source"]["user_login"] == "operator"


def test_workflow_event_api_rejects_invalid_bearer_payload_and_event_type(
    tmp_path: Path,
) -> None:
    app = create_app(build_admin_runtime(write_config(tmp_path)))
    client = TestClient(app)
    minted = client.post("/api/workflow-events/token")
    token = minted.json()["access_token"]
    expired_token = app.state.auth._encode_signed_payload(
        {
            "kind": "workflow_event_api",
            "allowed_event_prefix": "external.",
            "iat": 1,
            "exp": 1,
        }
    )

    missing_token = client.post(
        "/api/workflow-events",
        json={"event_type": "deploy_requested", "payload": {}},
    )
    expired = client.post(
        "/api/workflow-events",
        headers={"Authorization": f"Bearer {expired_token}"},
        json={"event_type": "deploy_requested", "payload": {}},
    )
    scalar_payload = client.post(
        "/api/workflow-events",
        headers={"Authorization": f"Bearer {token}"},
        json={"event_type": "deploy_requested", "payload": "not-an-object"},
    )
    reserved_event = client.post(
        "/api/workflow-events",
        headers={"Authorization": f"Bearer {token}"},
        json={"event_type": "external.deploy_requested", "payload": {}},
    )

    assert missing_token.status_code == 401
    assert expired.status_code == 401
    assert scalar_payload.status_code == 400
    assert scalar_payload.json()["error"] == "payload must be a JSON object"
    assert reserved_event.status_code == 400
    assert reserved_event.json()["error"] == (
        "event_type must not include the reserved 'external.' prefix"
    )


def test_workflow_event_api_accepts_configured_machine_tokens_with_source_names(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv(
        "EVANDER_WORKFLOW_EVENT_MACHINE_TOKENS",
        "ado-pipeline:ado-secret, github-action:github-secret",
    )
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))

    invalid = client.post(
        "/api/workflow-events",
        headers={"Authorization": "Bearer wrong-secret"},
        json={"event_type": "app-demo-requested", "payload": {}},
    )
    ado_published = client.post(
        "/api/workflow-events",
        headers={"Authorization": "Bearer ado-secret"},
        json={
            "event_type": "app-demo-requested",
            "payload": {"resourceBaseName": "customer-1"},
            "correlation_key": "app-demo:customer-1",
            "idempotency_key": "app-demo-ai:customer-1:123",
        },
    )
    github_published = client.post(
        "/api/workflow-events",
        headers={"Authorization": "Bearer github-secret"},
        json={"event_type": "repo-updated", "payload": {}},
    )

    assert invalid.status_code == 401
    assert ado_published.status_code == 202
    assert ado_published.json()["event_type"] == "external.app-demo-requested"
    assert ado_published.json()["payload"] == {"resourceBaseName": "customer-1"}
    assert ado_published.json()["correlation_key"] == "app-demo:customer-1"
    assert ado_published.json()["source"] == {
        "source_type": "workflow_event_api_machine",
        "source_name": "ado-pipeline",
        "user_id": None,
        "user_login": None,
        "workflow_run_id": None,
        "workflow_event_id": None,
        "workflow_trace_id": None,
        "node_id": None,
        "node_type": None,
    }
    assert github_published.status_code == 202
    assert github_published.json()["source"]["source_name"] == "github-action"
    assert "ado-secret" not in json.dumps(ado_published.json())
    assert "github-secret" not in json.dumps(github_published.json())


def test_workflow_event_api_accepts_legacy_machine_tokens(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("EVANDER_WORKFLOW_EVENT_MACHINE_TOKENS", raising=False)
    monkeypatch.setenv(
        "AGENT_SANDBOX_WORKFLOW_EVENT_MACHINE_TOKENS",
        "legacy-publisher:legacy-secret",
    )
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))

    published = client.post(
        "/api/workflow-events",
        headers={"Authorization": "Bearer legacy-secret"},
        json={"event_type": "legacy-published", "payload": {}},
    )

    assert published.status_code == 202
    assert published.json()["source"]["source_name"] == "legacy-publisher"


@pytest.mark.parametrize(
    "configured_tokens, expected_error",
    [
        ("machine-secret", "source-name:token format"),
        ("source-only:", "both source name and token"),
        (":token-only", "both source name and token"),
        ("source:token:extra", "source-name:token format"),
        ("ado-pipeline:secret,github-action:secret", "duplicate token values"),
    ],
)
def test_admin_web_config_rejects_invalid_workflow_event_machine_token_entries(
    monkeypatch, configured_tokens: str, expected_error: str
) -> None:
    monkeypatch.setenv("EVANDER_WORKFLOW_EVENT_MACHINE_TOKENS", configured_tokens)

    with pytest.raises(ValueError, match=expected_error):
        AdminWebConfig.from_env()


def test_workflow_event_api_token_from_azure_session_publishes_user_metadata(
    tmp_path: Path, monkeypatch
) -> None:
    _set_azure_sso_env(monkeypatch)
    app = create_app(build_admin_runtime(write_config(tmp_path)))
    client = TestClient(app)

    callback = _complete_azure_login(app, client)
    minted = client.post("/api/workflow-events/token")
    token = minted.json()["access_token"]
    published = client.post(
        "/api/workflow-events",
        headers={"Authorization": f"Bearer {token}"},
        json={"event_type": "question_answered", "payload": {"answer": "yes"}},
    )

    assert callback.status_code == 303
    assert minted.status_code == 200
    assert published.status_code == 202
    assert published.json()["event_type"] == "external.question_answered"
    assert published.json()["source"]["user_id"] == "subject-1"
    assert published.json()["source"]["user_login"] == "operator@example.com"


def test_workflow_event_api_published_event_is_consumed_by_event_trigger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    service = build_admin_runtime(write_config(tmp_path))
    client = TestClient(create_app(service))
    wake_paths: list[Path] = []

    def _fake_notify(lock_path: Path) -> bool:
        wake_paths.append(wake_socket_path_for_lock(lock_path))
        return True

    monkeypatch.setattr(
        "evander.admin.services.workflow_config_service.notify_wake_socket_for_lock", _fake_notify
    )
    token = client.post("/api/workflow-events/token").json()["access_token"]
    publish_response = client.post(
        "/api/workflow-events",
        headers={"Authorization": f"Bearer {token}"},
        json={"event_type": "deploy_requested", "payload": {"target": "demo"}},
    )

    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": runtime_dir,
            "orchestration_store_backend": "sqlite",
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "external-deploy",
                    "display_name": "External deploy",
                    "type": "trigger:event",
                    "event_type": "external.deploy_requested",
                }
            ],
        }
    )
    worker = TriggerOrchestratorWorker(
        config=GatewayConfig(runtime_dir=runtime_dir),
        worker_config=worker_config,
        runtime=None,  # type: ignore[arg-type]
    )
    result = worker.run_once()
    pending = SqliteTriggerEventQueue(runtime_dir / "orchestration.db").list_pending(
        trigger_id="external-deploy"
    )

    assert publish_response.status_code == 202
    assert wake_paths == [wake_socket_path_for_lock(worker_config.lock_path)]
    assert result.events_published == 1
    assert len(pending) == 1
    assert pending[0].trigger_type == "trigger:event"
    assert pending[0].payload["event_id"] == publish_response.json()["event_id"]
    assert pending[0].payload["event_type"] == "external.deploy_requested"
    assert pending[0].payload["payload"] == {"target": "demo"}


def test_workflow_webhook_ingress_publishes_internal_event_and_bypasses_auth(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AUTH_MODE", "local_password")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_USERNAME", "operator")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_PASSWORD", "swordfish")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_SESSION_SECRET", "test-secret")
    monkeypatch.setenv("WEBHOOK_SECRET", "webhook-secret")
    _write_workflow_webhook_config(tmp_path)

    service = build_admin_runtime(write_config(tmp_path))
    client = TestClient(create_app(service))

    body = json.dumps(
        {"target": "demo", "options": {"dry_run": True}},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    headers = _signed_webhook_headers(
        body,
        secret="webhook-secret",
        query={"source": "ci"},
    )

    response = client.post(
        "/api/workflow-webhooks/build-notify?source=ci",
        content=body,
        headers=headers,
    )
    repeat = client.post(
        "/api/workflow-webhooks/build-notify?source=ci",
        content=body,
        headers=headers,
    )
    auth_required = client.post("/api/workflow-events/token")

    assert response.status_code == 202
    assert repeat.status_code == 202
    assert auth_required.status_code == 401
    assert response.json()["event_id"] == repeat.json()["event_id"]
    assert response.json()["event_type"] == "external.webhook.build-notify"
    assert response.json()["correlation_key"] == "webhook:build-notify"
    assert response.json()["source"] == {
        "source_type": "webhook",
        "source_name": "build-notify",
        "user_id": None,
        "user_login": None,
        "workflow_run_id": None,
        "workflow_event_id": None,
        "workflow_trace_id": None,
        "node_id": "webhook-1",
        "node_type": "trigger:http:webhook",
    }
    assert response.json()["payload"]["headers"]["content-type"] == "application/json"
    assert (
        response.json()["payload"]["headers"]["x-evander-signature-256"]
        == headers["X-Evander-Signature-256"]
    )
    assert response.json()["payload"]["slug"] == "build-notify"
    assert response.json()["payload"]["body"] == {"target": "demo", "options": {"dry_run": True}}
    assert response.json()["payload"]["query"] == {"source": "ci"}
    assert response.json()["payload"]["signature_header"] == headers["X-Evander-Signature-256"]
    assert response.json()["payload"]["timestamp_header"] == headers["X-Evander-Timestamp"]
    assert response.json()["payload"]["received_at"].endswith("Z")
    assert response.json()["payload"]["remote_addr"]

    events = InternalEventBus(tmp_path / "runtime" / "internal-events.json").list_events()
    assert len(events) == 1
    assert events[0].event_id == response.json()["event_id"]
    assert {event.event_type for event in events} == {"external.webhook.build-notify"}
    assert events[0].idempotency_key == _expected_webhook_idempotency_key(
        trigger_id="webhook-1",
        slug="build-notify",
        headers=headers,
        query={"source": "ci"},
    )


def test_workflow_webhook_ingress_keeps_distinct_query_envelopes(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("WEBHOOK_SECRET", "webhook-secret")
    _write_workflow_webhook_config(tmp_path)
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))

    body = b'{"target":"demo"}'
    headers = _signed_webhook_headers(body, secret="webhook-secret", query={"source": "ci"})
    manual_headers = _signed_webhook_headers(
        body,
        secret="webhook-secret",
        query={"source": "manual"},
    )
    first = client.post(
        "/api/workflow-webhooks/build-notify?source=ci",
        content=body,
        headers=headers,
    )
    repeat = client.post(
        "/api/workflow-webhooks/build-notify?source=ci",
        content=body,
        headers=headers,
    )
    second = client.post(
        "/api/workflow-webhooks/build-notify?source=manual",
        content=body,
        headers=manual_headers,
    )

    assert first.status_code == 202
    assert repeat.status_code == 202
    assert second.status_code == 202
    assert first.json()["event_id"] == repeat.json()["event_id"]
    assert first.json()["event_id"] != second.json()["event_id"]

    events = InternalEventBus(tmp_path / "runtime" / "internal-events.json").list_events()
    assert len(events) == 2
    assert [event.payload["query"] for event in events] == [
        {"source": "ci"},
        {"source": "manual"},
    ]
    assert {event.idempotency_key for event in events} == {
        _expected_webhook_idempotency_key(
            trigger_id="webhook-1",
            slug="build-notify",
            headers=headers,
            query={"source": "ci"},
        ),
        _expected_webhook_idempotency_key(
            trigger_id="webhook-1",
            slug="build-notify",
            headers=manual_headers,
            query={"source": "manual"},
        ),
    }


def test_workflow_webhook_ingress_rejects_query_mutation_replay(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("WEBHOOK_SECRET", "webhook-secret")
    _write_workflow_webhook_config(tmp_path)
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))

    body = b'{"target":"demo"}'
    headers = _signed_webhook_headers(body, secret="webhook-secret", query={"source": "ci"})
    accepted = client.post(
        "/api/workflow-webhooks/build-notify?source=ci",
        content=body,
        headers=headers,
    )
    replay = client.post(
        "/api/workflow-webhooks/build-notify?source=manual",
        content=body,
        headers=headers,
    )

    assert accepted.status_code == 202
    assert replay.status_code == 401
    assert replay.json()["error"] == "Webhook signature is invalid or missing"

    events = InternalEventBus(tmp_path / "runtime" / "internal-events.json").list_events()
    assert len(events) == 1
    assert events[0].payload["query"] == {"source": "ci"}


def test_workflow_webhook_ingress_does_not_dedupe_same_body_across_slugs(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("WEBHOOK_SECRET", "webhook-secret")
    config_path = tmp_path / "config" / "trigger-orchestrator" / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(
            {
                "runtime_dir": str(tmp_path / "runtime"),
                "poll_interval_seconds": 1,
                "jitter_seconds": 0,
                "triggers": [
                    {
                        "id": "webhook-build",
                        "display_name": "Build webhook",
                        "type": "trigger:http:webhook",
                        "slug": "build-notify",
                        "secret_env": "WEBHOOK_SECRET",
                    },
                    {
                        "id": "webhook-deploy",
                        "display_name": "Deploy webhook",
                        "type": "trigger:http:webhook",
                        "slug": "deploy-notify",
                        "secret_env": "WEBHOOK_SECRET",
                    },
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))

    body = b'{"target":"demo"}'
    headers = _signed_webhook_headers(body, secret="webhook-secret")
    build_response = client.post(
        "/api/workflow-webhooks/build-notify",
        content=body,
        headers=headers,
    )
    deploy_response = client.post(
        "/api/workflow-webhooks/deploy-notify",
        content=body,
        headers=headers,
    )

    assert build_response.status_code == 202
    assert deploy_response.status_code == 202
    assert build_response.json()["event_id"] != deploy_response.json()["event_id"]

    events = InternalEventBus(tmp_path / "runtime" / "internal-events.json").list_events()
    assert len(events) == 2
    assert {event.event_id for event in events} == {
        build_response.json()["event_id"],
        deploy_response.json()["event_id"],
    }
    assert {event.event_type for event in events} == {
        "external.webhook.build-notify",
        "external.webhook.deploy-notify",
    }
    assert {event.source.source_name for event in events if event.source is not None} == {
        "build-notify",
        "deploy-notify",
    }
    assert {event.idempotency_key for event in events} == {
        _expected_webhook_idempotency_key(
            trigger_id="webhook-build",
            slug="build-notify",
            headers=headers,
            query={},
        ),
        _expected_webhook_idempotency_key(
            trigger_id="webhook-deploy",
            slug="deploy-notify",
            headers=headers,
            query={},
        ),
    }


@pytest.mark.parametrize(
    "header_value, expected_error",
    [
        (None, "Webhook signature is invalid or missing"),
        ("invalid", "Webhook signature is invalid or missing"),
    ],
)
def test_workflow_webhook_ingress_rejects_missing_or_malformed_signature(
    tmp_path: Path, header_value: str | None, expected_error: str, monkeypatch
) -> None:
    monkeypatch.setenv("WEBHOOK_SECRET", "webhook-secret")
    _write_workflow_webhook_config(tmp_path)
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))

    body = b'{"target":"demo"}'
    headers = {"Content-Type": "application/json"}
    if header_value is not None:
        headers["X-Evander-Signature-256"] = header_value

    response = client.post("/api/workflow-webhooks/build-notify", content=body, headers=headers)

    assert response.status_code == 401
    assert response.json()["error"] == expected_error


def test_workflow_webhook_ingress_rejects_bad_hmac(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("WEBHOOK_SECRET", "webhook-secret")
    _write_workflow_webhook_config(tmp_path)
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))

    body = b'{"target":"demo"}'
    response = client.post(
        "/api/workflow-webhooks/build-notify",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Evander-Timestamp": str(int(time.time())),
            "X-Evander-Signature-256": "sha256=" + ("0" * 64),
        },
    )

    assert response.status_code == 401
    assert response.json()["error"] == "Webhook signature is invalid or missing"


def test_workflow_webhook_ingress_rejects_missing_or_stale_timestamp(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("WEBHOOK_SECRET", "webhook-secret")
    _write_workflow_webhook_config(tmp_path)
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))

    body = b'{"target":"demo"}'
    missing_timestamp = client.post(
        "/api/workflow-webhooks/build-notify",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Evander-Signature-256": "sha256=" + ("0" * 64),
        },
    )
    stale_timestamp = client.post(
        "/api/workflow-webhooks/build-notify",
        content=body,
        headers=_signed_webhook_headers(
            body,
            secret="webhook-secret",
            timestamp=int(time.time()) - 600,
        ),
    )

    assert missing_timestamp.status_code == 401
    assert missing_timestamp.json()["error"] == "Webhook timestamp is invalid or stale"
    assert stale_timestamp.status_code == 401
    assert stale_timestamp.json()["error"] == "Webhook timestamp is invalid or stale"
    events = InternalEventBus(tmp_path / "runtime" / "internal-events.json").list_events()
    assert events == []


def test_workflow_webhook_ingress_rejects_invalid_json_and_non_object_body(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("WEBHOOK_SECRET", "webhook-secret")
    _write_workflow_webhook_config(tmp_path)
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))

    invalid_json = client.post(
        "/api/workflow-webhooks/build-notify",
        content=b"not-json",
        headers={
            "Content-Type": "application/json",
            "X-Evander-Signature-256": "sha256=" + ("1" * 64),
        },
    )
    non_object_json = client.post(
        "/api/workflow-webhooks/build-notify",
        content=b"[1,2,3]",
        headers={
            "Content-Type": "application/json",
            "X-Evander-Signature-256": "sha256=" + ("1" * 64),
        },
    )

    assert invalid_json.status_code == 400
    assert invalid_json.json()["error"] == "Request body must be valid JSON"
    assert non_object_json.status_code == 400
    assert non_object_json.json()["error"] == "Request body must be a JSON object"


def test_workflow_webhook_ingress_rejects_missing_secret_and_unknown_slug(
    tmp_path: Path, monkeypatch
) -> None:
    _write_workflow_webhook_config(tmp_path, secret_env="MISSING_WEBHOOK_SECRET")
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))

    body = b'{"target":"demo"}'
    response_missing_secret = client.post(
        "/api/workflow-webhooks/build-notify",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Evander-Signature-256": "sha256=" + ("1" * 64),
        },
    )

    _write_workflow_webhook_config(tmp_path, slug="other-slug", secret_env="WEBHOOK_SECRET")
    monkeypatch.setenv("WEBHOOK_SECRET", "webhook-secret")
    response_unknown_slug = client.post(
        "/api/workflow-webhooks/build-notify",
        content=body,
        headers=_signed_webhook_headers(body, secret="webhook-secret"),
    )

    assert response_missing_secret.status_code == 500
    assert "MISSING_WEBHOOK_SECRET" in response_missing_secret.json()["error"]
    assert response_unknown_slug.status_code == 404
    assert (
        response_unknown_slug.json()["error"] == "Webhook trigger not found for slug: build-notify"
    )


def test_workflow_webhook_ingress_rejects_duplicate_active_slug(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("WEBHOOK_SECRET", "webhook-secret")
    _write_workflow_webhook_config(tmp_path, duplicate=True)
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))

    body = b'{"target":"demo"}'
    response = client.post(
        "/api/workflow-webhooks/build-notify",
        content=body,
        headers=_signed_webhook_headers(body, secret="webhook-secret"),
    )

    assert response.status_code == 409
    assert (
        "Webhook trigger slugs must be globally unique across active config"
        in response.json()["error"]
    )


def test_admin_web_config_defaults_to_none_without_password(monkeypatch) -> None:
    monkeypatch.delenv("EVANDER_ADMIN_WEB_AUTH_MODE", raising=False)
    monkeypatch.delenv("EVANDER_ADMIN_WEB_PASSWORD", raising=False)

    config = AdminWebConfig.from_env()

    assert config.auth_mode == "none"
    assert config.auth_required is False


def test_admin_web_config_maps_password_only_deployments_to_local_password(monkeypatch) -> None:
    monkeypatch.delenv("EVANDER_ADMIN_WEB_AUTH_MODE", raising=False)
    monkeypatch.setenv("EVANDER_ADMIN_WEB_PASSWORD", "swordfish")

    config = AdminWebConfig.from_env()

    assert config.auth_mode == "local_password"
    assert config.auth_required is True


def test_admin_web_config_preserves_legacy_local_password_env(monkeypatch) -> None:
    monkeypatch.delenv("EVANDER_ADMIN_WEB_AUTH_MODE", raising=False)
    monkeypatch.delenv("EVANDER_ADMIN_WEB_USERNAME", raising=False)
    monkeypatch.delenv("EVANDER_ADMIN_WEB_PASSWORD", raising=False)
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AUTH_MODE", "local_password")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_USERNAME", "legacy-operator")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_PASSWORD", "legacy-secret")

    config = AdminWebConfig.from_env()

    assert config.auth_mode == "local_password"
    assert config.username == "legacy-operator"
    assert config.password == "legacy-secret"
    assert config.auth_required is True


def test_admin_web_config_preserves_legacy_session_settings(monkeypatch) -> None:
    for name in (
        "EVANDER_ADMIN_WEB_SESSION_SECRET",
        "EVANDER_ADMIN_WEB_SESSION_TTL_SECONDS",
        "EVANDER_ADMIN_WEB_COOKIE_SECURE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_SESSION_SECRET", "legacy-session-secret")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_SESSION_TTL_SECONDS", "7200")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_COOKIE_SECURE", "true")

    config = AdminWebConfig.from_env()

    assert config.session_secret == "legacy-session-secret"
    assert config.session_ttl_seconds == 7200
    assert config.cookie_secure is True


def test_admin_web_config_preserves_legacy_azure_sso_env(monkeypatch) -> None:
    for name in (
        "EVANDER_ADMIN_WEB_AUTH_MODE",
        "EVANDER_ADMIN_WEB_AZURE_TENANT_ID",
        "EVANDER_ADMIN_WEB_AZURE_CLIENT_ID",
        "EVANDER_ADMIN_WEB_AZURE_CLIENT_SECRET",
        "EVANDER_ADMIN_WEB_AZURE_AUTHORITY_BASE_URL",
        "EVANDER_ADMIN_WEB_PUBLIC_BASE_URL",
        "EVANDER_ADMIN_WEB_AZURE_REDIRECT_URI",
        "EVANDER_ADMIN_WEB_AZURE_POST_LOGIN_REDIRECT",
        "EVANDER_ADMIN_WEB_AZURE_ALLOWED_USERS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AUTH_MODE", "azure_sso")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AZURE_TENANT_ID", "legacy-tenant")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AZURE_CLIENT_ID", "legacy-client")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AZURE_CLIENT_SECRET", "legacy-secret")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_PUBLIC_BASE_URL", "https://admin.example.test")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AZURE_POST_LOGIN_REDIRECT", "/workflow")
    monkeypatch.setenv(
        "AGENT_SANDBOX_ADMIN_WEB_AZURE_ALLOWED_USERS",
        "Operator@Example.Test,viewer@example.test",
    )

    config = AdminWebConfig.from_env()

    assert config.auth_mode == "azure_sso"
    assert config.azure_sso is not None
    assert config.azure_sso.tenant_id == "legacy-tenant"
    assert config.azure_sso.client_id == "legacy-client"
    assert config.azure_sso.client_secret == "legacy-secret"
    assert config.azure_sso.redirect_uri == (
        "https://admin.example.test/api/auth/azure-sso/callback"
    )
    assert config.azure_sso.post_login_redirect == "/workflow"
    assert config.azure_sso.bootstrap_users == frozenset(
        {"operator@example.test", "viewer@example.test"}
    )


def test_admin_web_config_rejects_incomplete_azure_sso(monkeypatch) -> None:
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AUTH_MODE", "azure_sso")
    monkeypatch.delenv("EVANDER_ADMIN_WEB_AZURE_TENANT_ID", raising=False)
    monkeypatch.delenv("EVANDER_ADMIN_WEB_AZURE_CLIENT_ID", raising=False)
    monkeypatch.delenv("EVANDER_ADMIN_WEB_AZURE_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("EVANDER_ADMIN_WEB_AZURE_REDIRECT_URI", raising=False)
    monkeypatch.delenv("EVANDER_ADMIN_WEB_PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("EVANDER_ADMIN_WEB_AZURE_ALLOWED_USERS", raising=False)

    with pytest.raises(ValueError, match="Azure SSO admin auth is incomplete"):
        AdminWebConfig.from_env()


def test_admin_web_config_treats_azure_allowed_users_as_bootstrap_input(monkeypatch) -> None:
    _set_azure_sso_env(monkeypatch, allowed_users=None)

    config = AdminWebConfig.from_env()

    assert config.auth_mode == "azure_sso"
    assert config.azure_sso is not None
    assert config.azure_sso.bootstrap_users == frozenset()


def test_admin_web_session_payload_reports_local_password_mode(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EVANDER_ADMIN_WEB_PASSWORD", "swordfish")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_SESSION_SECRET", "test-secret")

    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))
    response = client.get("/api/auth/session")

    assert response.status_code == 200
    assert response.json()["auth_mode"] == "local_password"
    assert response.json()["authenticated"] is False
    assert response.json()["role"] is None
    assert response.json()["can_manage_users"] is False
    assert response.json()["permissions"] == []
    assert response.json()["effective_permissions"] == []
    assert response.json()["available_login_methods"] == ["password"]


def test_admin_web_session_payload_reports_azure_sso_mode(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AUTH_MODE", "azure_sso")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_SESSION_SECRET", "test-secret")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AZURE_TENANT_ID", "tenant-123")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AZURE_CLIENT_ID", "client-123")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AZURE_CLIENT_SECRET", "secret-123")
    monkeypatch.setenv(
        "EVANDER_ADMIN_WEB_AZURE_REDIRECT_URI",
        "https://admin.example.test/api/auth/azure-sso/callback",
    )
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AZURE_ALLOWED_USERS", "operator@example.com")

    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))
    response = client.get("/api/auth/session")

    assert response.status_code == 200
    assert response.json()["auth_mode"] == "azure_sso"
    assert response.json()["authenticated"] is False
    assert response.json()["role"] is None
    assert response.json()["can_manage_users"] is False
    assert response.json()["permissions"] == []
    assert response.json()["effective_permissions"] == []
    assert response.json()["available_login_methods"] == ["azure_sso"]
    assert response.json()["azure_sso_login_url"] == "/api/auth/azure-sso/start"


def test_admin_web_bootstraps_azure_users_from_env_on_first_startup(
    tmp_path: Path, monkeypatch
) -> None:
    _set_azure_sso_env(
        monkeypatch,
        allowed_users="Operator@Example.com,second@example.com, operator@example.com",
    )

    app = create_app(build_admin_runtime(write_config(tmp_path)))

    users = app.state.admin_user_store.list_users()
    assert [user.login for user in users] == ["operator@example.com", "second@example.com"]
    assert {user.role for user in users} == {"admin"}


def test_admin_web_does_not_rebootstrap_nonempty_azure_user_store(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = write_config(tmp_path)
    store = _admin_web_user_store(tmp_path)
    store.upsert_user(login="existing@example.com", role="admin")
    _set_azure_sso_env(monkeypatch, allowed_users="new@example.com")

    app = create_app(build_admin_runtime(config_path))

    assert [user.login for user in app.state.admin_user_store.list_users()] == [
        "existing@example.com"
    ]


def test_admin_web_azure_login_uses_runtime_user_store_without_env_allowlist(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = write_config(tmp_path)
    _admin_web_user_store(tmp_path).upsert_user(
        login="operator@example.com", role="admin", display_name="Runtime Operator"
    )
    _set_azure_sso_env(monkeypatch, allowed_users=None)
    app = create_app(build_admin_runtime(config_path))
    client = TestClient(app)

    callback = _complete_azure_login(app, client)
    session = client.get("/api/auth/session")
    admin_state = client.get("/api/admin/state")

    assert callback.status_code == 303
    assert session.json()["authenticated"] is True
    assert session.json()["role"] == "admin"
    assert session.json()["can_manage_users"] is True
    assert ADMIN_OWNER_PERMISSION in session.json()["permissions"]
    assert session.json()["permissions"] == session.json()["effective_permissions"]
    assert session.json()["display_name"] == "Runtime Operator"
    assert admin_state.json()["role"] == "admin"
    assert admin_state.json()["can_manage_users"] is True
    assert ADMIN_OWNER_PERMISSION in admin_state.json()["permissions"]
    assert admin_state.json()["permissions"] == admin_state.json()["effective_permissions"]
    assert app.state.admin_user_store.get_user("operator@example.com").last_login_at is not None


def test_admin_web_azure_login_uses_effective_permissions_for_regular_user(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = write_config(tmp_path)
    store = _admin_web_user_store(tmp_path)
    store.upsert_user(login="admin@example.com", role="admin")
    store.upsert_user(login="regular@example.com", role="regular")
    _set_azure_sso_env(monkeypatch, allowed_users=None)
    app = create_app(build_admin_runtime(config_path))
    client = TestClient(app)

    callback = _complete_azure_login(
        app, client, login="regular@example.com", display_name="Regular User"
    )
    session = client.get("/api/auth/session")
    admin_state = client.get("/api/admin/state")

    assert callback.status_code == 303
    assert session.status_code == 200
    assert session.json()["authenticated"] is True
    assert session.json()["role"] == "regular"
    assert session.json()["can_manage_users"] is False
    assert set(session.json()["permissions"]) == {
        WORKFLOWS_READ_PERMISSION,
        WORKFLOWS_WRITE_PERMISSION,
        WORKFLOW_PACKS_MANAGE_PERMISSION,
        WORKFLOW_RUNS_READ_PERMISSION,
        WORKFLOW_RUNS_CONTROL_PERMISSION,
        TEAM_CHATS_READ_ALL_PERMISSION,
    }
    assert session.json()["permissions"] == session.json()["effective_permissions"]
    assert admin_state.status_code == 200
    assert admin_state.json()["role"] == "regular"
    assert admin_state.json()["can_manage_users"] is False
    assert set(admin_state.json()["permissions"]) == {
        WORKFLOWS_READ_PERMISSION,
        WORKFLOWS_WRITE_PERMISSION,
        WORKFLOW_PACKS_MANAGE_PERMISSION,
        WORKFLOW_RUNS_READ_PERMISSION,
        WORKFLOW_RUNS_CONTROL_PERMISSION,
        TEAM_CHATS_READ_ALL_PERMISSION,
    }


def test_admin_web_state_exposes_effective_execution_backend(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    client = TestClient(create_app(service))
    _unlock_privileged_operations(client)

    root_state = client.get("/api/admin/state")
    create_directory = client.post(
        "/api/configuration/directories",
        json={"parent_path": "root", "name": "ops"},
    )
    write_backend = client.put(
        "/api/configuration/domains/providers?directory_path=root/ops",
        json={
            "config": {"execution_backend": "container_worker_vm"},
            "expected_revision": _configuration_expected_revision(client, "root/ops", "providers"),
        },
    )
    ops_state = client.get("/api/admin/state?directory_path=root/ops")
    ops_configuration = client.get("/api/configuration/state?directory_path=root/ops")

    assert root_state.status_code == 200
    assert root_state.json()["execution_backend"] == "libvirt_vm"
    assert create_directory.status_code == 200
    assert write_backend.status_code == 200
    assert ops_state.status_code == 200
    assert ops_state.json()["selected_directory_path"] == "root/ops"
    assert ops_state.json()["execution_backend"] == "container_worker_vm"
    assert ops_configuration.status_code == 200
    assert ops_configuration.json()["execution_backend"] == "container_worker_vm"


def test_admin_web_state_uses_root_execution_backend(tmp_path: Path) -> None:
    client = TestClient(
        create_app(
            build_admin_runtime(write_config(tmp_path, execution_backend="container_worker_vm"))
        )
    )

    response = client.get("/api/admin/state")

    assert response.status_code == 200
    assert response.json()["execution_backend"] == "container_worker_vm"


def test_admin_web_azure_mode_requires_login(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AUTH_MODE", "azure_sso")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_SESSION_SECRET", "test-secret")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AZURE_TENANT_ID", "tenant-123")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AZURE_CLIENT_ID", "client-123")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AZURE_CLIENT_SECRET", "secret-123")
    monkeypatch.setenv(
        "EVANDER_ADMIN_WEB_AZURE_REDIRECT_URI",
        "https://admin.example.test/api/auth/azure-sso/callback",
    )
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AZURE_ALLOWED_USERS", "operator@example.com")

    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))

    response = client.get("/api/workflow-editor/document")

    assert response.status_code == 401


def test_admin_web_azure_start_login_redirects_and_sets_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AUTH_MODE", "azure_sso")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_SESSION_SECRET", "test-secret")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AZURE_TENANT_ID", "tenant-123")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AZURE_CLIENT_ID", "client-123")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AZURE_CLIENT_SECRET", "secret-123")
    monkeypatch.setenv(
        "EVANDER_ADMIN_WEB_AZURE_REDIRECT_URI",
        "https://admin.example.test/api/auth/azure-sso/callback",
    )
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AZURE_ALLOWED_USERS", "operator@example.com")

    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))

    response = client.get("/api/auth/azure-sso/start?next=%2Fworkflow", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"].startswith(
        "https://login.microsoftonline.com/tenant-123/oauth2/v2.0/authorize?"
    )
    assert "evander_admin_oidc=" in response.headers.get("set-cookie", "")


def test_admin_web_azure_callback_rejects_bad_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AUTH_MODE", "azure_sso")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_SESSION_SECRET", "test-secret")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AZURE_TENANT_ID", "tenant-123")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AZURE_CLIENT_ID", "client-123")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AZURE_CLIENT_SECRET", "secret-123")
    monkeypatch.setenv(
        "EVANDER_ADMIN_WEB_AZURE_REDIRECT_URI",
        "https://admin.example.test/api/auth/azure-sso/callback",
    )
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AZURE_ALLOWED_USERS", "operator@example.com")
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))

    start = client.get("/api/auth/azure-sso/start", follow_redirects=False)
    assert start.status_code == 307

    response = client.get(
        "/api/auth/azure-sso/callback?code=test-code&state=wrong-state", follow_redirects=False
    )

    assert response.status_code == 401
    assert response.json()["error"] == "Invalid Azure sign-in state"


def test_admin_web_azure_callback_rejects_wrong_tenant(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AUTH_MODE", "azure_sso")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_SESSION_SECRET", "test-secret")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AZURE_TENANT_ID", "tenant-123")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AZURE_CLIENT_ID", "client-123")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AZURE_CLIENT_SECRET", "secret-123")
    monkeypatch.setenv(
        "EVANDER_ADMIN_WEB_AZURE_REDIRECT_URI",
        "https://admin.example.test/api/auth/azure-sso/callback",
    )
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AZURE_ALLOWED_USERS", "operator@example.com")
    app = create_app(build_admin_runtime(write_config(tmp_path)))
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
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AUTH_MODE", "azure_sso")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_SESSION_SECRET", "test-secret")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AZURE_TENANT_ID", "tenant-123")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AZURE_CLIENT_ID", "client-123")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AZURE_CLIENT_SECRET", "secret-123")
    monkeypatch.setenv(
        "EVANDER_ADMIN_WEB_AZURE_REDIRECT_URI",
        "https://admin.example.test/api/auth/azure-sso/callback",
    )
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AZURE_ALLOWED_USERS", "operator@example.com")
    app = create_app(build_admin_runtime(write_config(tmp_path)))
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
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AUTH_MODE", "azure_sso")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_SESSION_SECRET", "test-secret")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AZURE_TENANT_ID", "tenant-123")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AZURE_CLIENT_ID", "client-123")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AZURE_CLIENT_SECRET", "secret-123")
    monkeypatch.setenv(
        "EVANDER_ADMIN_WEB_AZURE_REDIRECT_URI",
        "https://admin.example.test/api/auth/azure-sso/callback",
    )
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AZURE_ALLOWED_USERS", "operator@example.com")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AZURE_POST_LOGIN_REDIRECT", "/#workflow")
    app = create_app(build_admin_runtime(write_config(tmp_path)))
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
    assert "evander_admin_session=" in callback.headers.get("set-cookie", "")
    assert session.status_code == 200
    assert session.json()["authenticated"] is True
    assert session.json()["auth_mode"] == "azure_sso"
    assert session.json()["username"] == "operator@example.com"
    assert session.json()["display_name"] == "Operator"
    assert session.json()["role"] == "admin"
    assert session.json()["can_manage_users"] is True
    assert ADMIN_OWNER_PERMISSION in session.json()["permissions"]
    assert session.json()["permissions"] == session.json()["effective_permissions"]


def test_admin_web_logout_clears_azure_local_session_cookie(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AUTH_MODE", "azure_sso")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_SESSION_SECRET", "test-secret")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AZURE_TENANT_ID", "tenant-123")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AZURE_CLIENT_ID", "client-123")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AZURE_CLIENT_SECRET", "secret-123")
    monkeypatch.setenv(
        "EVANDER_ADMIN_WEB_AZURE_REDIRECT_URI",
        "https://admin.example.test/api/auth/azure-sso/callback",
    )
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AZURE_ALLOWED_USERS", "operator@example.com")
    app = create_app(build_admin_runtime(write_config(tmp_path)))
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


def test_admin_web_removed_azure_user_loses_existing_session(tmp_path: Path, monkeypatch) -> None:
    _set_azure_sso_env(monkeypatch, allowed_users="operator@example.com,backup@example.com")
    app = create_app(build_admin_runtime(write_config(tmp_path)))
    client = TestClient(app)

    callback = _complete_azure_login(app, client, login="operator@example.com")
    before_removal = client.get("/api/workflow-editor/document")
    app.state.admin_user_store.delete_user("operator@example.com")
    session = client.get("/api/auth/session")
    after_removal = client.get("/api/workflow-editor/document")

    assert callback.status_code == 303
    assert before_removal.status_code == 200
    assert session.status_code == 200
    assert session.json()["authenticated"] is False
    assert session.json()["role"] is None
    assert session.json()["permissions"] == []
    assert session.json()["effective_permissions"] == []
    assert after_removal.status_code == 401


def test_admin_web_user_api_manages_users_as_builtin_admin(tmp_path: Path) -> None:
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))

    created = client.post(
        "/api/admin/users",
        json={"login": "Operator@Example.com", "role": "admin", "display_name": "Operator"},
    )
    updated = client.patch(
        "/api/admin/users/operator@example.com",
        json={"role": "regular", "display_name": "Ops User"},
    )
    listed = client.get("/api/admin/users")

    assert created.status_code == 200
    assert created.json()["user"]["login"] == "operator@example.com"
    assert created.json()["user"]["role"] == "admin"
    assert updated.status_code == 400
    assert updated.json()["error"] == "Cannot demote the last admin user"
    assert listed.status_code == 200
    assert listed.json()["users"] == [created.json()["user"]]


def test_admin_web_user_api_prevents_last_admin_delete_and_demotion(tmp_path: Path) -> None:
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))
    client.post("/api/admin/users", json={"login": "admin@example.com", "role": "admin"})

    demote = client.patch("/api/admin/users/admin@example.com", json={"role": "regular"})
    delete = client.delete("/api/admin/users/admin@example.com")
    create_second = client.post(
        "/api/admin/users", json={"login": "regular@example.com", "role": "regular"}
    )
    delete_second = client.delete("/api/admin/users/regular@example.com")

    assert demote.status_code == 400
    assert demote.json()["error"] == "Cannot demote the last admin user"
    assert delete.status_code == 400
    assert delete.json()["error"] == "Cannot remove the last admin user"
    assert create_second.status_code == 200
    assert create_second.json()["user"]["role"] == "regular"
    assert delete_second.status_code == 200


def test_admin_web_user_api_attaches_directory_contact(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    service.operations_admin.write_directory_domain_config(
        "root",
        "contacts",
        {
            "contacts": {
                "ops": {
                    "name": "Ops Duty",
                    "description": "Deployment window escalation contact",
                    "phone_numbers": [
                        {"label": "mobile", "number": "+15550000002", "primary": True}
                    ],
                }
            }
        },
    )
    client = TestClient(create_app(service))

    created = client.post(
        "/api/admin/users?directory_path=root",
        json={"login": "operator@example.com", "role": "admin", "contact_id": "ops"},
    )
    listed = client.get("/api/admin/users?directory_path=root")
    cleared = client.patch(
        "/api/admin/users/operator@example.com?directory_path=root",
        json={"contact_id": None},
    )

    assert created.status_code == 200
    assert created.json()["user"]["contact_id"] == "ops"
    assert created.json()["user"]["contact"]["name"] == "Ops Duty"
    assert listed.json()["contacts"][0]["id"] == "ops"
    assert listed.json()["users"][0]["contact_id"] == "ops"
    assert cleared.status_code == 200
    assert cleared.json()["user"]["contact_id"] is None


def test_admin_web_user_api_clears_inherited_directory_contact(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    service.directory_admin.create_child(parent="root", name="team")
    service.operations_admin.write_directory_domain_config(
        "root",
        "contacts",
        {
            "contacts": {
                "ops": {
                    "name": "Ops Duty",
                    "phone_numbers": [
                        {"label": "mobile", "number": "+15550000002", "primary": True}
                    ],
                }
            },
            "user_contact_links": {"operator@example.com": "ops"},
        },
    )
    client = TestClient(create_app(service))
    client.post(
        "/api/admin/users?directory_path=root",
        json={"login": "operator@example.com", "role": "admin"},
    )

    inherited = client.get("/api/admin/users?directory_path=root/team")
    cleared = client.patch(
        "/api/admin/users/operator@example.com?directory_path=root/team",
        json={"contact_id": None},
    )
    listed = client.get("/api/admin/users?directory_path=root/team")

    assert inherited.json()["users"][0]["contact_id"] == "ops"
    assert cleared.status_code == 200
    assert cleared.json()["user"]["contact_id"] is None
    assert listed.json()["users"][0]["contact_id"] is None


def test_admin_web_user_api_manages_groups_assignments_and_direct_workflow_grants(
    tmp_path: Path,
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    service.directory_admin.create_child(parent="root", name="ops")
    client = TestClient(create_app(service))

    owner = client.post(
        "/api/admin/users",
        json={"login": "owner@example.com", "role": "admin", "display_name": "Owner"},
    )
    group = client.post(
        "/api/admin/groups",
        json={
            "group_id": "ops-reviewers",
            "display_name": "Ops Reviewers",
            "description": "Review workflow changes for ops.",
            "permissions": [WORKFLOWS_READ_PERMISSION, WORKFLOW_RUNS_READ_PERMISSION],
        },
    )
    member = client.post(
        "/api/admin/users?directory_path=root/ops",
        json={
            "login": "member@example.com",
            "role": "regular",
            "display_name": "Member",
            "group_assignments": [{"group_id": "ops-reviewers", "directory_path": "root/ops"}],
            "workflow_grants": [
                {"permission": WORKFLOWS_WRITE_PERMISSION, "directory_path": "root/ops"}
            ],
        },
    )
    listed = client.get("/api/admin/users?directory_path=root/ops")
    updated_group = client.patch(
        "/api/admin/groups/ops-reviewers",
        json={
            "display_name": "Ops Workflow Reviewers",
            "permissions": [WORKFLOWS_READ_PERMISSION],
        },
    )

    assert owner.status_code == 200
    assert group.status_code == 200
    assert set(group.json()["group"]["permissions"]) == {
        WORKFLOWS_READ_PERMISSION,
        WORKFLOW_RUNS_READ_PERMISSION,
    }
    assert member.status_code == 200
    member_payload = member.json()["user"]
    assert member_payload["group_assignments"] == [
        {
            "login": "member@example.com",
            "group_id": "ops-reviewers",
            "directory_path": "root/ops",
            "created_at": member_payload["group_assignments"][0]["created_at"],
        }
    ]
    assert member_payload["workflow_grants"] == [
        {
            "login": "member@example.com",
            "permission": WORKFLOWS_WRITE_PERMISSION,
            "directory_path": "root/ops",
            "created_at": member_payload["workflow_grants"][0]["created_at"],
        }
    ]
    assert WORKFLOWS_WRITE_PERMISSION in member_payload["effective_permissions"]
    assert listed.status_code == 200
    assert "ops-reviewers" in {group["group_id"] for group in listed.json()["groups"]}
    assert WORKFLOWS_WRITE_PERMISSION in listed.json()["permission_catalog"]
    assert updated_group.status_code == 200
    assert updated_group.json()["group"]["display_name"] == "Ops Workflow Reviewers"
    assert updated_group.json()["group"]["permissions"] == [WORKFLOWS_READ_PERMISSION]


def test_admin_web_group_manager_cannot_mutate_users_or_assignments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = write_config(tmp_path)
    store = _admin_web_user_store(tmp_path)
    store.upsert_user(login="owner@example.com", role="admin")
    store.upsert_user(login="group-manager@example.com", role="regular")
    store.upsert_group(
        group_id="group-managers",
        display_name="Group Managers",
        description="Can edit groups only.",
    )
    store.set_group_permissions("group-managers", [GROUPS_MANAGE_PERMISSION])
    store.set_user_group_assignments(
        login="group-manager@example.com",
        assignments=[{"group_id": "group-managers", "directory_path": "root"}],
    )
    _set_azure_sso_env(monkeypatch, allowed_users=None)
    app = create_app(build_admin_runtime(config_path))
    client = TestClient(app)

    callback = _complete_azure_login(
        app,
        client,
        login="group-manager@example.com",
        display_name="Group Manager",
    )
    list_users = client.get("/api/admin/users")
    create_user = client.post(
        "/api/admin/users",
        json={
            "login": "other@example.com",
            "role": "regular",
            "group_assignments": [{"group_id": "owner", "directory_path": "root"}],
        },
    )
    list_groups = client.get("/api/admin/groups")
    create_group = client.post(
        "/api/admin/groups",
        json={
            "group_id": "ops-readers",
            "display_name": "Ops Readers",
            "permissions": [WORKFLOWS_READ_PERMISSION],
        },
    )

    assert callback.status_code == 303
    assert list_users.status_code == 403
    assert create_user.status_code == 403
    assert create_user.json()["error"] == "Permission required: users.manage"
    assert list_groups.status_code == 200
    assert create_group.status_code == 200
    assert create_group.json()["group"]["group_id"] == "ops-readers"


def test_admin_web_user_api_rejects_regular_azure_user(tmp_path: Path, monkeypatch) -> None:
    config_path = write_config(tmp_path)
    store = _admin_web_user_store(tmp_path)
    store.upsert_user(login="admin@example.com", role="admin")
    store.upsert_user(login="regular@example.com", role="regular")
    _set_azure_sso_env(monkeypatch, allowed_users=None)
    app = create_app(build_admin_runtime(config_path))
    client = TestClient(app)

    callback = _complete_azure_login(
        app, client, login="regular@example.com", display_name="Regular User"
    )
    allowed_api = client.get("/api/workflow-editor/document")
    list_users = client.get("/api/admin/users")
    create_user = client.post(
        "/api/admin/users", json={"login": "other@example.com", "role": "regular"}
    )

    assert callback.status_code == 303
    assert allowed_api.status_code == 200
    assert list_users.status_code == 403
    assert create_user.status_code == 403


def test_admin_web_workflow_tutorial_preference_defaults_and_persists(
    tmp_path: Path,
) -> None:
    app = create_app(build_admin_runtime(write_config(tmp_path)))
    client = TestClient(app)

    initial = client.get("/api/admin/user-preferences/workflow-tutorial-progress")
    saved = client.put(
        "/api/admin/user-preferences/workflow-tutorial-progress",
        json={
            "completed_tutorial_ids": ["unknown", "first-steps", "first-steps"],
            "skipped_tutorial_ids": ["first-steps", "unknown"],
        },
    )
    loaded = client.get("/api/admin/user-preferences/workflow-tutorial-progress")

    assert initial.status_code == 200
    assert initial.json() == {"completed_tutorial_ids": [], "skipped_tutorial_ids": []}
    assert saved.status_code == 200
    assert saved.json() == {
        "completed_tutorial_ids": ["first-steps"],
        "skipped_tutorial_ids": [],
    }
    assert loaded.json() == saved.json()
    assert (
        app.state.admin_user_store.get_user_preference("admin", "workflow_tutorial_progress")
        == saved.json()
    )


def test_admin_web_workflow_tutorial_preference_uses_local_password_identity(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("EVANDER_ADMIN_WEB_PASSWORD", "swordfish")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_SESSION_SECRET", "test-secret")
    app = create_app(build_admin_runtime(write_config(tmp_path)))
    client = TestClient(app)

    unauthenticated = client.get("/api/admin/user-preferences/workflow-tutorial-progress")
    login = client.post("/api/auth/login", json={"username": "admin", "password": "swordfish"})
    saved = client.put(
        "/api/admin/user-preferences/workflow-tutorial-progress",
        json={"skippedTutorialIds": ["first-steps"]},
    )

    assert unauthenticated.status_code == 401
    assert login.status_code == 200
    assert saved.status_code == 200
    assert saved.json() == {
        "completed_tutorial_ids": [],
        "skipped_tutorial_ids": ["first-steps"],
    }
    assert (
        app.state.admin_user_store.get_user_preference("admin", "workflow_tutorial_progress")
        == saved.json()
    )


def test_admin_web_workflow_tutorial_preference_uses_azure_identity(
    tmp_path: Path, monkeypatch
) -> None:
    _set_azure_sso_env(monkeypatch, allowed_users="operator@example.com,other@example.com")
    app = create_app(build_admin_runtime(write_config(tmp_path)))
    client = TestClient(app)

    callback = _complete_azure_login(app, client, login="operator@example.com")
    saved = client.put(
        "/api/admin/user-preferences/workflow-tutorial-progress",
        json={"completed_tutorial_ids": ["first-steps"]},
    )

    assert callback.status_code == 303
    assert saved.status_code == 200
    assert (
        app.state.admin_user_store.get_user_preference(
            "operator@example.com", "workflow_tutorial_progress"
        )
        == saved.json()
    )
    assert (
        app.state.admin_user_store.get_user_preference(
            "other@example.com", "workflow_tutorial_progress"
        )
        is None
    )


def test_admin_web_exposes_admin_domain_smoke_endpoints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    monkeypatch.setattr(service.vm_agent_admin, "list_vms", lambda **kwargs: [])
    monkeypatch.setattr(
        service.workflow_config_admin,
        "read_workflow_editor_document",
        lambda: SimpleNamespace(graphs=[]),
    )
    monkeypatch.setattr(
        service.workflow_reports_admin,
        "read_workflow_failure_clusters_report",
        lambda **kwargs: _workflow_failure_cluster_report(
            [],
            generated_at=datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc),
            period_start=datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc),
            period_end=datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc),
            recent_threshold_seconds=86400,
            limit=100,
            has_more=False,
        ),
    )
    client = TestClient(create_app(service))

    responses = {
        "agents": client.get("/api/admin/agents"),
        "vms": client.get("/api/admin/vms"),
        "issues": client.get("/api/admin/issues"),
        "approvals": client.get("/api/admin/approvals"),
        "sessions": client.get("/api/admin/sessions"),
        "tool_calls": client.get("/api/admin/tool-calls"),
        "users": client.get("/api/admin/users"),
        "workers": client.get("/api/workers"),
        "configuration": client.get("/api/configuration/state"),
        "reports": client.get("/api/admin/reports/workflow-graph-activity"),
        "failure_clusters": client.get("/api/admin/reports/workflow-failure-clusters"),
        "conformance_reports": client.get("/api/admin/reports/conformance"),
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
    assert responses["tool_calls"].status_code == 200
    assert isinstance(responses["tool_calls"].json()["items"], list)
    assert responses["users"].status_code == 200
    assert isinstance(responses["users"].json()["users"], list)
    assert responses["workers"].status_code == 200
    assert "workers" in responses["workers"].json()
    assert responses["configuration"].status_code == 200
    assert responses["configuration"].json()["selected_directory_path"] == "root"
    assert responses["reports"].status_code == 200
    assert isinstance(responses["reports"].json()["graphs"], list)
    assert responses["failure_clusters"].status_code == 200
    assert isinstance(responses["failure_clusters"].json()["clusters"], list)
    assert responses["conformance_reports"].status_code == 200
    assert len(responses["conformance_reports"].json()["reports"]) == 2


def test_admin_web_approval_decisions_use_approval_directory_permission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AUTH_MODE", "local_password")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_USERNAME", "admin")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_PASSWORD", "admin-pass")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_SESSION_SECRET", "test-secret")
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    service.directory_admin.create_child(parent="root", name="ops")
    service.runtime.approvals.put(
        "ops-pending",
        ApprovalRequest(
            request_id="ops-pending",
            session_id="ops-session",
            action_type="email_send_approved",
            tool_name="email_send_approved",
            payload_digest="abc",
            human_summary="send email",
            payload={"body": "hello"},
            directory_path="root/ops",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        ),
    )
    service.runtime.approvals.put(
        "ops-approved",
        ApprovalRequest(
            request_id="ops-approved",
            session_id="ops-session",
            action_type="run_script",
            tool_name="run_script",
            payload_digest="def",
            human_summary="run script",
            payload={"script": "deploy"},
            directory_path="root/ops",
            status=ApprovalStatus.APPROVED,
            approver="lead",
            decision_at=datetime.now(timezone.utc),
            decision_reason="approved for deploy",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        ),
    )
    app = create_app(service)
    store = app.state.admin_user_store
    store.upsert_user(login="ops-owner@example.com", role="regular", display_name="Ops Owner")
    store.remove_user_group_assignment(
        login="ops-owner@example.com", group_id="operator", directory_path="root"
    )
    store.assign_user_group(
        login="ops-owner@example.com", group_id=OWNER_GROUP_ID, directory_path="root/ops"
    )
    store.set_local_password(login="ops-owner@example.com", password="ops-pass")
    client = TestClient(app)
    login = client.post(
        "/api/auth/login",
        json={"username": "ops-owner@example.com", "password": "ops-pass"},
    )

    locked_decision = client.post(
        "/api/admin/approvals/ops-pending/decision",
        json={"approver": "ops-owner@example.com", "approve": False, "reason": "unsafe"},
    )
    _unlock_privileged_operations(client)
    decision = client.post(
        "/api/admin/approvals/ops-pending/decision",
        json={"approver": "ops-owner@example.com", "approve": False, "reason": "unsafe"},
    )
    revoke = client.post(
        "/api/admin/approvals/ops-approved/revoke",
        json={"approver": "ops-owner@example.com", "reason": "superseded"},
    )

    assert login.status_code == 200
    assert locked_decision.status_code == 403
    assert locked_decision.json()["error"] == "Privileged operations are locked."
    assert decision.status_code == 200
    assert decision.json()["request"]["status"] == "denied"
    assert revoke.status_code == 200
    assert revoke.json()["request"]["status"] == "denied"


def test_admin_web_session_reads_use_directory_owner_permission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AUTH_MODE", "local_password")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_USERNAME", "admin")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_PASSWORD", "admin-pass")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_SESSION_SECRET", "test-secret")
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    ops = service.directory_admin.create_child(parent="root", name="ops")
    finance = service.directory_admin.create_child(parent="root", name="finance")
    assert ops.ok is True and ops.data is not None
    assert finance.ok is True and finance.data is not None
    ops_path = ops.data.details.directory.path
    finance_path = finance.data.details.directory.path
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="ops-session",
            vm_id="vm-ops",
            directory_path=ops_path,
            allowed_tools=[ToolName.APPROVAL_CHECK],
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    )
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="finance-session",
            vm_id="vm-finance",
            directory_path=finance_path,
            allowed_tools=[ToolName.APPROVAL_CHECK],
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    )
    app = create_app(service)
    store = app.state.admin_user_store
    store.upsert_user(login="ops-owner@example.com", role="regular", display_name="Ops Owner")
    store.remove_user_group_assignment(
        login="ops-owner@example.com", group_id="operator", directory_path="root"
    )
    store.assign_user_group(
        login="ops-owner@example.com", group_id=OWNER_GROUP_ID, directory_path=ops_path
    )
    store.set_local_password(login="ops-owner@example.com", password="ops-pass")
    client = TestClient(app)
    login = client.post(
        "/api/auth/login",
        json={"username": "ops-owner@example.com", "password": "ops-pass"},
    )

    all_sessions = client.get("/api/admin/sessions", params={"include_expired": "true"})
    ops_sessions = client.get(
        "/api/admin/sessions",
        params={"directory_path": ops_path, "include_expired": "true"},
    )
    finance_sessions = client.get(
        "/api/admin/sessions",
        params={"directory_path": finance_path, "include_expired": "true"},
    )
    ops_detail = client.get("/api/admin/sessions/ops-session")
    finance_detail = client.get("/api/admin/sessions/finance-session")

    assert login.status_code == 200
    assert all_sessions.status_code == 200
    assert [item["session_id"] for item in all_sessions.json()["items"]] == ["ops-session"]
    assert ops_sessions.status_code == 200
    assert [item["session_id"] for item in ops_sessions.json()["items"]] == ["ops-session"]
    assert finance_sessions.status_code == 403
    assert ops_detail.status_code == 200
    assert ops_detail.json()["session"]["session_id"] == "ops-session"
    assert finance_detail.status_code == 403


def test_admin_web_approval_reads_use_directory_owner_permission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AUTH_MODE", "local_password")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_USERNAME", "admin")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_PASSWORD", "admin-pass")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_SESSION_SECRET", "test-secret")
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    ops = service.directory_admin.create_child(parent="root", name="ops")
    finance = service.directory_admin.create_child(parent="root", name="finance")
    assert ops.ok is True and ops.data is not None
    assert finance.ok is True and finance.data is not None
    ops_path = ops.data.details.directory.path
    finance_path = finance.data.details.directory.path
    service.runtime.approvals.put(
        "ops-approval",
        ApprovalRequest(
            request_id="ops-approval",
            session_id="ops-session",
            action_type="email_send_approved",
            tool_name="email_send_approved",
            payload_digest="abc",
            human_summary="send ops email",
            payload={"body": "ops"},
            directory_path=ops_path,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        ),
    )
    service.runtime.approvals.put(
        "finance-approval",
        ApprovalRequest(
            request_id="finance-approval",
            session_id="finance-session",
            action_type="email_send_approved",
            tool_name="email_send_approved",
            payload_digest="def",
            human_summary="send finance email",
            payload={"body": "finance"},
            directory_path=finance_path,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        ),
    )
    app = create_app(service)
    store = app.state.admin_user_store
    store.upsert_user(login="ops-owner@example.com", role="regular", display_name="Ops Owner")
    store.remove_user_group_assignment(
        login="ops-owner@example.com", group_id="operator", directory_path="root"
    )
    store.assign_user_group(
        login="ops-owner@example.com", group_id=OWNER_GROUP_ID, directory_path=ops_path
    )
    store.set_local_password(login="ops-owner@example.com", password="ops-pass")
    client = TestClient(app)
    login = client.post(
        "/api/auth/login",
        json={"username": "ops-owner@example.com", "password": "ops-pass"},
    )

    all_approvals = client.get("/api/admin/approvals")
    ops_approvals = client.get("/api/admin/approvals", params={"directory_path": ops_path})
    finance_approvals = client.get("/api/admin/approvals", params={"directory_path": finance_path})
    ops_detail = client.get("/api/admin/approvals/ops-approval")
    finance_detail = client.get("/api/admin/approvals/finance-approval")

    assert login.status_code == 200
    assert all_approvals.status_code == 200
    assert [item["request_id"] for item in all_approvals.json()["items"]] == ["ops-approval"]
    assert all_approvals.json()["health"]["total"] == 1
    assert ops_approvals.status_code == 200
    assert [item["request_id"] for item in ops_approvals.json()["items"]] == ["ops-approval"]
    assert finance_approvals.status_code == 403
    assert ops_detail.status_code == 200
    assert ops_detail.json()["request"]["request_id"] == "ops-approval"
    assert finance_detail.status_code == 403


def _assert_json_schema_requires_every_object_property(
    schema: dict[str, object], path: str = "$"
) -> None:
    schema_type = schema.get("type")
    if schema_type == "object" or (isinstance(schema_type, list) and "object" in schema_type):
        properties = schema.get("properties")
        required = schema.get("required")
        assert isinstance(properties, dict), f"{path} must define object properties"
        assert isinstance(required, list), f"{path} must define required properties"
        assert set(properties) <= set(required), f"{path} required must include every property"
    else:
        properties = schema.get("properties")

    if isinstance(properties, dict):
        for key, value in properties.items():
            if isinstance(value, dict):
                _assert_json_schema_requires_every_object_property(value, f"{path}.{key}")
    items = schema.get("items")
    if isinstance(items, dict):
        _assert_json_schema_requires_every_object_property(items, f"{path}[]")


def test_workflow_generator_returns_validated_draft_without_writing_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.operations_admin.write_assistant_settings(
        {"workflow_generation_model": "gpt-workflow-test"}
    )
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    captured_requests: list[dict[str, object]] = []

    generated = {
        "graph": {
            "id": "generated-maintenance",
            "name": "Generated maintenance",
            "description": "Drafted from natural language.",
            "directory_paths": [],
            "user_ids": [],
        },
        "nodes": [
            {
                "id": "schedule",
                "kind": "trigger",
                "type": "trigger:cron",
                "display_name": "Nightly schedule",
                "config_json": json.dumps({"expression": "0 2 * * *"}),
            },
            {
                "id": "emit-summary",
                "kind": "effect",
                "type": "effect:event:emit",
                "display_name": "Emit summary event",
                "config_json": json.dumps(
                    {"event_type": "generated.summary", "payload": {"source": "workflow-generator"}}
                ),
            },
        ],
        "edges": [
            {
                "source_id": "schedule",
                "source_port": None,
                "target_id": "emit-summary",
                "allow_cycle": False,
            }
        ],
        "warnings": ["Review placeholder payload values before saving."],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(
            {
                "url": str(request.url),
                "headers": dict(request.headers),
                "payload": json.loads(request.content.decode("utf-8")),
            }
        )
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(generated),
                            }
                        ]
                    }
                ]
            },
        )

    service.workflow_generation_admin._workflow_generation_transport_provider = lambda: (
        httpx.MockTransport(handler)
    )
    client = TestClient(create_app(service))

    response = client.post(
        "/api/workflow-editor/generate-draft",
        json={"description": "Every night, emit a summary event.", "directory_path": "root"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["model"] == "gpt-workflow-test"
    assert payload["graphs"][0]["directory_paths"] == ["root"]
    assert [node["id"] for node in payload["config"]["triggers"]] == ["schedule"]
    assert [node["id"] for node in payload["config"]["effects"]] == ["emit-summary"]
    assert payload["config"]["edges"] == [{"source_id": "schedule", "target_id": "emit-summary"}]
    assert payload["warnings"] == ["Review placeholder payload values before saving."]
    assert captured_requests
    request_payload = captured_requests[0]["payload"]
    assert request_payload["model"] == "gpt-workflow-test"
    assert request_payload["text"]["format"]["type"] == "json_schema"
    assert request_payload["text"]["format"]["strict"] is True
    schema = request_payload["text"]["format"]["schema"]
    assert isinstance(schema, dict)
    _assert_json_schema_requires_every_object_property(schema)
    assert captured_requests[0]["headers"]["authorization"] == "Bearer sk-test"
    assert not (tmp_path / "config" / "trigger-orchestrator" / "config.json").exists()
    assert not (tmp_path / "config" / "trigger-orchestrator" / "layout.json").exists()


def test_workflow_generator_rejects_cyclic_generated_graph_without_hanging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.operations_admin.write_assistant_settings(
        {"workflow_generation_model": "gpt-workflow-test"}
    )
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    generated = {
        "graph": {
            "id": "generated-cycle",
            "name": "Generated cycle",
            "description": "Drafted from natural language.",
        },
        "nodes": [
            {
                "id": "schedule",
                "kind": "trigger",
                "type": "trigger:cron",
                "display_name": "Nightly schedule",
                "config_json": json.dumps({"expression": "0 2 * * *"}),
            }
        ],
        "edges": [{"source_id": "schedule", "target_id": "schedule"}],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(generated),
                            }
                        ]
                    }
                ]
            },
        )

    service.workflow_generation_admin._workflow_generation_transport_provider = lambda: (
        httpx.MockTransport(handler)
    )
    client = TestClient(create_app(service))

    response = client.post(
        "/api/workflow-editor/generate-draft",
        json={"description": "Create a cyclic generated workflow.", "directory_path": "root"},
    )

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("application/json")
    assert "schedule" in response.json()["error"]


def _workflow_generator_success_response() -> dict[str, object]:
    generated = {
        "graph": {
            "id": "generated-cadence",
            "name": "Generated cadence",
            "description": "Drafted from natural language.",
        },
        "nodes": [
            {
                "id": "schedule",
                "kind": "trigger",
                "type": "trigger:cron",
                "display_name": "Nightly schedule",
                "config_json": json.dumps({"expression": "0 2 * * *"}),
            },
            {
                "id": "emit-summary",
                "kind": "effect",
                "type": "effect:event:emit",
                "display_name": "Emit summary event",
                "config_json": json.dumps(
                    {"event_type": "generated.summary", "payload": {"source": "workflow-generator"}}
                ),
            },
        ],
        "edges": [{"source_id": "schedule", "target_id": "emit-summary"}],
    }
    return {
        "output": [
            {
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(generated),
                    }
                ]
            }
        ]
    }


def test_workflow_generator_returns_json_error_for_transport_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.operations_admin.write_assistant_settings(
        {"workflow_generation_model": "gpt-workflow-test"}
    )
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    service.workflow_generation_admin._workflow_generation_transport_provider = lambda: (
        httpx.MockTransport(handler)
    )
    client = TestClient(create_app(service))

    response = client.post(
        "/api/workflow-editor/generate-draft",
        json={"description": "Every night, emit a generated summary event."},
    )

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("application/json")
    assert "OpenAI workflow generation request failed" in response.json()["error"]
    assert "connection refused" in response.json()["error"]


def test_workflow_generator_rejects_back_to_back_model_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.operations_admin.write_assistant_settings(
        {"workflow_generation_model": "gpt-workflow-test"}
    )
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    model_request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal model_request_count
        model_request_count += 1
        return httpx.Response(200, json=_workflow_generator_success_response())

    service.workflow_generation_admin._workflow_generation_transport_provider = lambda: (
        httpx.MockTransport(handler)
    )
    client = TestClient(create_app(service))

    first_response = client.post(
        "/api/workflow-editor/generate-draft",
        json={"description": "Every night, emit a generated summary event."},
    )
    second_response = client.post(
        "/api/workflow-editor/generate-draft",
        json={"description": "Every morning, emit a generated summary event."},
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 400
    assert "cooling down" in second_response.json()["error"]
    assert model_request_count == 1


def test_workflow_generator_rejects_concurrent_model_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.operations_admin.write_assistant_settings(
        {"workflow_generation_model": "gpt-workflow-test"}
    )
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    model_request_count = 0

    async def run_case() -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal model_request_count
            model_request_count += 1
            started.set()
            await release.wait()
            return httpx.Response(200, json=_workflow_generator_success_response())

        service.workflow_generation_admin._workflow_generation_transport_provider = lambda: (
            httpx.MockTransport(handler)
        )
        first_task = asyncio.create_task(
            service.workflow_generation_admin.generate_workflow_editor_draft(
                description="Every night, emit a generated summary event.",
                directory_path="root",
            )
        )
        try:
            await asyncio.wait_for(started.wait(), timeout=1.0)
            with pytest.raises(ValueError, match="already in progress"):
                await service.workflow_generation_admin.generate_workflow_editor_draft(
                    description="Every morning, emit a generated summary event.",
                    directory_path="root",
                )
        finally:
            release.set()
        await first_task

    asyncio.run(run_case())
    assert model_request_count == 1


def test_workflow_generator_requires_configured_model(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    client = TestClient(create_app(service))

    response = client.post(
        "/api/workflow-editor/generate-draft",
        json={"description": "Draft a workflow from this text."},
    )

    assert response.status_code == 400
    assert "workflow_generation_model" in response.json()["error"]


def test_admin_web_dashboard_requests_use_opt_in_backend_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    calls = 0
    capacity_calls = 0

    def list_background_workers() -> list[WorkerSummary]:
        nonlocal calls
        calls += 1
        return [
            WorkerSummary(
                worker_id="trigger",
                name="Trigger",
                unit="evander-trigger-orchestrator.service",
                active_state=f"active-{calls}",
                running=True,
                failed=False,
            )
        ]

    def read_capacity_health() -> dict[str, object]:
        nonlocal capacity_calls
        capacity_calls += 1
        return {
            "generated_at": "2026-05-21T12:00:00+00:00",
            "workers": {"total_workers": capacity_calls},
        }

    monkeypatch.setattr(
        service.operations_admin, "list_background_workers", list_background_workers
    )
    monkeypatch.setattr(service.capacity_health_admin, "read_capacity_health", read_capacity_health)
    client = TestClient(create_app(service))

    first = client.get("/api/workers?_dashboard=1")
    not_modified = client.get(
        "/api/workers?_dashboard=1",
        headers={"If-None-Match": first.headers["etag"]},
    )
    second = client.get("/api/workers?_dashboard=1")
    live = client.get("/api/workers")
    refreshed = client.get(
        "/api/workers?_dashboard=1&_dashboard_refresh=1",
        headers={"If-None-Match": first.headers["etag"]},
    )
    capacity_first = client.get("/api/admin/capacity-health?_dashboard=1")
    capacity_second = client.get("/api/admin/capacity-health?_dashboard=1")
    capacity_live = client.get("/api/admin/capacity-health")
    capacity_refreshed = client.get("/api/admin/capacity-health?_dashboard=1&_dashboard_refresh=1")

    assert first.status_code == 200
    assert not_modified.status_code == 304
    assert second.status_code == 200
    assert live.status_code == 200
    assert refreshed.status_code == 200
    assert capacity_first.status_code == 200
    assert capacity_second.status_code == 200
    assert capacity_live.status_code == 200
    assert capacity_refreshed.status_code == 200
    assert first.headers["x-admin-web-cache"] == "miss"
    assert not_modified.headers["x-admin-web-cache"] == "hit"
    assert not_modified.headers["etag"] == first.headers["etag"]
    assert not_modified.content == b""
    assert second.headers["x-admin-web-cache"] == "hit"
    assert second.headers["etag"] == first.headers["etag"]
    assert "x-admin-web-cache" not in live.headers
    assert refreshed.headers["x-admin-web-cache"] == "refresh"
    assert refreshed.status_code == 200
    assert capacity_first.headers["x-admin-web-cache"] == "miss"
    assert capacity_second.headers["x-admin-web-cache"] == "hit"
    assert "x-admin-web-cache" not in capacity_live.headers
    assert capacity_refreshed.headers["x-admin-web-cache"] == "refresh"
    assert first.json() == second.json()
    assert first.json()["workers"][0]["active_state"] == "active-1"
    assert live.json()["workers"][0]["active_state"] == "active-2"
    assert refreshed.json()["workers"][0]["active_state"] == "active-3"
    assert capacity_first.json() == capacity_second.json()
    assert capacity_first.json()["workers"]["total_workers"] == 1
    assert capacity_live.json()["workers"]["total_workers"] == 2
    assert capacity_refreshed.json()["workers"]["total_workers"] == 3
    assert calls == 3
    assert capacity_calls == 3


def _dashboard_cache_request(
    path: str = "/api/admin/agents",
    query_string: str = "_dashboard=1&directory_path=root&direct_only=false",
) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "root_path": "",
            "scheme": "http",
            "server": ("testserver", 80),
            "headers": [],
            "query_string": query_string.encode("utf-8"),
        }
    )


def test_admin_web_dashboard_cache_coalesces_concurrent_json_misses() -> None:
    async def run_case() -> None:
        cache = AdminWebJsonResponseCache(default_ttl_seconds=30.0)
        calls = 0
        started = asyncio.Event()
        release = asyncio.Event()

        async def factory() -> JSONResponse:
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return JSONResponse({"items": [{"agent_id": f"agent-{calls}"}]})

        first_task = asyncio.create_task(
            cache.get_or_set_json(
                _dashboard_cache_request(),
                "admin.agents.list",
                factory,
            )
        )
        await asyncio.wait_for(started.wait(), timeout=1.0)
        second_task = asyncio.create_task(
            cache.get_or_set_json(
                _dashboard_cache_request(),
                "admin.agents.list",
                factory,
            )
        )
        await asyncio.sleep(0)
        release.set()
        first, second = await asyncio.gather(first_task, second_task)

        assert calls == 1
        assert first.headers["x-admin-web-cache"] == "miss"
        assert second.headers["x-admin-web-cache"] == "hit"
        assert json.loads(first.body) == {"items": [{"agent_id": "agent-1"}]}
        assert json.loads(second.body) == {"items": [{"agent_id": "agent-1"}]}

    asyncio.run(run_case())


def test_admin_web_conformance_report_endpoint_returns_invalid_records(
    tmp_path: Path,
) -> None:
    config_path = write_config(tmp_path)
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "trigger-conformance-report.json").write_text("{bad json", encoding="utf-8")
    client = TestClient(create_app(build_admin_runtime(config_path)))

    response = client.get("/api/admin/reports/conformance")

    assert response.status_code == 200
    payload = response.json()
    reports = {report["kind"]: report for report in payload["reports"]}
    assert reports["trigger"]["state"] == "invalid"
    assert "not valid JSON" in reports["trigger"]["error"]
    assert reports["effect"]["state"] == "missing"


def test_admin_web_run_conformance_report_endpoint_delegates_to_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    client = TestClient(create_app(service))
    calls: list[str] = []

    def fake_run(kind: str) -> SimpleNamespace:
        calls.append(kind)
        return SimpleNamespace(
            model_dump=lambda mode: {
                "kind": kind,
                "exit_code": 0,
                "started_at": "2026-05-17T09:00:00Z",
                "completed_at": "2026-05-17T09:00:01Z",
                "duration_seconds": 1.0,
                "output": "ok",
                "reports": {
                    "generated_at": "2026-05-17T09:00:01Z",
                    "stale_after_seconds": 604800,
                    "reports": [],
                },
            }
        )

    monkeypatch.setattr(service.workflow_reports_admin, "run_conformance_report", fake_run)

    response = client.post("/api/admin/reports/conformance/trigger/run")

    assert response.status_code == 200
    assert calls == ["trigger"]
    assert response.json()["kind"] == "trigger"


def test_admin_web_active_issue_filter_excludes_done_issues(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    active_issue = service.issue_git_admin.create_local_issue(title="Active issue", body="body")
    done_issue = LocalIssueRecord(
        issue_id="root::azure_devops::42",
        issue_number="42",
        title="Done issue",
        body="body",
        yaml_path=str(tmp_path / "done-issue.yaml"),
        directory_path="root",
        source_type="remote_proxy",
        source="azure_devops",
        state="Done",
    )
    service.local_issue_store.put(done_issue.issue_id, done_issue)
    client = TestClient(create_app(service))

    active_response = client.get("/api/admin/issues?state_filter=active")
    all_response = client.get("/api/admin/issues?state_filter=all")

    assert active_response.status_code == 200
    assert all_response.status_code == 200
    assert [item["issue_id"] for item in active_response.json()["items"]] == [active_issue.issue_id]
    assert {item["issue_id"] for item in all_response.json()["items"]} == {
        active_issue.issue_id,
        done_issue.issue_id,
    }


def test_admin_web_workers_expose_config_schema(tmp_path: Path) -> None:
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))

    workers_response = client.get("/api/workers")
    worker_id = workers_response.json()["workers"][0]["worker_id"]
    detail_response = client.get(f"/api/workers/{worker_id}")

    assert detail_response.status_code == 200
    payload = detail_response.json()
    assert payload["config"]["worker_id"] == worker_id
    assert isinstance(payload["config"]["revision"], str)
    assert payload["config_schema"]["worker_id"] == worker_id
    assert any(field["field_name"] == "runtime_dir" for field in payload["config_schema"]["fields"])


def test_admin_web_worker_config_save_requires_matching_revision(tmp_path: Path) -> None:
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))
    _unlock_privileged_operations(client)
    worker_id = "trigger-orchestrator"
    detail_response = client.get(f"/api/workers/{worker_id}")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    config = dict(detail["config"]["config"])
    config["poll_interval_seconds"] = int(config.get("poll_interval_seconds") or 5) + 1

    missing = client.put(f"/api/workers/{worker_id}/config", json={"config": config})
    stale = client.put(
        f"/api/workers/{worker_id}/config",
        json={"config": config, "expected_revision": "stale"},
    )
    saved = client.put(
        f"/api/workers/{worker_id}/config",
        json={"config": config, "expected_revision": detail["config"]["revision"]},
    )

    assert missing.status_code == 428
    assert stale.status_code == 409
    assert stale.json()["details"]["current_revision"] == detail["config"]["revision"]
    assert saved.status_code == 200
    assert detail["config"]["exists"] is False
    assert saved.json()["config"]["exists"] is True
    assert saved.json()["config"]["revision"] != detail["config"]["revision"]
    assert (
        saved.json()["config"]["config"]["poll_interval_seconds"] == config["poll_interval_seconds"]
    )


def test_admin_web_configuration_state_exposes_grouped_tabs(tmp_path: Path) -> None:
    SqliteCommunicationTargetsRepository(
        communication_targets_db_path(tmp_path / "runtime")
    ).write_overlay(
        "root",
        {
            "targets": [
                {
                    "id": "ops-approvals",
                    "provider": "teams",
                    "kind": "teams",
                    "address": "team/channel",
                    "policy": {"capabilities": ["notify"]},
                },
                {
                    "id": "ops-chat",
                    "provider": "teams",
                    "kind": "teams",
                    "address": "team/other-channel",
                    "policy": {"capabilities": ["reply"]},
                },
                {
                    "id": "backup-approvals",
                    "provider": "email",
                    "kind": "email",
                    "address": "backup@example.com",
                    "policy": {"capabilities": ["notify", "reply"]},
                },
            ]
        },
    )
    client = TestClient(
        create_app(build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True)))
    )

    response = client.get("/api/configuration/state")

    assert response.status_code == 200
    payload = response.json()
    expected_tab_keys = [
        "people_communications",
        "models_assistant_knowledge",
        "agent_runtime_launch",
        "code_issues_prs",
        "access_approvals_audit",
        "network_relays_packages",
        "automation_assignment",
        "advanced_raw",
    ]
    assert [tab["key"] for tab in payload["tabs"]] == expected_tab_keys
    assert [tab["title"] for tab in payload["tabs"]] == [
        "People & Communications",
        "Models, Assistant & Knowledge",
        "Agent Runtime & Launch",
        "Code, Issues & Pull Requests",
        "Access, Approvals & Audit",
        "Network, Relays & Package Access",
        "Automation & Assignment",
        "Advanced & Raw",
    ]
    assert "communications" not in payload["tab_snapshots"]
    assert "providers" not in payload["tab_snapshots"]
    assert isinstance(payload["tab_snapshots"]["people_communications"]["revision"], str)
    assert isinstance(payload["snapshots"]["providers"]["revision"], str)
    people = next(tab for tab in payload["tabs"] if tab["key"] == "people_communications")
    assert people["title"] == "People & Communications"
    assert {section["title"] for section in people["sections"]} == {
        "Contacts",
        "Communication targets",
        "Approval notification targets",
        "Discord",
        "Teams",
        "Email mailboxes",
        "Email access and sending policy",
        "Phone calls",
        "Communication files",
        "Communication artifact links",
    }
    contacts = next(section for section in people["sections"] if section["title"] == "Contacts")
    assert {field["field_name"] for field in contacts["fields"]} == {"contacts"}
    communication_targets = next(
        section for section in people["sections"] if section["title"] == "Communication targets"
    )
    target_fields = {field["field_name"]: field for field in communication_targets["fields"]}
    assert target_fields["targets"]["source_domain"] == "communication_targets"
    assert target_fields["targets"]["kind"] == "json"
    assert target_fields["targets"]["root_starts_editable"] is True
    assert "workflow notification" in target_fields["targets"]["tooltip"]
    approval_notifications = next(
        section
        for section in people["sections"]
        if section["title"] == "Approval notification targets"
    )
    approval_notification_fields = {
        field["field_name"]: field for field in approval_notifications["fields"]
    }
    assert approval_notification_fields["approval_notification_target_ids"]["kind"] == "string_list"
    assert approval_notification_fields["approval_notification_target_ids"]["options"] == [
        "backup-approvals",
        "ops-approvals",
    ]
    assert (
        "approval request notifications"
        in approval_notification_fields["approval_notification_target_ids"]["tooltip"]
    )
    assert (
        "known notify-capable communication targets"
        in approval_notification_fields["approval_notification_target_ids"]["tooltip"]
    )

    models = next(tab for tab in payload["tabs"] if tab["key"] == "models_assistant_knowledge")
    openai_section = next(
        section for section in models["sections"] if section["title"] == "LLM providers"
    )
    openai_fields = {field["field_name"]: field for field in openai_section["fields"]}
    assert {
        "model_provider_mode",
        "openai_api_base_url",
        "openai_api_key_env",
        "openai_relay_timeout_seconds",
        "local_openai_api_base_url",
        "local_openai_api_key",
        "local_openai_provider_name",
        "azure_openai_endpoint",
        "azure_openai_auth_mode",
        "azure_openai_api_key_env",
        "azure_openai_api_version",
        "azure_openai_model_deployments",
        "anthropic_api_base_url",
        "anthropic_api_key_env",
        "anthropic_api_version",
        "anthropic_relay_timeout_seconds",
    }.issubset(openai_fields)
    assert "local_openai" in openai_fields["model_provider_mode"]["tooltip"]
    assert "azure_openai" in openai_fields["model_provider_mode"]["tooltip"]
    assert "anthropic" in openai_fields["model_provider_mode"]["tooltip"]
    assert openai_fields["model_provider_mode"]["label"] == "Default model provider mode"
    assert "sessions can override" in openai_fields["model_provider_mode"]["tooltip"]
    assert openai_fields["openai_api_base_url"]["label"] == "OpenAI API base URL"
    assert openai_fields["openai_api_key_env"]["label"] == "OpenAI API key env"
    assert openai_fields["openai_relay_timeout_seconds"]["label"] == (
        "OpenAI relay timeout seconds"
    )
    assert openai_fields["local_openai_api_base_url"]["label"] == ("Local OpenAI API base URL")
    assert "OpenAI API base URL" in openai_fields["openai_api_base_url"]["tooltip"]
    assert "local_openai mode" in openai_fields["local_openai_api_base_url"]["tooltip"]
    assert "Azure OpenAI" in openai_fields["azure_openai_endpoint"]["tooltip"]
    assert "Anthropic" in openai_fields["anthropic_api_base_url"]["tooltip"]
    assistant = next(section for section in models["sections"] if section["title"] == "Assistant")
    assistant_fields = {field["field_name"]: field for field in assistant["fields"]}
    assert assistant_fields["enabled"]["scope"] == "global"
    assert "admin web assistant launcher" in assistant_fields["enabled"]["tooltip"]
    assert assistant_fields["show_launcher_when_disabled"]["kind"] == "bool"
    assert assistant_fields["default_profile"]["kind"] == "literal"
    assert assistant_fields["default_profile"]["label"] == "assistant capability profile"
    assert assistant_fields["default_profile"]["options"] == [
        "",
        "read-only",
        "operator-limited",
        "full-admin",
    ]
    assert "allowed_mcp_tool_profile" not in assistant_fields
    assert assistant_fields["custom_system_instructions"]["kind"] == "multiline_text"
    semantic_store = next(
        section for section in models["sections"] if section["title"] == "Semantic store"
    )
    assert {field["field_name"] for field in semantic_store["fields"]} == {
        "semantic_store_provider",
        "qdrant_base_url",
        "qdrant_api_key_env",
        "semantic_embedding_provider",
        "semantic_embedding_model",
        "semantic_embedding_dimensions",
        "semantic_vector_distance",
    }
    all_fields = {
        field["field_name"]: field
        for tab in payload["tabs"]
        for section in tab["sections"]
        for field in section["fields"]
    }

    runtime = next(tab for tab in payload["tabs"] if tab["key"] == "agent_runtime_launch")
    agent_execution = next(
        section for section in runtime["sections"] if section["title"] == "Agent execution"
    )
    agent_execution_fields = {field["field_name"]: field for field in agent_execution["fields"]}
    assert agent_execution_fields["execution_backend"]["label"] == "Execution backend"
    assert "Value: one of" in agent_execution_fields["execution_backend"]["tooltip"]
    container_worker = next(
        section
        for section in runtime["sections"]
        if section["title"] == "Container worker settings"
    )
    container_worker_fields = {field["field_name"] for field in container_worker["fields"]}
    assert "container_worker_base_url" in container_worker_fields
    assert "container_worker_images" in container_worker_fields
    assert "container_worker_mssql_enabled" in container_worker_fields
    agent_prompt = next(
        section for section in runtime["sections"] if section["title"] == "Agent prompt"
    )
    agent_prompt_fields = {field["field_name"]: field for field in agent_prompt["fields"]}
    assert set(agent_prompt_fields) == {
        "agent_prompt_token_secret_env",
        "agent_prompt_token_ttl_seconds",
        "agent_prompt_queue_max_retries",
        "agent_context_retry_enabled",
        "agent_context_retry_max_attempts",
        "agent_context_retry_target_chars",
    }
    assert agent_prompt_fields["agent_prompt_queue_max_retries"]["kind"] == "int"
    assert agent_prompt_fields["agent_context_retry_enabled"]["kind"] == "bool"
    assert agent_prompt_fields["agent_context_retry_max_attempts"]["kind"] == "int"
    assert agent_prompt_fields["agent_context_retry_target_chars"]["kind"] == "int"
    assert "container worker service" in all_fields["container_worker_base_url"]["tooltip"]
    assert (
        "Fallback runner container image" in all_fields["container_worker_default_image"]["tooltip"]
    )
    assert "catalog" in all_fields["container_worker_images"]["tooltip"]
    assert (
        "runtime dispatch never pulls or builds images"
        in all_fields["container_worker_images"]["tooltip"]
    )
    agent_defaults = next(
        section for section in runtime["sections"] if section["title"] == "Agent defaults"
    )
    assert {field["field_name"] for field in agent_defaults["fields"]} == {
        "default_model",
        "post_exec_verification_prompt",
    }
    guest_heartbeat = next(
        section for section in runtime["sections"] if section["title"] == "Guest heartbeat"
    )
    assert {field["field_name"] for field in guest_heartbeat["fields"]} == {
        "enable_guest_heartbeat",
        "guest_heartbeat_prompt",
    }

    for tab in payload["tabs"]:
        for section in tab["sections"]:
            for field in section["fields"]:
                assert field["tooltip"], f"{tab['key']}.{field['field_name']} is missing a tooltip"
                assert "Value:" in field["tooltip"]

    email_mailboxes = next(
        section for section in people["sections"] if section["title"] == "Email mailboxes"
    )
    assert {field["field_name"] for field in email_mailboxes["fields"]} == {
        "email_mailboxes",
        "email_provider_timeout_seconds",
    }
    email_policy = next(
        section
        for section in people["sections"]
        if section["title"] == "Email access and sending policy"
    )
    assert {field["field_name"] for field in email_policy["fields"]} == {
        "email_draft_domain_allowlist",
        "email_draft_recipient_allowlist",
        "email_folder_allowlist_by_mailbox",
        "email_mailbox_allowlist",
        "email_send_enabled",
    }
    communication_files = next(
        section for section in people["sections"] if section["title"] == "Communication files"
    )
    communication_file_fields = {field["field_name"] for field in communication_files["fields"]}
    assert "communication_file_receive_enabled" in communication_file_fields
    assert "communication_file_require_approval_for_send" in communication_file_fields
    communication_artifacts = next(
        section
        for section in people["sections"]
        if section["title"] == "Communication artifact links"
    )
    communication_artifact_fields = {
        field["field_name"]: field for field in communication_artifacts["fields"]
    }
    assert set(communication_artifact_fields) == {
        "communication_artifact_public_base_url",
        "communication_artifact_link_secret_env",
        "communication_artifact_link_ttl_seconds",
    }
    assert communication_artifact_fields["communication_artifact_public_base_url"]["label"] == (
        "Communication artifact public base URL"
    )
    assert communication_artifact_fields["communication_artifact_public_base_url"]["kind"] == (
        "text"
    )
    assert (
        "Teams recipients"
        in communication_artifact_fields["communication_artifact_public_base_url"]["tooltip"]
    )
    assert (
        "gateway and Admin Web"
        in communication_artifact_fields["communication_artifact_link_secret_env"]["tooltip"]
    )
    assert communication_artifact_fields["communication_artifact_link_ttl_seconds"]["kind"] == (
        "int"
    )

    access_policy = next(tab for tab in payload["tabs"] if tab["key"] == "access_approvals_audit")
    approval_requirements = next(
        section
        for section in access_policy["sections"]
        if section["title"] == "Approval requirements"
    )
    approval_requirement_fields = {
        field["field_name"]: field for field in approval_requirements["fields"]
    }
    assert set(approval_requirement_fields) == {"approval_required_tools"}
    assert approval_requirement_fields["approval_required_tools"]["source_domain"] == "policy"
    tool_argument_policy = next(
        section
        for section in access_policy["sections"]
        if section["title"] == "Tool argument policy"
    )
    tool_argument_policy_fields = {
        field["field_name"]: field for field in tool_argument_policy["fields"]
    }
    assert tool_argument_policy_fields["tool_argument_policies"]["kind"] == "json"
    assert tool_argument_policy_fields["tool_argument_policies"]["label"] == (
        "Tool argument policy"
    )
    assert "MCP tool name" in tool_argument_policy_fields["tool_argument_policies"]["tooltip"]
    approval_timing = next(
        section for section in access_policy["sections"] if section["title"] == "Approval timing"
    )
    assert {field["field_name"] for field in approval_timing["fields"]} == {
        "approval_ttl_seconds",
        "default_session_ttl_seconds",
    }

    phone_calls = next(
        section for section in people["sections"] if section["title"] == "Phone calls"
    )
    assert {field["field_name"] for field in phone_calls["fields"]} == {
        "phone_call_backend",
        "retell_api_base_url",
        "retell_api_key_env",
        "retell_from_number",
        "retell_default_agent_id",
        "retell_request_timeout_seconds",
        "didww_sip_bridge_url",
        "didww_sip_bridge_token_env",
        "didww_from_number",
        "inbound_phone_routes",
    }
    phone_call_fields = {field["field_name"]: field for field in phone_calls["fields"]}
    assert phone_call_fields["phone_call_backend"]["label"] == "Phone call backend"
    assert phone_call_fields["retell_from_number"]["label"] == "Retell from number"
    assert phone_call_fields["didww_from_number"]["label"] == "DIDWW from number"
    assert phone_call_fields["didww_sip_bridge_url"]["label"] == "DIDWW SIP bridge URL"
    assert phone_call_fields["inbound_phone_routes"]["label"] == "Inbound phone routes"

    code = next(tab for tab in payload["tabs"] if tab["key"] == "code_issues_prs")
    assert {section["title"] for section in code["sections"]} == {
        "Azure DevOps",
        "GitHub",
        "ADO worker filters",
        "ADO worker repo metadata",
        "Git transport",
        "Issue setup Azure DevOps",
        "PR feedback approval",
        "PR template",
    }
    pr_template = next(section for section in code["sections"] if section["title"] == "PR template")
    pr_template_fields = {field["field_name"]: field for field in pr_template["fields"]}
    assert pr_template_fields["pr_template_markdown"]["kind"] == "multiline_text"
    assert pr_template_fields["pr_template_markdown"]["root_starts_editable"] is True
    pr_feedback = next(
        section for section in code["sections"] if section["title"] == "PR feedback approval"
    )
    assert {field["field_name"] for field in pr_feedback["fields"]} == {"pr_reply_require_approval"}

    network = next(tab for tab in payload["tabs"] if tab["key"] == "network_relays_packages")
    vm_provider = next(
        section for section in network["sections"] if section["title"] == "VM provider"
    )
    vm_provider_fields = {field["field_name"]: field for field in vm_provider["fields"]}
    assert set(vm_provider_fields) == {"provider", "provider_defaults", "azure"}
    assert vm_provider_fields["provider"]["kind"] == "text"
    assert vm_provider_fields["provider_defaults"]["kind"] == "json"
    assert vm_provider_fields["azure"]["kind"] == "json"
    libvirt_templates = next(
        section for section in network["sections"] if section["title"] == "Libvirt templates"
    )
    libvirt_template_fields = {field["field_name"]: field for field in libvirt_templates["fields"]}
    assert set(libvirt_template_fields) == {
        "libvirt_vm_default_template",
        "libvirt_vm_templates",
    }
    assert libvirt_template_fields["libvirt_vm_default_template"]["kind"] == "text"
    assert libvirt_template_fields["libvirt_vm_templates"]["kind"] == "json"
    http_target_relay = next(
        section for section in network["sections"] if section["title"] == "HTTP target relay"
    )
    assert {field["field_name"] for field in http_target_relay["fields"]} == {
        "http_target_relay_host",
        "http_target_relay_port",
        "http_target_relay_timeout_seconds",
    }
    nuget = next(section for section in network["sections"] if section["title"] == "NuGet")
    assert {field["field_name"] for field in nuget["fields"]} == {
        "nuget_relay_host",
        "nuget_relay_port",
        "nuget_azure_feed_base_url",
        "nuget_azure_pat_env",
        "nuget_public_feed_base_url",
        "nuget_relay_timeout_seconds",
    }

    automation = next(tab for tab in payload["tabs"] if tab["key"] == "automation_assignment")
    assert {section["title"] for section in automation["sections"]} == {"Capacity", "Roles"}
    advanced = next(tab for tab in payload["tabs"] if tab["key"] == "advanced_raw")
    provider_state = next(
        section for section in advanced["sections"] if section["title"] == "Provider state"
    )
    assert {field["field_name"] for field in provider_state["fields"]} == {"provider_state"}
    assert "people_communications" in payload["tab_snapshots"]
    assert payload["selected_directory"]["path"] == "root"


def test_admin_web_model_pricing_catalog_status_uses_default_catalog(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("EVANDER_MODEL_PRICING_PATH", raising=False)
    catalog_path = seed_default_model_pricing_catalog(tmp_path / "runtime")
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))

    response = client.get("/api/configuration/model-pricing")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["path"] == str(catalog_path)
    assert payload["path_source"] == "default"
    assert isinstance(payload["version"], str)
    assert payload["version"]
    assert payload["currency"] == "usd"
    assert payload["rule_count"] >= 1
    assert payload["sources"]
    assert isinstance(payload["revision"], str)
    assert payload["catalog"]["version"] == payload["version"]
    assert payload["catalog"]["currency"] == "usd"
    assert payload["editable"] is True
    assert payload["write_requires_privileged_unlock"] is True
    first_rule = payload["rules"][0]
    assert isinstance(first_rule["id"], str)
    assert first_rule["id"]
    assert isinstance(first_rule["provider"], str)
    assert first_rule["provider"]
    assert first_rule["rates_per_1m_tokens"]["input"]
    assert payload["catalog"]["rules"][0]["id"] == first_rule["id"]


def test_admin_web_model_pricing_catalog_status_reports_env_override(
    tmp_path: Path, monkeypatch
) -> None:
    catalog_path = tmp_path / "runtime" / "pricing.json"
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    catalog_path.write_text(
        json.dumps(_pricing_catalog_payload()),
        encoding="utf-8",
    )
    monkeypatch.setenv("EVANDER_MODEL_PRICING_PATH", str(catalog_path))
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))

    response = client.get("/api/configuration/model-pricing")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["path"] == str(catalog_path)
    assert payload["path_source"] == "env"
    assert payload["version"] == "test-pricing"
    assert payload["rules"][0]["models"] == ["test-model"]
    assert payload["catalog"]["rules"][0]["models"] == ["test-model"]
    assert payload["catalog"]["rules"][0]["model_patterns"] == []
    assert payload["editable"] is True
    assert payload["write_requires_privileged_unlock"] is True


def test_admin_web_model_pricing_catalog_status_reports_invalid_catalog(
    tmp_path: Path, monkeypatch
) -> None:
    catalog_path = tmp_path / "runtime" / "pricing.json"
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    catalog_path.write_text("not-json", encoding="utf-8")
    monkeypatch.setenv("EVANDER_MODEL_PRICING_PATH", str(catalog_path))
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))

    response = client.get("/api/configuration/model-pricing")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "error"
    assert payload["path"] == str(catalog_path)
    assert payload["path_source"] == "env"
    assert payload["error"]
    assert payload["error_class"]
    assert isinstance(payload["revision"], str)
    assert payload["catalog"] == {
        "version": "",
        "currency": "usd",
        "description": None,
        "sources": [],
        "rules": [],
    }
    assert payload["editable"] is True
    assert payload["write_requires_privileged_unlock"] is True


def test_admin_web_model_pricing_catalog_write_requires_revision(
    tmp_path: Path, monkeypatch
) -> None:
    catalog_path = tmp_path / "runtime" / "pricing.json"
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    catalog_path.write_text(
        json.dumps(_pricing_catalog_payload(version="initial-pricing", model="initial-model")),
        encoding="utf-8",
    )
    monkeypatch.setenv("EVANDER_MODEL_PRICING_PATH", str(catalog_path))
    clear_model_pricing_catalog_cache()
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))
    _unlock_privileged_operations(client)

    initial_response = client.get("/api/configuration/model-pricing")
    assert initial_response.status_code == 200
    initial_revision = initial_response.json()["revision"]
    updated_catalog = _pricing_catalog_payload(version="updated-pricing", model="updated-model")

    missing_revision = client.put(
        "/api/configuration/model-pricing", json={"catalog": updated_catalog}
    )
    stale = client.put(
        "/api/configuration/model-pricing",
        json={"catalog": updated_catalog, "expected_revision": "stale"},
    )
    saved = client.put(
        "/api/configuration/model-pricing",
        json={"catalog": updated_catalog, "expected_revision": initial_revision},
    )

    assert missing_revision.status_code == 428
    assert stale.status_code == 409
    assert stale.json()["details"]["current_revision"] == initial_revision
    assert saved.status_code == 200
    payload = saved.json()
    assert payload["status"] == "ok"
    assert payload["path"] == str(catalog_path)
    assert payload["path_source"] == "env"
    assert payload["version"] == "updated-pricing"
    assert payload["catalog"]["rules"][0]["models"] == ["updated-model"]
    assert payload["revision"] != initial_revision
    assert json.loads(catalog_path.read_text(encoding="utf-8")) == payload["catalog"]


def test_admin_web_model_pricing_catalog_write_rejects_invalid_catalog(
    tmp_path: Path, monkeypatch
) -> None:
    catalog_path = tmp_path / "runtime" / "pricing.json"
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    initial_catalog = _pricing_catalog_payload(version="initial-pricing", model="initial-model")
    catalog_path.write_text(json.dumps(initial_catalog), encoding="utf-8")
    monkeypatch.setenv("EVANDER_MODEL_PRICING_PATH", str(catalog_path))
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))
    _unlock_privileged_operations(client)
    initial_revision = client.get("/api/configuration/model-pricing").json()["revision"]
    invalid_catalog = _pricing_catalog_payload(version="invalid-pricing")
    invalid_catalog["rules"] = []

    response = client.put(
        "/api/configuration/model-pricing",
        json={"catalog": invalid_catalog, "expected_revision": initial_revision},
    )

    assert response.status_code == 400
    assert "non-empty rules array" in response.json()["error"]
    assert json.loads(catalog_path.read_text(encoding="utf-8")) == initial_catalog


def test_admin_web_model_pricing_catalog_write_creates_missing_env_catalog(
    tmp_path: Path, monkeypatch
) -> None:
    catalog_path = tmp_path / "runtime" / "pricing.json"
    monkeypatch.setenv("EVANDER_MODEL_PRICING_PATH", str(catalog_path))
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))
    missing_response = client.get("/api/configuration/model-pricing")
    assert missing_response.status_code == 200
    assert missing_response.json()["status"] == "error"
    assert missing_response.json()["editable"] is True
    _unlock_privileged_operations(client)

    response = client.put(
        "/api/configuration/model-pricing",
        json={
            "catalog": _pricing_catalog_payload(version="created-pricing", model="created-model"),
            "expected_revision": missing_response.json()["revision"],
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["version"] == "created-pricing"
    assert catalog_path.exists()
    assert json.loads(catalog_path.read_text(encoding="utf-8")) == response.json()["catalog"]


def test_admin_web_model_pricing_catalog_write_uses_default_catalog_path(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("EVANDER_MODEL_PRICING_PATH", raising=False)
    catalog_path = tmp_path / "runtime" / "model-pricing.openai.json"
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    catalog_path.write_text(
        json.dumps(_pricing_catalog_payload(version="initial-pricing", model="initial-model")),
        encoding="utf-8",
    )
    clear_model_pricing_catalog_cache()
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))
    initial_response = client.get("/api/configuration/model-pricing")
    assert initial_response.status_code == 200
    assert initial_response.json()["path"] == str(catalog_path)
    assert initial_response.json()["path_source"] == "default"
    _unlock_privileged_operations(client)

    response = client.put(
        "/api/configuration/model-pricing",
        json={
            "catalog": _pricing_catalog_payload(version="updated-pricing", model="updated-model"),
            "expected_revision": initial_response.json()["revision"],
        },
    )

    assert response.status_code == 200
    assert response.json()["version"] == "updated-pricing"
    assert json.loads(catalog_path.read_text(encoding="utf-8")) == response.json()["catalog"]
    clear_model_pricing_catalog_cache()


def test_admin_web_model_pricing_catalog_write_invalidates_cached_default_catalog(
    tmp_path: Path, monkeypatch
) -> None:
    catalog_path = tmp_path / "runtime" / "pricing.json"
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    catalog_path.write_text(
        json.dumps(_pricing_catalog_payload(version="initial-pricing", model="initial-model")),
        encoding="utf-8",
    )
    monkeypatch.setenv("EVANDER_MODEL_PRICING_PATH", str(catalog_path))
    clear_model_pricing_catalog_cache()
    assert default_model_pricing_catalog().version == "initial-pricing"
    client = TestClient(create_app(build_admin_runtime(write_config(tmp_path))))
    _unlock_privileged_operations(client)
    initial_revision = client.get("/api/configuration/model-pricing").json()["revision"]

    response = client.put(
        "/api/configuration/model-pricing",
        json={
            "catalog": _pricing_catalog_payload(version="updated-pricing", model="updated-model"),
            "expected_revision": initial_revision,
        },
    )

    assert response.status_code == 200
    assert default_model_pricing_catalog().version == "updated-pricing"
    clear_model_pricing_catalog_cache()


def test_admin_web_people_communications_tab_save_preserves_user_contact_links(
    tmp_path: Path,
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    service.operations_admin.write_directory_domain_config(
        "root",
        "contacts",
        {
            "contacts": {
                "ops": {
                    "name": "Ops Duty",
                    "phone_numbers": [
                        {"label": "mobile", "number": "+15550000002", "primary": True}
                    ],
                }
            },
            "user_contact_links": {"operator@example.com": "ops"},
        },
    )
    client = TestClient(create_app(service))
    _unlock_privileged_operations(client)

    response = client.put(
        "/api/configuration/domains/people_communications?directory_path=root",
        json={
            "config": {
                "contacts": {
                    "ops": {
                        "name": "Ops Duty",
                        "phone_numbers": [
                            {
                                "label": "mobile",
                                "number": "+15550000002",
                                "primary": True,
                            }
                        ],
                    },
                    "support": {
                        "name": "Support",
                        "email_addresses": ["support@example.com"],
                        "phone_numbers": [],
                    },
                }
            },
            "expected_revision": _configuration_expected_revision(
                client, "root", "people_communications"
            ),
        },
    )

    assert response.status_code == 200
    saved = service.operations_admin.read_directory_domain_config("root", "contacts")
    assert saved.ok
    assert saved.data.config["contacts"]["support"]["name"] == "Support"
    assert saved.data.config["user_contact_links"] == {"operator@example.com": "ops"}


def test_admin_web_configuration_state_exposes_unified_directory_overrides(
    tmp_path: Path,
) -> None:
    client = TestClient(
        create_app(build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True)))
    )
    _unlock_privileged_operations(client)
    create_response = client.post(
        "/api/configuration/directories",
        json={"parent_path": "root", "name": "ops"},
    )
    assert create_response.status_code == 200

    communications_response = client.put(
        "/api/configuration/domains/people_communications?directory_path=root/ops",
        json={
            "config": {
                "discord_backend": "stub",
                "discord_operator_channel_allowlist": ["chan-1"],
                "discord_resume_prompt": "Resume this session.",
            },
            "expected_revision": _configuration_expected_revision(
                client, "root/ops", "people_communications"
            ),
        },
    )
    code_response = client.put(
        "/api/configuration/domains/code_issues_prs?directory_path=root/ops",
        json={
            "config": {"github_token_env": "GITHUB_TOKEN_OPS"},
            "expected_revision": _configuration_expected_revision(
                client, "root/ops", "code_issues_prs"
            ),
        },
    )
    state_response = client.get("/api/configuration/state?directory_path=root/ops")

    assert communications_response.status_code == 200
    assert code_response.status_code == 200
    assert state_response.status_code == 200
    overrides = state_response.json()["directory_overrides"]
    assert overrides["directory_path"] == "root/ops"
    assert overrides["exists"] is True
    assert overrides["domain_count"] == 3
    assert overrides["field_count"] == 4
    assert overrides["payload"] == {
        "directory_path": "root/ops",
        "overrides": {
            "wake": {"discord_resume_prompt": "Resume this session."},
            "policy": {"discord_operator_channel_allowlist": ["chan-1"]},
            "providers": {
                "discord_backend": "stub",
                "github_token_env": "GITHUB_TOKEN_OPS",
            },
        },
    }
    assert "assistant_settings" not in overrides["overrides"]


def test_admin_web_people_communications_tab_save_splits_underlying_domains(
    tmp_path: Path,
) -> None:
    client = TestClient(
        create_app(build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True)))
    )
    _unlock_privileged_operations(client)

    create_response = client.post(
        "/api/configuration/directories",
        json={"parent_path": "root", "name": "ops"},
    )
    assert create_response.status_code == 200

    response = client.put(
        "/api/configuration/domains/people_communications?directory_path=ops",
        json={
            "config": {
                "discord_backend": "stub",
                "discord_operator_channel_allowlist": ["chan-1", "chan-2"],
                "discord_resume_prompt": "Resume this session.",
            },
            "expected_revision": _configuration_expected_revision(
                client, "ops", "people_communications"
            ),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["write_result"]["ok"] is True
    snapshot = payload["tab_snapshot"]
    assert snapshot["raw"]["config"]["discord_backend"] == "stub"
    assert snapshot["raw"]["config"]["discord_operator_channel_allowlist"] == ["chan-1", "chan-2"]
    assert snapshot["raw"]["config"]["discord_resume_prompt"] == "Resume this session."


def test_admin_web_people_communications_saves_communication_artifact_link_fields(
    tmp_path: Path,
) -> None:
    client = TestClient(
        create_app(build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True)))
    )
    _unlock_privileged_operations(client)

    response = client.put(
        "/api/configuration/domains/people_communications?directory_path=root",
        json={
            "config": {
                "communication_artifact_public_base_url": "https://admin.example.test",
                "communication_artifact_link_secret_env": "TEAM_ARTIFACT_LINK_SECRET",
                "communication_artifact_link_ttl_seconds": 3600,
            },
            "expected_revision": _configuration_expected_revision(
                client, "root", "people_communications"
            ),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["write_result"]["ok"] is True
    provider_config = payload["tab_snapshot"]["raw"]["config"]
    assert provider_config["communication_artifact_public_base_url"] == (
        "https://admin.example.test"
    )
    assert provider_config["communication_artifact_link_secret_env"] == (
        "TEAM_ARTIFACT_LINK_SECRET"
    )
    assert provider_config["communication_artifact_link_ttl_seconds"] == 3600


def test_admin_web_people_communications_tab_save_rolls_back_on_later_domain_failure(
    tmp_path: Path,
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    client = TestClient(create_app(service))
    _unlock_privileged_operations(client)
    providers_before = service.operations_admin.read_directory_domain_config("root", "providers")
    assert providers_before.ok is True
    assert providers_before.data is not None

    response = client.put(
        "/api/configuration/domains/people_communications?directory_path=root",
        json={
            "config": {
                "contacts": {
                    "ops": {
                        "name": "Ops Duty",
                        "email_addresses": ["ops@example.com"],
                        "phone_numbers": [],
                    }
                },
                "communication_artifact_link_ttl_seconds": 0,
            },
            "expected_revision": _configuration_expected_revision(
                client, "root", "people_communications"
            ),
        },
    )

    assert response.status_code == 400
    assert response.json()["source_domain"] == "providers"
    assert "rollback_errors" not in response.json()
    contacts = service.operations_admin.read_directory_domain_config("root", "contacts")
    providers = service.operations_admin.read_directory_domain_config("root", "providers")
    assert contacts.ok is True
    assert contacts.data is not None
    assert contacts.data.exists is False
    assert contacts.data.config is None
    assert providers.ok is True
    assert providers.data is not None
    assert providers.data.config == providers_before.data.config


def test_admin_web_configuration_tab_save_removed_fields_clears_child_domain_override(
    tmp_path: Path,
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    client = TestClient(create_app(service))
    _unlock_privileged_operations(client)
    target_payload = [
        {
            "id": "ops-notify",
            "provider": "teams",
            "kind": "teams",
            "address": "team-id/channel-id",
            "policy": {"capabilities": ["notify"]},
        }
    ]

    create_response = client.post(
        "/api/configuration/directories",
        json={"parent_path": "root", "name": "ops"},
    )
    assert create_response.status_code == 200
    root_write = service.operations_admin.write_directory_domain_config(
        "root",
        "providers",
        {"teams_delegated_scopes": ["Chat.ReadWrite"]},
    )
    child_provider_write = service.operations_admin.write_directory_domain_config(
        "root/ops",
        "providers",
        {"teams_delegated_scopes": ["ChannelMessage.Read.All"]},
    )
    child_target_write = service.operations_admin.write_directory_domain_config(
        "root/ops",
        COMMUNICATION_TARGETS_DOMAIN,
        {"targets": target_payload},
    )
    assert root_write.ok is True
    assert child_provider_write.ok is True
    assert child_target_write.ok is True

    response = client.put(
        "/api/configuration/domains/people_communications?directory_path=root/ops",
        json={
            "config": {"targets": target_payload},
            "removed_fields": ["teams_delegated_scopes"],
            "expected_revision": _configuration_expected_revision(
                client, "root/ops", "people_communications"
            ),
        },
    )

    assert response.status_code == 200
    child_providers = service.operations_admin.read_directory_domain_config("root/ops", "providers")
    assert child_providers.ok is True
    assert child_providers.data is not None
    assert child_providers.data.exists is False
    assert child_providers.data.config is None
    effective_providers = service.operations_admin.compute_directory_effective_config(
        "root/ops", "providers"
    )
    assert effective_providers.ok is True
    assert effective_providers.data is not None
    assert effective_providers.data.resolution.effective_config["teams_delegated_scopes"] == [
        "Chat.ReadWrite"
    ]
    targets = service.operations_admin.read_directory_domain_config(
        "root/ops", COMMUNICATION_TARGETS_DOMAIN
    )
    assert targets.ok is True
    assert targets.data is not None
    assert targets.data.config is not None
    assert targets.data.config["targets"][0]["id"] == "ops-notify"
    assert targets.data.config["targets"][0]["policy"]["capabilities"] == ["notify"]


def test_admin_web_configuration_tab_save_rejects_missing_and_stale_revision(
    tmp_path: Path,
) -> None:
    client = TestClient(
        create_app(build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True)))
    )
    _unlock_privileged_operations(client)
    create_response = client.post(
        "/api/configuration/directories",
        json={"parent_path": "root", "name": "ops"},
    )
    assert create_response.status_code == 200
    initial_revision = _configuration_expected_revision(client, "ops", "people_communications")

    missing = client.put(
        "/api/configuration/domains/people_communications?directory_path=ops",
        json={"config": {"discord_backend": "stub"}},
    )
    first_save = client.put(
        "/api/configuration/domains/people_communications?directory_path=ops",
        json={
            "config": {"discord_backend": "stub"},
            "expected_revision": initial_revision,
        },
    )
    stale = client.put(
        "/api/configuration/domains/people_communications?directory_path=ops",
        json={
            "config": {"discord_operator_channel_allowlist": ["chan-2"]},
            "expected_revision": initial_revision,
        },
    )

    assert missing.status_code == 428
    assert first_save.status_code == 200
    assert first_save.json()["tab_snapshot"]["revision"] != initial_revision
    assert stale.status_code == 409
    assert (
        stale.json()["details"]["current_revision"] == first_save.json()["tab_snapshot"]["revision"]
    )


def test_admin_web_people_communications_tab_saves_sqlite_communication_targets(
    tmp_path: Path,
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    client = TestClient(create_app(service))
    _unlock_privileged_operations(client)

    root_response = client.put(
        "/api/configuration/domains/people_communications?directory_path=root",
        json={
            "config": {
                "targets": [
                    {
                        "id": "teams-primary",
                        "provider": "teams",
                        "kind": "teams",
                        "address": "team-a/channel-1",
                        "policy": {"capabilities": ["notify"]},
                    }
                ]
            },
            "expected_revision": _configuration_expected_revision(
                client, "root", "people_communications"
            ),
        },
    )
    create_response = client.post(
        "/api/configuration/directories",
        json={"parent_path": "root", "name": "ops"},
    )
    child_response = client.put(
        "/api/configuration/domains/people_communications?directory_path=root/ops",
        json={
            "config": {
                "targets": [
                    {"id": "teams-primary", "disabled": True},
                    {
                        "id": "discord-ops",
                        "provider": "discord",
                        "kind": "discord",
                        "address": "123456789012345678",
                        "policy": {"capabilities": ["write"]},
                    },
                ]
            },
            "expected_revision": _configuration_expected_revision(
                client, "root/ops", "people_communications"
            ),
        },
    )
    state_response = client.get("/api/configuration/state?directory_path=root/ops")

    assert root_response.status_code == 200
    assert create_response.status_code == 200
    assert child_response.status_code == 200
    payload = state_response.json()
    communications = payload["tab_snapshots"]["people_communications"]
    assert communications["raw"]["config"]["targets"][0] == {
        "id": "discord-ops",
        "provider": "discord",
        "kind": "discord",
        "address": "123456789012345678",
        "policy": {"capabilities": ["write"], "auth_profile_ref": None},
    }
    assert communications["raw"]["config"]["targets"][1] == {
        "id": "teams-primary",
        "disabled": True,
    }
    assert communications["parent_defaults"]["config"]["targets"][0]["id"] == "teams-primary"
    assert communications["effective"]["config"]["targets"][0]["id"] == "discord-ops"
    assert payload["directory_overrides"]["overrides"]["communication_targets"]["targets"][1] == {
        "id": "teams-primary",
        "disabled": True,
    }


def test_admin_web_models_assistant_knowledge_tab_saves_assistant_settings(
    tmp_path: Path,
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    client = TestClient(create_app(service))

    unlock = client.post("/api/admin/privileged/unlock")
    assert unlock.status_code == 200

    response = client.put(
        "/api/configuration/domains/models_assistant_knowledge?directory_path=root",
        json={
            "config": {
                "enabled": True,
                "show_launcher_when_disabled": True,
                "read_only": False,
                "default_model": "gpt-test",
                "default_profile": "full-admin",
                "custom_system_instructions": "Use the admin context.",
            },
            "expected_revision": _configuration_expected_revision(
                client, "root", "models_assistant_knowledge"
            ),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    snapshot = payload["tab_snapshot"]
    assert snapshot["raw"]["config"]["enabled"] is True
    assert snapshot["raw"]["config"]["read_only"] is False
    assert snapshot["raw"]["config"]["custom_system_instructions"] == "Use the admin context."
    assert service.operations_admin.read_assistant_settings() == {
        "custom_system_instructions": "Use the admin context.",
        "default_model": "gpt-test",
        "default_profile": "full-admin",
        "enabled": True,
        "read_only": False,
        "show_launcher_when_disabled": True,
    }
    assert service.operations_admin.assistant_settings_path().exists()


def test_admin_web_models_assistant_knowledge_tab_save_preserves_directory_domains(
    tmp_path: Path,
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    client = TestClient(create_app(service))
    _unlock_privileged_operations(client)

    create_response = client.post(
        "/api/configuration/directories",
        json={"parent_path": "root", "name": "ops"},
    )
    assert create_response.status_code == 200

    communications_response = client.put(
        "/api/configuration/domains/people_communications?directory_path=ops",
        json={
            "config": {
                "discord_backend": "stub",
                "discord_operator_channel_allowlist": ["chan-1"],
                "discord_resume_prompt": "Resume this session.",
            },
            "expected_revision": _configuration_expected_revision(
                client, "ops", "people_communications"
            ),
        },
    )
    assert communications_response.status_code == 200

    unlock = client.post("/api/admin/privileged/unlock")
    assert unlock.status_code == 200

    assistant_response = client.put(
        "/api/configuration/domains/models_assistant_knowledge?directory_path=ops",
        json={
            "config": {"enabled": True, "read_only": True, "default_model": "gpt-test"},
            "expected_revision": _configuration_expected_revision(
                client, "ops", "models_assistant_knowledge"
            ),
        },
    )
    assert assistant_response.status_code == 200

    providers_result = service.operations_admin.read_directory_domain_config("ops", "providers")
    policy_result = service.operations_admin.read_directory_domain_config("ops", "policy")
    wake_result = service.operations_admin.read_directory_domain_config("ops", "wake")
    assert providers_result.ok is True
    assert policy_result.ok is True
    assert wake_result.ok is True
    assert providers_result.data is not None
    assert policy_result.data is not None
    assert wake_result.data is not None
    assert providers_result.data.config["discord_backend"] == "stub"
    assert policy_result.data.config["discord_operator_channel_allowlist"] == ["chan-1"]
    assert wake_result.data.config["discord_resume_prompt"] == "Resume this session."
    assert service.operations_admin.read_assistant_settings()["default_model"] == "gpt-test"


def test_admin_web_assistant_settings_migrates_legacy_write_tool_profile(
    tmp_path: Path,
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    path = service.operations_admin.assistant_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "enabled": True,
                "read_only": False,
                "allowed_mcp_tool_profile": "full-admin",
            }
        ),
        encoding="utf-8",
    )

    settings = service.operations_admin.read_assistant_settings()

    assert settings["default_profile"] == "full-admin"
    assert "allowed_mcp_tool_profile" not in settings


def test_admin_web_people_communications_tab_preserves_assistant_settings_when_absent(
    tmp_path: Path,
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    service.operations_admin.write_assistant_settings(
        {
            "enabled": True,
            "show_launcher_when_disabled": True,
            "read_only": False,
            "default_model": "gpt-existing",
        }
    )
    client = TestClient(create_app(service))
    _unlock_privileged_operations(client)

    response = client.put(
        "/api/configuration/domains/people_communications?directory_path=root",
        json={
            "config": {"discord_backend": "stub"},
            "expected_revision": _configuration_expected_revision(
                client, "root", "people_communications"
            ),
        },
    )

    assert response.status_code == 200
    assert service.operations_admin.read_assistant_settings()["default_model"] == "gpt-existing"
    assert service.operations_admin.read_assistant_settings()["enabled"] is True


def test_admin_web_chat_settings_write_requires_authentication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AUTH_MODE", "local_password")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_USERNAME", "operator")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_PASSWORD", "swordfish")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_SESSION_SECRET", "test-secret")
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    client = TestClient(create_app(service))

    unauthenticated = client.put(
        "/api/chat/settings",
        json={"enabled": True, "read_only": False, "default_profile": "full-admin"},
    )
    assert unauthenticated.status_code == 401
    assert service.operations_admin.read_assistant_settings()["enabled"] is False

    login = client.post(
        "/api/auth/login",
        json={"username": "operator", "password": "swordfish"},
    )
    assert login.status_code == 200
    locked = client.put(
        "/api/chat/settings",
        json={"enabled": True, "read_only": False, "default_profile": "full-admin"},
    )
    assert locked.status_code == 403
    _unlock_privileged_operations(client)
    saved = client.put(
        "/api/chat/settings",
        json={"enabled": True, "read_only": False, "default_profile": "full-admin"},
    )
    assert saved.status_code == 200
    assert service.operations_admin.read_assistant_settings()["default_profile"] == "full-admin"


def test_admin_web_grouped_tab_save_preserves_unrelated_shared_domain_fields(
    tmp_path: Path,
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    client = TestClient(create_app(service))
    _unlock_privileged_operations(client)

    create_response = client.post(
        "/api/configuration/directories",
        json={"parent_path": "root", "name": "ops"},
    )
    assert create_response.status_code == 200

    provider_response = client.put(
        "/api/configuration/domains/providers?directory_path=ops",
        json={
            "config": {"openai_api_base_url": "http://relay.internal/v1"},
            "expected_revision": _configuration_expected_revision(client, "ops", "providers"),
        },
    )
    assert provider_response.status_code == 200

    communications_response = client.put(
        "/api/configuration/domains/people_communications?directory_path=ops",
        json={
            "config": {"discord_backend": "stub"},
            "expected_revision": _configuration_expected_revision(
                client, "ops", "people_communications"
            ),
        },
    )
    assert communications_response.status_code == 200

    providers_result = service.operations_admin.read_directory_domain_config("ops", "providers")
    assert providers_result.ok is True
    assert providers_result.data is not None
    assert providers_result.data.config == {
        "discord_backend": "stub",
        "openai_api_base_url": "http://relay.internal/v1",
    }

    clear_response = client.put(
        "/api/configuration/domains/people_communications?directory_path=ops",
        json={
            "config": {},
            "removed_fields": ["discord_backend"],
            "expected_revision": _configuration_expected_revision(
                client, "ops", "people_communications"
            ),
        },
    )
    assert clear_response.status_code == 200

    providers_result = service.operations_admin.read_directory_domain_config("ops", "providers")
    assert providers_result.ok is True
    assert providers_result.data is not None
    assert providers_result.data.config == {"openai_api_base_url": "http://relay.internal/v1"}


def test_admin_web_models_tab_save_preserves_other_provider_area_fields(
    tmp_path: Path,
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    service.operations_admin.write_directory_domain_config(
        "root",
        "providers",
        {
            "discord_backend": "stub",
            "execution_backend": "container_worker_vm",
            "github_token_env": "GITHUB_TOKEN_OPS",
            "nuget_public_feed_base_url": "https://nuget.example/v3/index.json",
            "openai_api_base_url": "http://old-relay.internal/v1",
        },
    )
    client = TestClient(create_app(service))
    _unlock_privileged_operations(client)

    response = client.put(
        "/api/configuration/domains/models_assistant_knowledge?directory_path=root",
        json={
            "config": {"model_provider_mode": "local_openai"},
            "expected_revision": _configuration_expected_revision(
                client, "root", "models_assistant_knowledge"
            ),
        },
    )

    assert response.status_code == 200
    providers_result = service.operations_admin.read_directory_domain_config("root", "providers")
    assert providers_result.ok is True
    assert providers_result.data is not None
    assert providers_result.data.config == {
        "discord_backend": "stub",
        "execution_backend": "container_worker_vm",
        "github_token_env": "GITHUB_TOKEN_OPS",
        "model_provider_mode": "local_openai",
        "nuget_public_feed_base_url": "https://nuget.example/v3/index.json",
    }


def test_admin_web_can_toggle_privileged_operations_state(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))

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


def test_request_has_privileged_operations_requires_unlock_state() -> None:
    calls: list[str] = []

    service = SimpleNamespace(operations_admin=SimpleNamespace())
    locked_auth = SimpleNamespace(
        require=lambda _request: calls.append("require") or {"sid": "test"},
        privileged_for_request=lambda _request: calls.append("privileged_for_request") or False,
    )
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})

    assert (
        request_has_privileged_operations(auth=locked_auth, service=service, request=request)
        is False
    )
    assert calls == ["require", "privileged_for_request"]

    unlocked_auth = SimpleNamespace(
        require=lambda _request: {"sid": "test"},
        privileged_for_request=lambda _request: True,
    )
    assert (
        request_has_privileged_operations(auth=unlocked_auth, service=service, request=request)
        is True
    )


def test_helper_gated_route_requires_unlock_in_local_dev_auth_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AUTH_MODE", "none")
    service = build_admin_runtime(write_config(tmp_path))
    client = TestClient(create_app(service))

    locked_response = client.put(
        "/api/chat/settings",
        json={"enabled": True, "read_only": False, "default_profile": "full-admin"},
    )
    unlock_response = client.post("/api/admin/privileged/unlock")
    unlocked_response = client.put(
        "/api/chat/settings",
        json={"enabled": True, "read_only": False, "default_profile": "full-admin"},
    )

    assert locked_response.status_code == 403
    assert locked_response.json()["error"] == "Privileged operations are locked."
    assert unlock_response.status_code == 200
    assert unlock_response.json()["privileged_operations"]["unlocked"] is True
    assert unlocked_response.status_code == 200
    assert unlocked_response.json()["enabled"] is True
    assert unlocked_response.json()["read_only"] is False
    assert unlocked_response.json()["default_profile"] == "full-admin"


def test_request_has_privileged_operations_can_require_permission() -> None:
    calls: list[tuple[str, object, object | None]] = []

    def require_permission(_request, permission, directory_path=None):
        calls.append(("require_permission", permission, directory_path))

    service = SimpleNamespace(operations_admin=SimpleNamespace())
    auth = SimpleNamespace(
        require_permission=require_permission,
        privileged_for_request=lambda _request: True,
    )
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})

    assert (
        request_has_privileged_operations(
            auth=auth,
            service=service,
            request=request,
            required_permission=ADMIN_OWNER_PERMISSION,
            directory_path="root/team-a",
        )
        is True
    )
    assert calls == [("require_permission", ADMIN_OWNER_PERMISSION, "root/team-a")]

    def deny_permission(_request, _permission, directory_path=None):
        raise PermissionError("denied")

    denied_auth = SimpleNamespace(
        require_permission=deny_permission,
        privileged_for_request=lambda _request: True,
    )
    assert (
        request_has_privileged_operations(
            auth=denied_auth,
            service=service,
            request=request,
            required_permission=ADMIN_OWNER_PERMISSION,
        )
        is False
    )

    minimal_auth = SimpleNamespace(
        require=lambda _request: {"sid": "test"},
        privileged_for_request=lambda _request: True,
    )
    assert (
        request_has_privileged_operations(
            auth=minimal_auth,
            service=service,
            request=request,
            required_permission=ADMIN_OWNER_PERMISSION,
        )
        is False
    )


def test_admin_web_global_policy_writes_require_admin_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EVANDER_ADMIN_WEB_USERNAME", "admin")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_PASSWORD", "admin-pass")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_SESSION_SECRET", "test-secret")
    catalog_path = tmp_path / "runtime" / "pricing.json"
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    initial_catalog = _pricing_catalog_payload(version="initial-pricing", model="initial-model")
    catalog_path.write_text(json.dumps(initial_catalog), encoding="utf-8")
    monkeypatch.setenv("EVANDER_MODEL_PRICING_PATH", str(catalog_path))
    clear_model_pricing_catalog_cache()
    app = create_app(build_admin_runtime(write_config(tmp_path)))
    admin_client = TestClient(app)
    regular_client = TestClient(app)
    assert (
        admin_client.post(
            "/api/auth/login", json={"username": "admin", "password": "admin-pass"}
        ).status_code
        == 200
    )
    created = admin_client.post(
        "/api/admin/users",
        json={
            "login": "operator@example.com",
            "role": "regular",
            "password": "operator-pass",
        },
    )
    assert created.status_code == 200
    assert (
        regular_client.post(
            "/api/auth/login",
            json={"username": "operator@example.com", "password": "operator-pass"},
        ).status_code
        == 200
    )

    pricing_revision = regular_client.get("/api/configuration/model-pricing").json()["revision"]
    pricing_response = regular_client.put(
        "/api/configuration/model-pricing",
        json={
            "catalog": _pricing_catalog_payload(version="updated-pricing", model="updated-model"),
            "expected_revision": pricing_revision,
        },
    )
    chat_response = regular_client.put(
        "/api/chat/settings",
        json={"enabled": True, "read_only": False, "default_profile": "full-admin"},
    )
    mcp_tools = regular_client.get("/api/admin/mcp-tools?directory_path=root").json()
    mcp_response = regular_client.put(
        "/api/admin/mcp-tools?directory_path=root",
        json={"expected_revision": mcp_tools["revision"], "mcp_tool_policies": {}},
    )

    assert pricing_response.status_code == 403
    assert chat_response.status_code == 403
    assert mcp_response.status_code == 403
    assert json.loads(catalog_path.read_text(encoding="utf-8")) == initial_catalog
    clear_model_pricing_catalog_cache()


def test_admin_web_unlock_endpoint_does_not_start_privileged_helper(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))

    def fail_privileged_helper_call(self):
        raise AssertionError("web lock endpoints should not start or inspect the privileged helper")

    for method_name in (
        "unlock_privileged_operations",
        "lock_privileged_operations",
        "privileged_operations_unlocked",
    ):
        monkeypatch.setattr(
            service.operations_admin,
            method_name,
            MethodType(fail_privileged_helper_call, service.operations_admin),
        )

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


_ADMIN_AGENT_MUTATION_CASES = (
    (
        "create",
        "POST",
        "/api/admin/agents",
        {"backend": "container_worker_vm", "session_id": "session-1"},
        201,
    ),
    (
        "submit-create-operation",
        "POST",
        "/api/admin/agents/create-operations",
        {"backend": "container_worker_vm", "session_id": "session-1"},
        202,
    ),
    (
        "patch",
        "PATCH",
        "/api/admin/agents/session-1?backend=libvirt_vm",
        {"model": "gpt-5"},
        200,
    ),
    ("delete", "DELETE", "/api/admin/agents/session-1?backend=libvirt_vm", None, 200),
    ("action", "POST", "/api/admin/agents/vm-1/actions/start?backend=libvirt_vm", {}, 200),
    (
        "prompt",
        "POST",
        "/api/admin/agents/vm-1/actions/prompt-agent?backend=libvirt_vm",
        {"prompt": "Continue"},
        200,
    ),
)


_ADMIN_VM_ISSUE_MUTATION_CASES = (
    ("vm-create", "POST", "/api/admin/vms", {"vm_name": "vm-1"}, 201),
    ("vm-action", "POST", "/api/admin/vms/vm-1/actions/start", {}, 200),
    ("issue-create", "POST", "/api/admin/issues", {"title": "Issue", "body": "Body"}, 201),
    (
        "issue-create-start-vm",
        "POST",
        "/api/admin/issues",
        {"title": "Issue", "body": "Body", "start_vm": True},
        201,
    ),
    (
        "issue-update",
        "PUT",
        "/api/admin/issues/issue-1",
        {"title": "Updated", "body": "Body", "state": "New"},
        200,
    ),
    ("issue-delete", "DELETE", "/api/admin/issues/issue-1", None, 200),
    ("issue-create-vm", "POST", "/api/admin/issues/issue-1/create-vm", {}, 200),
)


def _admin_route_issue_record(
    issue_id: str = "issue-1", *, directory_path: str = "root"
) -> LocalIssueRecord:
    return LocalIssueRecord(
        issue_id=issue_id,
        issue_number="1",
        title="Issue",
        body="Body",
        yaml_path="/tmp/issue.yaml",
        directory_path=directory_path,
    )


def _invoke_admin_mutation(
    client: TestClient,
    *,
    method: str,
    path: str,
    payload: dict[str, object] | None,
) -> httpx.Response:
    request_kwargs: dict[str, object] = {}
    if payload is not None:
        request_kwargs["json"] = payload
    return client.request(method, path, **request_kwargs)


def _assert_admin_mutation_forbidden(response: httpx.Response) -> None:
    assert response.status_code == 403
    assert response.json()["error"] == "Privileged operations are locked."


def _regular_admin_web_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    service: AdminRuntimeComponents,
) -> TestClient:
    store = _admin_web_user_store(tmp_path)
    store.upsert_user(login="owner@example.com", role="admin")
    store.upsert_user(login="regular@example.com", role="regular")
    _set_azure_sso_env(monkeypatch, allowed_users=None)
    app = create_app(service)
    client = TestClient(app)
    callback = _complete_azure_login(
        app, client, login="regular@example.com", display_name="Regular User"
    )
    assert callback.status_code == 303
    return client


def _scoped_owner_admin_web_client(
    monkeypatch: pytest.MonkeyPatch,
    service: AdminRuntimeComponents,
    *,
    directory_path: str = "root/ops",
) -> TestClient:
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AUTH_MODE", "local_password")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_USERNAME", "admin")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_PASSWORD", "admin-pass")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_SESSION_SECRET", "test-secret")
    app = create_app(service)
    store = app.state.admin_user_store
    store.upsert_user(login="ops-owner@example.com", role="regular", display_name="Ops Owner")
    store.remove_user_group_assignment(
        login="ops-owner@example.com", group_id="operator", directory_path="root"
    )
    store.assign_user_group(
        login="ops-owner@example.com", group_id=OWNER_GROUP_ID, directory_path=directory_path
    )
    store.set_local_password(login="ops-owner@example.com", password="ops-pass")
    client = TestClient(app)
    login = client.post(
        "/api/auth/login",
        json={"username": "ops-owner@example.com", "password": "ops-pass"},
    )
    assert login.status_code == 200
    _unlock_privileged_operations(client)
    return client


def _install_admin_agent_mutation_stub(
    service: AdminRuntimeComponents,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    calls: list[str],
    *,
    directory_path: str = "root",
) -> None:
    if case in {"patch", "delete", "action", "prompt"}:
        service.runtime.session_repository.upsert_session(
            SessionManifest(
                session_id="session-1",
                vm_id="vm-1",
                vm_provider="libvirt",
                vm_provider_id="vm-1",
                directory_path=directory_path,
                allowed_tools=[],
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            )
        )

    if case == "create":

        def create_admin_agent(self, payload):
            del self
            calls.append("create_admin_agent")
            return {
                "success": True,
                "agent_id": str(payload.get("session_id") or "session-1"),
                "backend": str(payload.get("backend") or "container_worker_vm"),
                "target_name": "target-1",
                "action": "create",
                "message": "created",
            }

        monkeypatch.setattr(
            service.vm_agent_admin,
            "create_admin_agent",
            MethodType(create_admin_agent, service.vm_agent_admin),
        )
        return
    if case == "submit-create-operation":

        def submit_admin_agent_create(self, payload):
            del self, payload
            calls.append("submit_admin_agent_create")
            return AgentCreateOperation(
                operation_id="operation-1",
                status="queued",
                backend="container_worker_vm",
                target_name="target-1",
                session_id="session-1",
            )

        monkeypatch.setattr(
            service.vm_agent_admin,
            "submit_admin_agent_create",
            MethodType(submit_admin_agent_create, service.vm_agent_admin),
        )
        return
    if case == "patch":

        def update_admin_agent(self, agent_id, *, backend=None, payload=None):
            del self, backend, payload
            calls.append("update_admin_agent")
            return {
                "success": True,
                "agent_id": agent_id,
                "backend": "libvirt_vm",
                "target_name": agent_id,
                "action": "patch",
                "message": "updated",
            }

        monkeypatch.setattr(
            service.vm_agent_admin,
            "update_admin_agent",
            MethodType(update_admin_agent, service.vm_agent_admin),
        )
        return
    if case == "delete":

        def delete_admin_agent(self, agent_id, *, backend=None):
            del self, backend
            calls.append("delete_admin_agent")
            return {
                "success": True,
                "agent_id": agent_id,
                "backend": "libvirt_vm",
                "target_name": agent_id,
                "action": "delete",
                "message": "deleted",
                "status": "deleted",
            }

        monkeypatch.setattr(
            service.vm_agent_admin,
            "delete_admin_agent",
            MethodType(delete_admin_agent, service.vm_agent_admin),
        )
        return

    def run_admin_agent_action(self, agent_id, action, *, backend=None, payload=None):
        del self, payload
        calls.append(f"run_admin_agent_action:{action}")
        return {
            "success": True,
            "agent_id": agent_id,
            "backend": backend or "libvirt_vm",
            "target_name": agent_id,
            "action": action,
            "message": "ran",
        }

    monkeypatch.setattr(
        service.vm_agent_admin,
        "run_admin_agent_action",
        MethodType(run_admin_agent_action, service.vm_agent_admin),
    )


def _install_vm_issue_mutation_stub(
    service: AdminRuntimeComponents,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    calls: list[str],
    *,
    directory_path: str = "root",
) -> None:
    if case in {"issue-update", "issue-delete", "issue-create-vm"}:

        def resolve_local_issue_record(_service, issue_identifier):
            return _admin_route_issue_record(issue_identifier, directory_path=directory_path)

        monkeypatch.setattr(
            vms_issues_module,
            "_resolve_local_issue_record",
            resolve_local_issue_record,
        )

    if case == "vm-create":

        def create_vm(self, **payload):
            del self
            calls.append("create_vm")
            return VmActionResult(
                success=True,
                vm_name=str(payload.get("vm_name") or "vm-1"),
                action="create-vm",
                message="created",
            )

        monkeypatch.setattr(
            service.vm_agent_admin,
            "create_vm",
            MethodType(create_vm, service.vm_agent_admin),
        )
        return
    if case == "vm-action":

        def list_vms(self, **_kwargs):
            del self
            calls.append("list_vms")
            return [
                VmSummary(
                    vm_name="vm-1",
                    issue_number="ISSUE-1",
                    session_ids=[],
                    directory_paths=[directory_path],
                    libvirt_state="stopped",
                    agent_state="idle",
                )
            ]

        def start_vm(self, vm_name):
            del self
            calls.append("start_vm")
            return VmActionResult(
                success=True,
                vm_name=vm_name,
                action="start",
                message="started",
            )

        monkeypatch.setattr(
            service.vm_agent_admin,
            "list_vms",
            MethodType(list_vms, service.vm_agent_admin),
        )
        monkeypatch.setattr(
            service.vm_agent_admin,
            "start_vm",
            MethodType(start_vm, service.vm_agent_admin),
        )
        return
    if case in {"issue-create", "issue-create-start-vm"}:

        def create_local_issue(self, **_payload):
            del self
            calls.append("create_local_issue")
            return _admin_route_issue_record(directory_path=directory_path)

        monkeypatch.setattr(
            service.issue_git_admin,
            "create_local_issue",
            MethodType(create_local_issue, service.issue_git_admin),
        )
        if case == "issue-create-start-vm":

            def create_issue_vm_from_local_issue(self, issue_identifier, **_payload):
                del self
                calls.append(f"create_issue_vm_from_local_issue:{issue_identifier}")
                return VmActionResult(
                    success=True,
                    vm_name="vm-issue-1",
                    action="create-issue-vm",
                    message="created",
                )

            monkeypatch.setattr(
                service.vm_agent_admin,
                "create_issue_vm_from_local_issue",
                MethodType(create_issue_vm_from_local_issue, service.vm_agent_admin),
            )
        return
    if case == "issue-update":

        def update_local_issue(self, issue_identifier, **_payload):
            del self
            calls.append(f"update_local_issue:{issue_identifier}")
            return _admin_route_issue_record(issue_identifier, directory_path=directory_path)

        monkeypatch.setattr(
            service.issue_git_admin,
            "update_local_issue",
            MethodType(update_local_issue, service.issue_git_admin),
        )
        return
    if case == "issue-delete":

        def delete_local_issue(self, issue_identifier):
            del self
            calls.append(f"delete_local_issue:{issue_identifier}")
            return VmActionResult(
                success=True,
                vm_name="vm-issue-1",
                action="delete_local_issue",
                message="deleted",
            )

        monkeypatch.setattr(
            service.issue_git_admin,
            "delete_local_issue",
            MethodType(delete_local_issue, service.issue_git_admin),
        )
        return

    def create_issue_vm_from_local_issue(self, issue_identifier, **_payload):
        del self
        calls.append(f"create_issue_vm_from_local_issue:{issue_identifier}")
        return VmActionResult(
            success=True,
            vm_name="vm-issue-1",
            action="create-issue-vm",
            message="created",
        )

    monkeypatch.setattr(
        service.vm_agent_admin,
        "create_issue_vm_from_local_issue",
        MethodType(create_issue_vm_from_local_issue, service.vm_agent_admin),
    )


@pytest.mark.parametrize(
    ("case", "method", "path", "payload", "expected_status"),
    _ADMIN_AGENT_MUTATION_CASES,
)
def test_admin_web_agent_mutations_require_owner_unlock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    method: str,
    path: str,
    payload: dict[str, object] | None,
    expected_status: int,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    calls: list[str] = []
    _install_admin_agent_mutation_stub(service, monkeypatch, case, calls)
    client = TestClient(create_app(service))

    locked_response = _invoke_admin_mutation(client, method=method, path=path, payload=payload)
    assert calls == []
    _assert_admin_mutation_forbidden(locked_response)

    _unlock_privileged_operations(client)
    unlocked_response = _invoke_admin_mutation(client, method=method, path=path, payload=payload)

    assert unlocked_response.status_code == expected_status
    assert calls


@pytest.mark.parametrize(
    ("case", "method", "path", "payload", "_expected_status"),
    _ADMIN_AGENT_MUTATION_CASES,
)
def test_admin_web_agent_mutations_reject_non_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    method: str,
    path: str,
    payload: dict[str, object] | None,
    _expected_status: int,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    calls: list[str] = []
    _install_admin_agent_mutation_stub(service, monkeypatch, case, calls)
    client = _regular_admin_web_client(tmp_path, monkeypatch, service)

    response = _invoke_admin_mutation(client, method=method, path=path, payload=payload)

    _assert_admin_mutation_forbidden(response)
    assert calls == []


@pytest.mark.parametrize(
    ("case", "method", "path", "payload", "expected_status"),
    _ADMIN_VM_ISSUE_MUTATION_CASES,
)
def test_admin_web_vm_issue_mutations_require_owner_unlock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    method: str,
    path: str,
    payload: dict[str, object] | None,
    expected_status: int,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    calls: list[str] = []
    _install_vm_issue_mutation_stub(service, monkeypatch, case, calls)
    client = TestClient(create_app(service))

    locked_response = _invoke_admin_mutation(client, method=method, path=path, payload=payload)
    assert calls == []
    _assert_admin_mutation_forbidden(locked_response)

    _unlock_privileged_operations(client)
    unlocked_response = _invoke_admin_mutation(client, method=method, path=path, payload=payload)

    assert unlocked_response.status_code == expected_status
    assert calls


@pytest.mark.parametrize(
    ("case", "method", "path", "payload", "_expected_status"),
    _ADMIN_VM_ISSUE_MUTATION_CASES,
)
def test_admin_web_vm_issue_mutations_reject_non_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    method: str,
    path: str,
    payload: dict[str, object] | None,
    _expected_status: int,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    calls: list[str] = []
    _install_vm_issue_mutation_stub(service, monkeypatch, case, calls)
    client = _regular_admin_web_client(tmp_path, monkeypatch, service)

    response = _invoke_admin_mutation(client, method=method, path=path, payload=payload)

    _assert_admin_mutation_forbidden(response)
    assert calls == []


def test_admin_web_directory_create_authorizes_scoped_owner_on_parent_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    service.directory_admin.create_child(parent="root", name="ops")
    client = _scoped_owner_admin_web_client(monkeypatch, service)

    response = client.post(
        "/api/configuration/directories",
        json={"parent_path": "root/ops", "name": "new"},
    )

    assert response.status_code == 200
    assert service.directory_admin.get("root/ops/new").ok is True


@pytest.mark.parametrize(
    ("case", "method", "path", "payload", "expected_status"),
    (
        (
            "vm-create",
            "POST",
            "/api/admin/vms",
            {"vm_name": "vm-1", "directory_path": "root/ops"},
            201,
        ),
        ("vm-action", "POST", "/api/admin/vms/vm-1/actions/start?directory_path=root/ops", {}, 200),
        (
            "issue-create",
            "POST",
            "/api/admin/issues",
            {"title": "Issue", "body": "Body", "directory_path": "root/ops"},
            201,
        ),
        (
            "issue-update",
            "PUT",
            "/api/admin/issues/issue-1",
            {"title": "Updated", "body": "Body", "state": "New"},
            200,
        ),
        ("issue-delete", "DELETE", "/api/admin/issues/issue-1", None, 200),
        ("issue-create-vm", "POST", "/api/admin/issues/issue-1/create-vm", {}, 200),
    ),
)
def test_admin_web_vm_issue_mutations_authorize_scoped_owner_at_target_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    method: str,
    path: str,
    payload: dict[str, object] | None,
    expected_status: int,
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    service.directory_admin.create_child(parent="root", name="ops")
    calls: list[str] = []
    _install_vm_issue_mutation_stub(service, monkeypatch, case, calls, directory_path="root/ops")
    client = _scoped_owner_admin_web_client(monkeypatch, service)

    response = _invoke_admin_mutation(client, method=method, path=path, payload=payload)

    assert response.status_code == expected_status
    assert calls


def test_admin_web_vm_action_requires_owner_for_every_linked_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    service.directory_admin.create_child(parent="root", name="team-a")
    service.directory_admin.create_child(parent="root", name="team-b")
    destroyed: list[str] = []

    def list_vms(self, **_kwargs):
        del self
        return [
            VmSummary(
                vm_name="shared-vm",
                issue_number="ISSUE-1",
                session_ids=["session-a", "session-b"],
                directory_paths=["root/team-a", "root/team-b"],
                libvirt_state="stopped",
                agent_state="idle",
            )
        ]

    def destroy_vm(self, vm_name: str) -> VmActionResult:
        del self
        destroyed.append(vm_name)
        return VmActionResult(
            success=True,
            vm_name=vm_name,
            action="destroy",
            message="destroyed",
        )

    monkeypatch.setattr(
        service.vm_agent_admin,
        "list_vms",
        MethodType(list_vms, service.vm_agent_admin),
    )
    monkeypatch.setattr(
        service.vm_agent_admin,
        "destroy_vm",
        MethodType(destroy_vm, service.vm_agent_admin),
    )
    client = _scoped_owner_admin_web_client(monkeypatch, service, directory_path="root/team-a")

    response = client.post(
        "/api/admin/vms/shared-vm/actions/destroy?directory_path=root/team-a", json={}
    )

    _assert_admin_mutation_forbidden(response)
    assert destroyed == []


@pytest.mark.parametrize(
    ("case", "method", "path", "payload", "expected_status"),
    (
        (
            "create",
            "POST",
            "/api/admin/agents",
            {
                "backend": "container_worker_vm",
                "session_id": "session-1",
                "directory_path": "root/ops",
            },
            201,
        ),
        (
            "submit-create-operation",
            "POST",
            "/api/admin/agents/create-operations",
            {
                "backend": "container_worker_vm",
                "session_id": "session-1",
                "directory_path": "root/ops",
            },
            202,
        ),
        (
            "patch",
            "PATCH",
            "/api/admin/agents/session-1?backend=libvirt_vm",
            {"model": "gpt-5"},
            200,
        ),
        ("delete", "DELETE", "/api/admin/agents/session-1?backend=libvirt_vm", None, 200),
        ("action", "POST", "/api/admin/agents/vm-1/actions/start?backend=libvirt_vm", {}, 200),
        (
            "prompt",
            "POST",
            "/api/admin/agents/vm-1/actions/prompt-agent?backend=libvirt_vm",
            {"prompt": "Continue"},
            200,
        ),
    ),
)
def test_admin_web_agent_mutations_authorize_scoped_owner_at_target_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    method: str,
    path: str,
    payload: dict[str, object] | None,
    expected_status: int,
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    service.directory_admin.create_child(parent="root", name="ops")
    calls: list[str] = []
    _install_admin_agent_mutation_stub(service, monkeypatch, case, calls, directory_path="root/ops")
    client = _scoped_owner_admin_web_client(monkeypatch, service)

    response = _invoke_admin_mutation(client, method=method, path=path, payload=payload)

    assert response.status_code == expected_status
    assert calls


def test_admin_web_rejects_start_when_vm_session_model_is_missing(
    tmp_path: Path, monkeypatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))

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

    monkeypatch.setattr(
        service.vm_agent_admin,
        "list_vms",
        MethodType(list_vms, service.vm_agent_admin),
    )
    monkeypatch.setattr(
        service.vm_agent_admin,
        "list_sessions",
        MethodType(list_sessions, service.vm_agent_admin),
    )
    monkeypatch.setattr(
        service.operations_admin,
        "compute_directory_effective_config",
        MethodType(compute_effective, service.operations_admin),
    )
    monkeypatch.setattr(
        service.vm_agent_admin,
        "start_vm",
        MethodType(start_vm, service.vm_agent_admin),
    )

    client = TestClient(create_app(service))
    _unlock_privileged_operations(client)

    response = client.post(
        "/api/admin/vms/vm-1/actions/start?directory_path=root/team-a&direct_only=true", json={}
    )

    assert response.status_code == 400
    assert "Cannot start/resume agent" in response.json()["error"]


def test_admin_web_agents_list_includes_vms_and_container_sessions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    service.directory_admin.create_child(parent="root", name="team-a")
    service.directory_admin.create_child(parent="root", name="team-b")
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="vm-session-a",
            vm_id="vm-a",
            vm_provider="libvirt",
            vm_provider_id="vm-a",
            directory_path="root/team-a",
            allowed_tools=[],
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            metadata={
                "libvirt_vm_template": {
                    "image_ref": "/var/lib/libvirt/images/evander/dotnet-agent.qcow2",
                    "template_alias": "dotnet",
                }
            },
        )
    )
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="vm-stale",
            vm_id="vm-stale",
            vm_provider="libvirt",
            vm_provider_id="vm-stale",
            directory_path="root/team-a",
            allowed_tools=[],
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            metadata={},
        )
    )
    for session_id, target_name, directory_path in (
        ("container-session-a", "target-a", "root/team-a"),
        ("container-session-b", "target-b", "root/team-b"),
    ):
        service.runtime.session_repository.upsert_session(
            SessionManifest(
                session_id=session_id,
                vm_provider="container_worker_vm",
                vm_provider_id=target_name,
                directory_path=directory_path,
                allowed_tools=[],
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                metadata={
                    "execution_backend": "container_worker_vm",
                    "target_name": target_name,
                },
            )
        )
    repository = ContainerWorkerExecutionRepository(
        service.config.runtime_dir / "container-worker-executions.json"
    )
    repository.put(
        ContainerWorkerExecutionRecord(
            execution_id="exec-a-1",
            session_id="container-session-a",
            worker_job_id="job-a-1",
            worker_url="http://worker.test",
            target_name="target-a",
            last_status="removed",
            directory_path="root/team-a",
            metadata={"image": "runner:old"},
        )
    )
    repository.put(
        ContainerWorkerExecutionRecord(
            execution_id="exec-a-2",
            session_id="container-session-a",
            worker_job_id="job-a-2",
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

    monkeypatch.setattr(
        service.vm_agent_admin,
        "list_vms",
        MethodType(list_vms, service.vm_agent_admin),
    )

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
    assert {item["agent_id"] for item in payload["items"]} == {
        "vm-a",
        "vm-stale",
        "container-session-a",
    }
    vm_item = next(
        item
        for item in payload["items"]
        if item["backend"] == "libvirt_vm" and item["agent_id"] == "vm-a"
    )
    assert vm_item["host"]["runtime_image"] == {
        "backend": "libvirt_vm",
        "kind": "libvirt_vm_template",
        "label": "Libvirt VM template",
        "template_alias": "dotnet",
        "image_ref": "dotnet-agent.qcow2",
        "image_ref_sanitized": True,
        "missing_reason": None,
    }
    stale_item = next(
        item
        for item in payload["items"]
        if item["backend"] == "libvirt_vm" and item["agent_id"] == "vm-stale"
    )
    assert stale_item["host"]["runtime_image"] == {
        "backend": "libvirt_vm",
        "kind": "libvirt_vm_template",
        "label": "Legacy session: VM template metadata unavailable",
        "template_alias": None,
        "image_ref": None,
        "image_ref_sanitized": False,
        "missing_reason": "legacy_missing_libvirt_vm_template",
    }
    container_item = next(
        item for item in payload["items"] if item["backend"] == "container_worker_vm"
    )
    assert container_item["status"] == "running"
    assert container_item["capabilities"]["logs"] is True
    assert container_item["host"]["execution_count"] == 2
    assert container_item["host"]["image"] == "runner:latest"
    assert "runtime_image" not in container_item["host"]
    assert [item["execution_id"] for item in container_item["host"]["executions"]] == [
        "exec-a-2",
        "exec-a-1",
    ]
    assert list_vms_calls[0]["directory_path"] == "root/team-a"
    assert list_vms_calls[0]["direct_only"] is True
    assert list_vms_calls[0]["omit_state"] is False
    assert payload["summary_only"] is False
    assert payload["reconcile_containers"] is True
    assert payload["omit_vm_state"] is False

    summary_response = client.get(
        "/api/admin/agents?directory_path=root/team-a&direct_only=true&include_inactive=true"
        "&summary_only=true"
    )

    assert summary_response.status_code == 200
    summary_payload = summary_response.json()
    assert summary_payload["summary_only"] is True
    assert summary_payload["reconcile_containers"] is False
    assert summary_payload["omit_vm_state"] is True
    summary_container_item = next(
        item for item in summary_payload["items"] if item["backend"] == "container_worker_vm"
    )
    assert "session_policy" in summary_container_item["host"]
    assert "executions" in summary_container_item["host"]
    assert list_vms_calls[1]["directory_path"] == "root/team-a"
    assert list_vms_calls[1]["omit_state"] is True

    override_response = client.get(
        "/api/admin/agents?directory_path=root/team-a&direct_only=true&include_inactive=true"
        "&summary_only=true&reconcile_containers=true&omit_vm_state=false"
    )

    assert override_response.status_code == 200
    override_payload = override_response.json()
    assert override_payload["summary_only"] is True
    assert override_payload["reconcile_containers"] is True
    assert override_payload["omit_vm_state"] is False
    assert list_vms_calls[2]["directory_path"] == "root/team-a"
    assert list_vms_calls[2]["omit_state"] is False

    root_response = client.get(
        "/api/admin/agents?directory_path=root&direct_only=true&include_inactive=true"
    )

    assert root_response.status_code == 200
    assert root_response.json()["items"] == []


def test_admin_web_agents_summary_reads_use_dashboard_cache(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    service.directory_admin.create_child(parent="root", name="team-a")
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="container-session-a",
            vm_provider="container_worker_vm",
            vm_provider_id="target-a",
            directory_path="root/team-a",
            allowed_tools=[],
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            metadata={
                "execution_backend": "container_worker_vm",
                "target_name": "target-a",
            },
        )
    )
    repository = ContainerWorkerExecutionRepository(
        service.config.runtime_dir / "container-worker-executions.json"
    )
    repository.put(
        ContainerWorkerExecutionRecord(
            execution_id="exec-a-1",
            session_id="container-session-a",
            worker_job_id="job-a-1",
            worker_url="http://worker.test",
            target_name="target-a",
            last_status="running",
            directory_path="root/team-a",
        )
    )
    original_list_agents = service.vm_agent_admin.list_agents
    list_agents_calls: list[dict[str, object]] = []

    def list_agents(self, **kwargs):
        list_agents_calls.append(kwargs)
        return original_list_agents(**kwargs)

    monkeypatch.setattr(
        service.vm_agent_admin,
        "list_agents",
        MethodType(list_agents, service.vm_agent_admin),
    )
    client = TestClient(create_app(service))

    summary_url = (
        "/api/admin/agents?directory_path=root/team-a&direct_only=true&include_inactive=true"
        "&summary_only=true&_dashboard=1&_dashboard_agent_inventory=1"
    )
    agent_page_summary_url = (
        "/api/admin/agents?directory_path=root/team-a&direct_only=true&include_inactive=true"
        "&summary_only=true&_dashboard=1&omit_vm_state=false"
    )
    deep_url = (
        "/api/admin/agents?directory_path=root/team-a&direct_only=true&include_inactive=true"
        "&summary_only=false&reconcile_containers=true&omit_vm_state=false"
    )
    refresh_url = f"{deep_url}&_dashboard=1&_dashboard_refresh=1"

    first = client.get(summary_url)
    second = client.get(summary_url)
    agent_page_summary = client.get(agent_page_summary_url)
    live = client.get(deep_url)
    refreshed = client.get(refresh_url)

    assert first.status_code == 200
    assert second.status_code == 200
    assert agent_page_summary.status_code == 200
    assert live.status_code == 200
    assert refreshed.status_code == 200
    assert first.headers["x-admin-web-cache"] == "miss"
    assert second.headers["x-admin-web-cache"] == "hit"
    assert "x-admin-web-cache" not in live.headers
    assert refreshed.headers["x-admin-web-cache"] == "refresh"
    assert first.json()["summary_only"] is True
    assert first.json()["reconcile_containers"] is False
    assert first.json()["omit_vm_state"] is True
    summary_item = first.json()["items"][0]
    assert summary_item["host"]["kind"] == "container_worker_session"
    assert "executions" not in summary_item["host"]
    assert "session_policy" not in summary_item["host"]
    assert "metadata" not in summary_item["host"]
    assert "records" not in summary_item["prompt_queue"]
    assert second.json() == first.json()
    agent_page_item = agent_page_summary.json()["items"][0]
    assert "session_policy" in agent_page_item["host"]
    assert "metadata" in agent_page_item["host"]
    assert live.json()["summary_only"] is False
    assert live.json()["reconcile_containers"] is True
    assert live.json()["omit_vm_state"] is False
    live_item = live.json()["items"][0]
    assert "executions" in live_item["host"]
    assert "session_policy" in live_item["host"]
    assert refreshed.json()["summary_only"] is False
    assert refreshed.json()["reconcile_containers"] is True
    assert refreshed.json()["omit_vm_state"] is False
    assert list_agents_calls == [
        {
            "directory_path": "root/team-a",
            "direct_only": True,
            "include_inactive": True,
            "reconcile_containers": False,
            "omit_vm_state": True,
            "summary_only": True,
            "backend": "all",
            "include_prompt_queue": True,
        },
        {
            "directory_path": "root/team-a",
            "direct_only": True,
            "include_inactive": True,
            "reconcile_containers": False,
            "omit_vm_state": False,
            "summary_only": True,
            "backend": "all",
            "include_prompt_queue": True,
        },
        {
            "directory_path": "root/team-a",
            "direct_only": True,
            "include_inactive": True,
            "reconcile_containers": True,
            "omit_vm_state": False,
            "summary_only": False,
            "backend": "all",
            "include_prompt_queue": True,
        },
        {
            "directory_path": "root/team-a",
            "direct_only": True,
            "include_inactive": True,
            "reconcile_containers": True,
            "omit_vm_state": False,
            "summary_only": False,
            "backend": "all",
            "include_prompt_queue": True,
        },
    ]


def test_admin_web_agents_backend_filter_controls_vm_inventory_failures(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="container-session",
            vm_provider="container_worker_vm",
            vm_provider_id="container-target",
            directory_path="root",
            allowed_tools=[],
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            metadata={
                "execution_backend": "container_worker_vm",
                "target_name": "container-target",
            },
        )
    )

    def fail_list_vms(self, **_kwargs):
        raise RuntimeError("sudo: a password is required")

    monkeypatch.setattr(
        service.vm_agent_admin, "list_vms", MethodType(fail_list_vms, service.vm_agent_admin)
    )
    client = TestClient(create_app(service))

    container_response = client.get(
        "/api/admin/agents?directory_path=root&backend=container_worker_vm"
    )
    vm_response = client.get("/api/admin/agents?directory_path=root&backend=libvirt_vm")

    assert container_response.status_code == 200
    assert [
        (item["agent_id"], item["backend"], item["status"])
        for item in container_response.json()["items"]
    ] == [("container-session", "container_worker_vm", "prepared")]
    assert vm_response.status_code == 400
    assert vm_response.json()["error"] == "sudo: a password is required"


def test_admin_web_container_agent_log_and_actions_do_not_require_vm_summary(
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
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
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
            execution_id="exec-2",
            session_id="session-1",
            worker_job_id="job-2",
            worker_url="http://worker.test",
            target_name="target-1",
            last_status="removed",
            directory_path="root",
        )
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
            self.logged: list[tuple[str, int, str | None]] = []
            self.paged: list[tuple[str, int, int, str | None]] = []
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

        def get_log_snapshot(
            self,
            target: str,
            *,
            lines: int = 50,
            mode: str | None = None,
        ) -> str:
            self.logged.append((target, lines, mode))
            if target == "exec-1":
                if mode == "execution":
                    return "session id: thread-a\nrepo execution line for exec-1\n"
                if mode == "raw":
                    return "session id: thread-a\nrepo raw line for exec-1\n"
                if mode == "session":
                    return "session transcript for exec-1\n"
                if mode == "augmented":
                    return "augmented transcript for exec-1\n"
            if target == "exec-2":
                if mode == "execution":
                    return "session id: thread-b\nother execution line for exec-2\n"
                if mode == "raw":
                    return "session id: thread-b\nother raw line for exec-2\n"
                if mode == "session":
                    return "session transcript for exec-2\n"
                if mode == "augmented":
                    return "augmented transcript for exec-2\n"
            raise AssertionError(f"unexpected log request: {target} {mode}")

        def get_log_page(
            self,
            target: str,
            *,
            limit: int = 200,
            offset: int = 0,
            query: str | None = None,
        ) -> dict[str, object]:
            self.paged.append((target, limit, offset, query))
            return {
                "text": "paged line\n",
                "limit": limit,
                "offset": offset,
                "returned_lines": 1,
                "total_lines": 8,
                "start_line": 5,
                "end_line": 5,
                "has_more_before": True,
                "query": query,
            }

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

    monkeypatch.setattr(
        service.vm_agent_admin, "list_vms", MethodType(fail_list_vms, service.vm_agent_admin)
    )
    monkeypatch.setattr(
        service.vm_agent_admin,
        "container_worker_backend",
        MethodType(lambda self, **_kwargs: fake_backend, service.vm_agent_admin),
    )

    client = TestClient(create_app(service))
    _unlock_privileged_operations(client)

    log_response = client.get(
        "/api/admin/agents/session-1/log?backend=container_worker_vm&view=executions&lines=4"
    )
    paged_response = client.get(
        "/api/admin/agents/session-1/log?backend=container_worker_vm&view=executions&limit=3&offset=0&query=repo"
    )
    stop_response = client.post(
        "/api/admin/agents/session-1/actions/stop?backend=container_worker_vm", json={}
    )
    destroy_response = client.post(
        "/api/admin/agents/exec-1/actions/destroy?backend=container_worker_vm", json={}
    )

    assert log_response.status_code == 200
    assert log_response.json()["view"] == "executions"
    assert "execution=exec-2" in log_response.json()["text"]
    assert "execution=exec-1" in log_response.json()["text"]
    assert "log=execution" in log_response.json()["text"]
    assert "repo execution line for exec-1" in log_response.json()["text"]
    assert "other execution line for exec-2" in log_response.json()["text"]
    assert "session transcript" not in log_response.json()["text"]
    assert "augmented transcript" not in log_response.json()["text"]
    assert paged_response.status_code == 200
    assert paged_response.json()["view"] == "executions"
    assert paged_response.json()["text"] == "repo execution line for exec-1\n"
    assert paged_response.json()["has_more_before"] is False
    assert paged_response.json()["total_lines"] == 1
    assert stop_response.status_code == 200
    assert stop_response.json()["status"] == "stopped"
    assert destroy_response.status_code == 200
    assert destroy_response.json()["status"] == "destroyed"
    assert fake_backend.inspected == []
    assert sorted(fake_backend.logged) == sorted(
        [
            ("exec-1", 2, "execution"),
            ("exec-2", 2, "execution"),
            ("exec-1", 0, "execution"),
            ("exec-2", 0, "execution"),
        ]
    )
    assert fake_backend.paged == []
    assert fake_backend.stopped == ["exec-1"]
    assert fake_backend.destroyed == ["exec-1"]


def test_admin_web_container_agent_log_threads_view_groups_threads(
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
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
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
            started_at=datetime(2026, 5, 18, 8, 30, tzinfo=timezone.utc),
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
            started_at=datetime(2026, 5, 18, 7, 0, tzinfo=timezone.utc),
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
            started_at=datetime(2026, 5, 18, 8, 0, tzinfo=timezone.utc),
            last_status="running",
            directory_path="root",
        )
    )

    class FakeContainerBackend:
        def __init__(self) -> None:
            self.logged: list[tuple[str, int, str | None]] = []

        def get_log_snapshot(
            self,
            target: str,
            *,
            lines: int = 50,
            mode: str | None = None,
        ) -> str:
            self.logged.append((target, lines, mode))
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
    client = TestClient(create_app(service))

    response = client.get(
        "/api/admin/agents/session-threads/log?backend=container_worker_vm&view=threads"
    )

    assert response.status_code == 200
    assert response.json()["view"] == "threads"
    assert response.json()["text"].index("thread=thread-b") < response.json()["text"].index(
        "thread=thread-a"
    )
    assert "first_execution=exec-b1" in response.json()["text"]
    assert "first_execution=exec-a1" in response.json()["text"]
    assert "executions=2" in response.json()["text"]
    assert "thread-b transcript" in response.json()["text"]
    assert "thread-a transcript" in response.json()["text"]
    assert fake_backend.logged == [
        ("exec-b1", 0, "raw"),
        ("exec-a1", 0, "raw"),
        ("exec-a2", 0, "raw"),
        ("exec-b1", 200, "session"),
        ("exec-a1", 200, "session"),
    ]


def test_admin_web_agents_create_vm_uses_agent_crud_payload(tmp_path: Path, monkeypatch) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    service.directory_admin.create_child(parent="root", name="team-a")
    service.skill_admin.create_skill({"name": "exampleindustry-app-demo", "content": "# Demo\n"})
    communication_targets_write = service.operations_admin.write_directory_domain_config(
        "root/team-a",
        COMMUNICATION_TARGETS_DOMAIN,
        {
            "targets": [
                {
                    "id": "ops-teams",
                    "provider": "teams",
                    "kind": "teams",
                    "address": "team-a/channel-2",
                    "policy": {"capabilities": ["read"]},
                },
                {
                    "id": "ops-email",
                    "provider": "email",
                    "kind": "email",
                    "address": "ops@example.com",
                    "policy": {
                        "capabilities": ["read", "write"],
                        "auth_profile_ref": "email.ops",
                    },
                },
            ]
        },
    )
    assert communication_targets_write.ok is True
    captured: dict[str, object] = {}

    def create_vm(self, **payload):
        captured.update(payload)
        return VmActionResult(
            success=True,
            vm_name=payload["vm_name"],
            action="create_vm",
            message="created",
            provider="libvirt",
        )

    monkeypatch.setattr(
        service.vm_agent_admin,
        "create_vm",
        MethodType(create_vm, service.vm_agent_admin),
    )
    client = TestClient(create_app(service))
    _unlock_privileged_operations(client)

    response = client.post(
        "/api/admin/agents",
        json={
            "backend": "libvirt_vm",
            "agent_provider": "claude",
            "provider": "libvirt",
            "vm_name": "lab-vm",
            "session_id": "lab-session",
            "directory_path": "root/team-a",
            "model": "gpt-5.2-codex",
            "model_provider_mode": "local_openai",
            "ttl_hours": 12,
            "labels": {"team": "a"},
            "allowed_tools": ["run_script"],
            "allowed_script_names": ["demo"],
            "communication_target_ids": ["ops-teams", "ops-email"],
            "discord_channels": ["ops"],
            "teams_channel_allowlist": ["team-a/channel-1", "chat:chat-1"],
            "email_mailboxes": ["default"],
            "email_folders": ["inbox"],
            "communication_file_receive_enabled": True,
            "communication_file_send_enabled": True,
            "communication_file_max_bytes": 4096,
            "communication_file_require_approval_for_send": True,
            "vm_user": "agent",
            "dotnet": True,
            "nuget": True,
            "mssql": True,
            "mssql_init_root": "/tmp/init",
            "playwright": True,
            "codex_skills": ["exampleindustry-app-demo"],
            "vm_image": "default",
        },
    )

    assert response.status_code == 201
    assert captured == {
        "provider": "libvirt",
        "vm_name": "lab-vm",
        "session_id": "lab-session",
        "labels": {"team": "a"},
        "model": "gpt-5.2-codex",
        "vm_user": "agent",
        "ttl_seconds": None,
        "ttl_hours": 12,
        "allowed_tools": ["run_script"],
        "allowed_script_names": ["demo"],
        "allowed_contact_ids": None,
        "discord_channels": ["ops"],
        "teams_channel_allowlist": ["team-a/channel-1", "chat:chat-1", "ops-teams"],
        "teams_chat_allowlist": None,
        "teams_user_allowlist": None,
        "email_mailboxes": ["default", "ops"],
        "email_folders": ["inbox"],
        "communication_file_receive_enabled": True,
        "communication_file_send_enabled": True,
        "communication_file_max_bytes": 4096,
        "communication_file_require_approval_for_send": True,
        "web_search_enabled": False,
        "dotnet": True,
        "nuget": True,
        "mssql": True,
        "mssql_init_root": "/tmp/init",
        "playwright": True,
        "codex_skills": ["exampleindustry-app-demo"],
        "directory_path": "root/team-a",
        "metadata": {
            "agent_provider": "claude",
            "model_provider_mode": "local_openai",
            "libvirt_vm_template": {
                "image_ref": "ubuntu-24.04-server-cloudimg-amd64.img",
                "template_alias": "default",
            },
        },
    }


@pytest.mark.parametrize(
    ("target_id", "capabilities"),
    [
        ("ops-email-read", ["read"]),
        ("ops-email-write", ["write"]),
    ],
)
def test_admin_web_agents_create_rejects_capability_limited_email_targets(
    tmp_path: Path,
    monkeypatch,
    target_id: str,
    capabilities: list[str],
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    service.directory_admin.create_child(parent="root", name="team-a")
    communication_targets_write = service.operations_admin.write_directory_domain_config(
        "root/team-a",
        COMMUNICATION_TARGETS_DOMAIN,
        {
            "targets": [
                {
                    "id": target_id,
                    "provider": "email",
                    "kind": "email",
                    "address": "ops@example.com",
                    "policy": {
                        "capabilities": capabilities,
                        "auth_profile_ref": "email.ops",
                    },
                },
            ]
        },
    )
    assert communication_targets_write.ok is True
    create_vm_calls: list[dict[str, object]] = []

    def create_vm(self, **payload):
        del self
        create_vm_calls.append(payload)
        raise AssertionError("create_vm should not run for invalid email targets")

    monkeypatch.setattr(
        service.vm_agent_admin,
        "create_vm",
        MethodType(create_vm, service.vm_agent_admin),
    )
    client = TestClient(create_app(service))
    _unlock_privileged_operations(client)

    response = client.post(
        "/api/admin/agents",
        json={
            "backend": "libvirt_vm",
            "vm_name": "lab-vm",
            "session_id": "lab-session",
            "directory_path": "root/team-a",
            "communication_target_ids": [target_id],
        },
    )

    assert response.status_code == 400
    assert target_id in response.json()["error"]
    assert "requires both read and write capabilities" in response.json()["error"]
    assert create_vm_calls == []


def test_admin_web_agents_create_options_returns_default_catalogs(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    client = TestClient(create_app(service))

    response = client.get("/api/admin/agents/create-options")

    assert response.status_code == 200
    body = response.json()
    assert body["directory_path"] == "root"
    assert body["backends"] == ["libvirt_vm", "container_worker_vm"]
    providers = {provider["value"]: provider for provider in body["agent_providers"]}
    assert providers["codex"]["execution_backends"] == [
        "libvirt_vm",
        "container_worker_vm",
    ]
    assert providers["claude"]["execution_backends"] == [
        "libvirt_vm",
        "container_worker_vm",
    ]
    assert providers["copilot"]["execution_backends"] == ["libvirt_vm"]
    assert body["model_provider_modes"] == [
        {"value": "openai_public", "label": "Public OpenAI"},
        {"value": "local_openai", "label": "Local OpenAI"},
        {"value": "azure_openai", "label": "Azure OpenAI"},
    ]
    assert body["libvirt"]["default_template"] == "default"
    assert body["libvirt"]["templates"] == [
        {"alias": "default", "image_ref": "ubuntu-24.04-server-cloudimg-amd64.img"}
    ]
    assert body["container_worker"]["default_image"] == "evander-runner:latest"
    assert body["container_worker"]["images"] == []
    assert body["communication_targets"] == []


def test_admin_web_agents_create_options_reflects_directory_overrides(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    created = service.directory_admin.create_child(parent="root", name="team")
    assert created.ok is True and created.data is not None
    team_path = created.data.details.directory.path
    providers_write = service.operations_admin.write_directory_domain_config(
        team_path,
        "providers",
        {
            "container_worker_default_image": "runner-base:latest",
            "container_worker_images": {
                "default": "runner-base:latest",
                "dotnet-playwright": "runner-dotnet-playwright:2026-06-05",
            },
        },
    )
    assert providers_write.ok is True
    vm_write = service.operations_admin.write_directory_domain_config(
        team_path,
        "vm",
        {
            "libvirt_vm_default_template": "ubuntu",
            "libvirt_vm_templates": {
                "ubuntu": "ubuntu-24.04-server-cloudimg-amd64.img",
                "fedora": "fedora-40-cloud.img",
            },
        },
    )
    assert vm_write.ok is True
    communications_write = service.operations_admin.write_directory_domain_config(
        team_path,
        COMMUNICATION_TARGETS_DOMAIN,
        {
            "targets": [
                {
                    "id": "ops-teams",
                    "provider": "teams",
                    "kind": "teams",
                    "address": "team-a/channel-1",
                    "policy": {"capabilities": ["read", "reply"]},
                },
                {
                    "id": "ops-discord",
                    "provider": "discord",
                    "kind": "discord",
                    "address": "123456789012345678",
                    "policy": {"capabilities": ["write"]},
                },
                {
                    "id": "ops-email",
                    "provider": "email",
                    "kind": "email",
                    "address": "ops@example.com",
                    "policy": {
                        "capabilities": ["read", "write"],
                        "auth_profile_ref": "email.ops",
                    },
                },
                {
                    "id": "ops-email-read",
                    "provider": "email",
                    "kind": "email",
                    "address": "read@example.com",
                    "policy": {"capabilities": ["read"], "auth_profile_ref": "email.read"},
                },
                {
                    "id": "ops-email-write",
                    "provider": "email",
                    "kind": "email",
                    "address": "write@example.com",
                    "policy": {
                        "capabilities": ["write"],
                        "auth_profile_ref": "email.write",
                    },
                },
            ]
        },
    )
    assert communications_write.ok is True
    client = TestClient(create_app(service))

    response = client.get(
        "/api/admin/agents/create-options",
        params={"directory_path": team_path},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["directory_path"] == team_path
    assert body["libvirt"]["default_template"] == "ubuntu"
    assert body["libvirt"]["templates"] == [
        {"alias": "default", "image_ref": "ubuntu-24.04-server-cloudimg-amd64.img"},
        {"alias": "fedora", "image_ref": "fedora-40-cloud.img"},
        {"alias": "ubuntu", "image_ref": "ubuntu-24.04-server-cloudimg-amd64.img"},
    ]
    assert body["container_worker"]["default_image"] == "runner-base:latest"
    assert body["container_worker"]["images"] == [
        {"alias": "default", "image_ref": "runner-base:latest"},
        {
            "alias": "dotnet-playwright",
            "image_ref": "runner-dotnet-playwright:2026-06-05",
        },
    ]
    communication_targets = {target["value"]: target for target in body["communication_targets"]}
    assert set(communication_targets) == {"ops-teams", "ops-discord", "ops-email"}
    assert communication_targets["ops-teams"]["metadata"]["provider"] == "teams"
    assert communication_targets["ops-discord"]["metadata"]["provider"] == "discord"
    assert communication_targets["ops-email"]["metadata"]["provider"] == "email"


def test_admin_web_agents_create_vm_duplicate_session_returns_409(
    tmp_path: Path, monkeypatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))

    def create_vm(self, **payload):
        return VmActionResult(
            success=False,
            vm_name=payload["vm_name"],
            action="create_vm",
            message="Session already exists: lab-session",
        )

    monkeypatch.setattr(
        service.vm_agent_admin,
        "create_vm",
        MethodType(create_vm, service.vm_agent_admin),
    )
    client = TestClient(create_app(service))
    _unlock_privileged_operations(client)

    response = client.post(
        "/api/admin/agents",
        json={
            "backend": "libvirt_vm",
            "vm_name": "lab-vm",
            "session_id": "lab-session",
        },
    )

    assert response.status_code == 409
    body = response.json()
    assert body["success"] is False
    assert body["message"] == "Session already exists: lab-session"


def test_admin_web_agents_create_operations_submit_list_and_get(
    tmp_path: Path, monkeypatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    captured_payloads: list[dict[str, object]] = []

    def create_admin_agent(self, payload):
        del self
        captured_payloads.append(dict(payload))
        return {
            "success": True,
            "agent_id": "container-session",
            "backend": "container_worker_vm",
            "target_name": "container-target",
            "action": "create",
            "message": "Prepared container worker session container-session",
            "session_id": "container-session",
        }

    monkeypatch.setattr(
        service.vm_agent_admin,
        "create_admin_agent",
        MethodType(create_admin_agent, service.vm_agent_admin),
    )
    client = TestClient(create_app(service))
    _unlock_privileged_operations(client)

    response = client.post(
        "/api/admin/agents/create-operations",
        json={
            "backend": "container_worker_vm",
            "session_id": "container-session",
            "target_name": "container-target",
            "ignored_provider_token": "super-secret-token",
        },
    )

    assert response.status_code == 202
    response_payload = response.json()
    assert "request_payload" not in response_payload["operation"]
    assert "super-secret-token" not in json.dumps(response_payload)
    operation_id = response_payload["operation"]["operation_id"]
    operation = wait_for_agent_create_operation_status(service, operation_id, "succeeded")
    assert operation.result is not None
    assert operation.result["target_name"] == "container-target"
    assert captured_payloads == [
        {
            "backend": "container_worker_vm",
            "session_id": "container-session",
            "target_name": "container-target",
            "ignored_provider_token": "super-secret-token",
        }
    ]

    list_response = client.get("/api/admin/agents/create-operations?directory_path=root&limit=5")
    get_response = client.get(f"/api/admin/agents/create-operations/{operation_id}")

    assert list_response.status_code == 200
    assert [item["operation_id"] for item in list_response.json()["items"]] == [operation_id]
    assert "request_payload" not in list_response.json()["items"][0]
    assert "super-secret-token" not in json.dumps(list_response.json())
    assert get_response.status_code == 200
    assert get_response.json()["operation"]["status"] == "succeeded"
    assert "request_payload" not in get_response.json()["operation"]
    assert "super-secret-token" not in json.dumps(get_response.json())
    assert (
        get_response.json()["operation"]["result"]["message"]
        == "Prepared container worker session container-session"
    )


def test_admin_web_startup_marks_abandoned_agent_create_operations_failed(
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

    create_app(service)

    operation = service.vm_agent_admin.get_admin_agent_create_operation("operation-running")
    assert operation.status == "failed"
    assert operation.error == "Admin Web process exited before this create operation finished."
    assert operation.finished_at is not None


def test_admin_web_agents_create_vm_forwards_supplied_teams_chat_allowlist(
    tmp_path: Path, monkeypatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    captured: dict[str, object] = {}

    def create_vm(self, **payload):
        captured.update(payload)
        return VmActionResult(
            success=True,
            vm_name=payload["vm_name"],
            action="create_vm",
            message="created",
            provider="libvirt",
        )

    monkeypatch.setattr(
        service.vm_agent_admin,
        "create_vm",
        MethodType(create_vm, service.vm_agent_admin),
    )
    client = TestClient(create_app(service))
    _unlock_privileged_operations(client)

    response = client.post(
        "/api/admin/agents",
        json={
            "backend": "libvirt_vm",
            "vm_name": "lab-vm",
            "teams_chat_allowlist": [" chat:chat-1 ", "chat:chat-2"],
        },
    )

    assert response.status_code == 201
    assert captured["teams_chat_allowlist"] == ["chat:chat-1", "chat:chat-2"]


def test_admin_web_agents_create_container_session_and_list_it(tmp_path: Path, monkeypatch) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    created = service.directory_admin.create_child(parent="root", name="team-a")
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
        },
    )
    assert write_result.ok is True

    class FakeContainerBackend:
        def __init__(self) -> None:
            self.config = service.runtime.provider_config.resolve_for_directory(team_path)
            self.prepared: list[tuple[str, dict[str, object]]] = []
            self.written: list[tuple[str, list[str]]] = []

        def prepare_session(
            self, *, session_id: str, metadata: dict[str, object] | None = None
        ) -> WorkerSessionRecord:
            self.prepared.append((session_id, dict(metadata or {})))
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
            self.written.append(
                (
                    session_id,
                    [str(file.get("destination_path") or "") for file in files],
                )
            )
            return object()

    fake_backend = FakeContainerBackend()
    monkeypatch.setattr(
        service.vm_agent_admin,
        "container_worker_backend",
        MethodType(lambda self, **_kwargs: fake_backend, service.vm_agent_admin),
    )
    monkeypatch.setattr(
        service.vm_agent_admin,
        "list_vms",
        MethodType(lambda self, **kwargs: [], service.vm_agent_admin),
    )
    client = TestClient(create_app(service))
    _unlock_privileged_operations(client)

    create_response = client.post(
        "/api/admin/agents",
        json={
            "backend": "container_worker_vm",
            "session_id": "container-session",
            "target_name": "container-target",
            "directory_path": team_path,
            "container_image": "dotnet-playwright",
            "model": "gpt-5.2-codex",
            "labels": {"role": "demo"},
            "allowed_script_names": ["seed-demo"],
            "teams_channel_allowlist": ["team-a/channel-1", "chat:chat-1"],
            "communication_file_receive_enabled": True,
            "communication_file_send_enabled": True,
            "communication_file_max_bytes": 8192,
            "communication_file_require_approval_for_send": True,
        },
    )
    list_response = client.get(
        f"/api/admin/agents?directory_path={team_path}&direct_only=true&include_inactive=true"
    )

    assert create_response.status_code == 201
    assert fake_backend.prepared == [
        (
            "container-session",
            {
                "execution_backend": "container_worker_vm",
                "directory_path": team_path,
                "target_name": "container-target",
                "labels": {"role": "demo"},
                "container_worker": {
                    "image": "runner-dotnet-playwright:2026-06-05",
                    "image_alias": "dotnet-playwright",
                },
                "worker_url": "http://team-worker.test:11888",
            },
        )
    ]
    assert create_response.json()["container_image"] == "runner-dotnet-playwright:2026-06-05"
    assert create_response.json()["container_image_alias"] == "dotnet-playwright"
    assert fake_backend.written
    assert "/home/agent/.evander/session.env" in fake_backend.written[0][1]
    assert "/home/agent/.codex/config.toml" in fake_backend.written[0][1]
    manifest = service.runtime.session_repository.get_session("container-session")
    assert manifest is not None
    assert manifest.vm_provider == "container_worker_vm"
    assert manifest.vm_id is None
    assert manifest.model == "gpt-5.2-codex"
    assert manifest.allowed_script_names == ["seed-demo"]
    assert manifest.teams_channel_allowlist == ["team-a/channel-1", "chat:chat-1"]
    assert manifest.communication_file_receive_enabled is True
    assert manifest.communication_file_send_enabled is True
    assert manifest.communication_file_max_bytes == 8192
    assert manifest.communication_file_require_approval_for_send is True

    assert list_response.status_code == 200
    payload = list_response.json()
    assert [
        (item["agent_id"], item["target_name"], item["status"]) for item in payload["items"]
    ] == [("container-session", "container-target", "prepared")]
    assert payload["items"][0]["capabilities"]["logs"] is False
    assert payload["items"][0]["host"]["image"] == "runner-dotnet-playwright:2026-06-05"
    assert payload["items"][0]["host"]["image_alias"] == "dotnet-playwright"


def test_admin_web_agents_create_container_duplicate_session_returns_400(
    tmp_path: Path, monkeypatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))

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
    client = TestClient(create_app(service))
    _unlock_privileged_operations(client)

    payload = {
        "backend": "container_worker_vm",
        "session_id": "container-session",
        "target_name": "container-target",
    }
    first_response = client.post("/api/admin/agents", json=payload)
    assert first_response.status_code == 201

    duplicate_response = client.post("/api/admin/agents", json=payload)
    assert duplicate_response.status_code == 400
    assert "Session already exists: container-session" in duplicate_response.json()["error"]


def test_admin_web_agents_patch_updates_session_policy_and_rejects_renames(
    tmp_path: Path,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="lab-session",
            vm_id="lab-vm",
            model="old-model",
            directory_path="root",
            allowed_tools=[ToolName.APPROVAL_SUBMIT],
            tool_argument_policies={
                "create_ado_backlog_item": [
                    {
                        "id": "policy",
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
            allowed_script_names=["old-script"],
            discord_channel_allowlist=["old-channel"],
            teams_channel_allowlist=["old-team/channel"],
            email_mailbox_allowlist=["old-mailbox"],
            email_folder_allowlist=["old-folder"],
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            metadata={"labels": {"old": "label"}, "keep": "value", "agent_thread_id": "old-thread"},
        )
    )
    client = TestClient(create_app(service))
    _unlock_privileged_operations(client)

    response = client.patch(
        "/api/admin/agents/lab-session?backend=libvirt_vm",
        json={
            "model": "new-model",
            "ttl_hours": 4,
            "allowed_tools": ["run_script"],
            "tool_argument_policies": {
                "create_ado_backlog_item": [
                    {
                        "id": "policy",
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
            "allowed_script_names": ["new-script"],
            "discord_channels": ["new-channel"],
            "teams_channel_allowlist": ["team-a/channel-1", "chat:chat-1"],
            "metadata": {"keep": "new-value"},
            "email_mailboxes": ["new-mailbox"],
            "email_folders": ["new-folder"],
            "communication_file_receive_enabled": True,
            "communication_file_send_enabled": True,
            "communication_file_max_bytes": 8192,
            "communication_file_allowed_mime_types": ["text/plain"],
            "communication_file_allowed_extensions": [".txt"],
            "communication_file_blocked_extensions": [".exe"],
            "communication_file_require_approval_for_send": True,
            "labels": {"new": "label"},
        },
    )
    rename_response = client.patch(
        "/api/admin/agents/lab-session?backend=libvirt_vm",
        json={"target_name": "renamed"},
    )

    assert response.status_code == 200
    assert rename_response.status_code == 400
    assert response.json()["effective_tool_policy"]["allowed_tools"] == ["run_script"]
    assert response.json()["effective_tool_policy"]["tool_summary"]["session_id"] == "lab-session"
    assert any(
        item["tool_name"] == "run_script"
        for item in response.json()["effective_tool_policy"]["tool_summary"]["available_tools"]
    )
    assert any(
        item["tool_name"] == "assistant_read_status"
        for item in response.json()["effective_tool_policy"]["tool_summary"]["unavailable_tools"]
    )
    assert (
        response.json()["effective_tool_policy"]["tool_argument_policies"][
            "create_ado_backlog_item"
        ][0]["id"]
        == "policy"
    )
    updated = service.runtime.session_repository.get_session("lab-session")
    assert updated is not None
    assert updated.model == "new-model"
    assert updated.allowed_tools == [ToolName.RUN_SCRIPT]
    assert updated.tool_argument_policies["create_ado_backlog_item"][0].id == "policy"
    assert updated.allowed_script_names == ["new-script"]
    assert updated.discord_channel_allowlist == ["new-channel"]
    assert updated.teams_channel_allowlist == ["team-a/channel-1", "chat:chat-1"]
    assert updated.email_mailbox_allowlist == ["new-mailbox"]
    assert updated.email_folder_allowlist == ["new-folder"]
    assert updated.communication_file_receive_enabled is True
    assert updated.communication_file_send_enabled is True
    assert updated.communication_file_max_bytes == 8192
    assert updated.communication_file_allowed_mime_types == ["text/plain"]
    assert updated.communication_file_allowed_extensions == [".txt"]
    assert updated.communication_file_blocked_extensions == [".exe"]
    assert updated.communication_file_require_approval_for_send is True
    assert updated.metadata == {"labels": {"new": "label"}, "keep": "new-value"}
    assert updated.expires_at > datetime.now(timezone.utc) + timedelta(hours=3)


def test_admin_web_agents_delete_vm_removes_unambiguous_session(
    tmp_path: Path, monkeypatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="lab-session",
            vm_id="lab-vm",
            allowed_tools=[],
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    )
    calls: list[list[str]] = []

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="destroyed", stderr="")

    monkeypatch.setattr("evander.admin.services.process_helpers.subprocess.run", _fake_run)
    client = TestClient(create_app(service))
    _unlock_privileged_operations(client)

    response = client.delete("/api/admin/agents/lab-vm?backend=libvirt_vm")

    assert response.status_code == 200
    assert response.json()["status"] == "deleted"
    assert service.runtime.session_repository.get_session("lab-session") is None
    assert calls[0][-1] == "lab-vm"


def test_admin_web_agents_delete_container_cleans_mapping_and_session(
    tmp_path: Path, monkeypatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="container-session",
            vm_provider="container_worker_vm",
            allowed_tools=[],
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            metadata={"target_name": "container-target"},
        )
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
    service.runtime.approvals.put(
        "approval-1",
        ApprovalRequest(
            request_id="approval-1",
            session_id="container-session",
            action_type="run_script",
            tool_name="run_script",
            payload_digest="abc",
            human_summary="run script",
            payload={"script": "seed-demo"},
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        ),
    )

    class FakeContainerBackend:
        def __init__(self) -> None:
            self.destroyed: list[str] = []
            self.deleted_sessions: list[str] = []

        def destroy(self, target: str) -> ExecutionStatus:
            self.destroyed.append(target)
            return ExecutionStatus(
                backend=ExecutionBackendKind.CONTAINER_WORKER_VM,
                execution_id=target,
                target_name="container-target",
                status=ExecutionStatusKind.DESTROYED,
                session_id="container-session",
            )

        def delete_session(self, session_id: str) -> dict[str, object]:
            self.deleted_sessions.append(session_id)
            return {"deleted": True, "session_id": session_id}

    fake_backend = FakeContainerBackend()
    monkeypatch.setattr(
        service.vm_agent_admin,
        "container_worker_backend",
        MethodType(lambda self, **_kwargs: fake_backend, service.vm_agent_admin),
    )
    client = TestClient(create_app(service))
    _unlock_privileged_operations(client)

    response = client.delete("/api/admin/agents/exec-1?backend=container_worker_vm")

    assert response.status_code == 200
    assert fake_backend.destroyed == ["exec-1"]
    assert fake_backend.deleted_sessions == ["container-session"]
    assert service.runtime.session_repository.get_session("container-session") is None
    assert repository.read_all() == {}
    assert service.runtime.approvals.read_all() == {}


def test_admin_web_agents_delete_container_session_uses_persisted_worker_url(
    tmp_path: Path, monkeypatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="container-session",
            vm_provider="container_worker_vm",
            allowed_tools=[],
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            metadata={"execution_backend": "container_worker_vm"},
        )
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
            last_status="removed",
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
    backend_kwargs: list[dict[str, object]] = []

    def fake_container_worker_backend(self, **kwargs):
        backend_kwargs.append(dict(kwargs))
        return fake_backend

    monkeypatch.setattr(
        service.vm_agent_admin,
        "container_worker_backend",
        MethodType(fake_container_worker_backend, service.vm_agent_admin),
    )
    client = TestClient(create_app(service))
    _unlock_privileged_operations(client)

    response = client.delete("/api/admin/agents/container-session?backend=container_worker_vm")

    assert response.status_code == 200
    assert backend_kwargs == [{"directory_path": "root", "worker_url": "http://worker.test"}]
    assert fake_backend.deleted_sessions == ["container-session"]
    assert service.runtime.session_repository.get_session("container-session") is None
    assert repository.read_all() == {}


def test_admin_web_agents_delete_ambiguous_vm_session_link_fails(
    tmp_path: Path, monkeypatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    for session_id in ("session-a", "session-b"):
        service.runtime.session_repository.upsert_session(
            SessionManifest(
                session_id=session_id,
                vm_id="lab-vm",
                allowed_tools=[],
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            )
        )
    destroyed: list[str] = []
    monkeypatch.setattr(
        service.vm_agent_admin,
        "destroy_vm",
        MethodType(lambda self, target: destroyed.append(target), service.vm_agent_admin),
    )
    client = TestClient(create_app(service))
    _unlock_privileged_operations(client)

    response = client.delete("/api/admin/agents/lab-vm?backend=libvirt_vm")

    assert response.status_code == 400
    assert "multiple linked sessions" in response.json()["error"]
    assert destroyed == []
    assert service.runtime.session_repository.get_session("session-a") is not None
    assert service.runtime.session_repository.get_session("session-b") is not None


def test_admin_web_vm_agent_action_uses_existing_vm_path(tmp_path: Path, monkeypatch) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    called: list[str] = []

    def start_vm(self, target: str) -> VmActionResult:
        called.append(target)
        return VmActionResult(
            success=True,
            vm_name=target,
            action="start",
            message="started",
        )

    monkeypatch.setattr(
        service.vm_agent_admin,
        "start_vm",
        MethodType(start_vm, service.vm_agent_admin),
    )

    client = TestClient(create_app(service))
    _unlock_privileged_operations(client)
    response = client.post("/api/admin/agents/vm-1/actions/start?backend=libvirt_vm", json={})

    assert response.status_code == 200
    assert response.json()["backend"] == "libvirt_vm"
    assert called == ["vm-1"]


def test_admin_web_agent_prompt_action_uses_existing_vm_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    calls: list[dict[str, object]] = []

    def prompt_admin_agent(
        self: AdminRuntimeComponents,
        agent_id: str,
        *,
        backend: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        calls.append(
            {
                "agent_id": agent_id,
                "backend": backend,
                "payload": payload,
            }
        )
        return {
            "success": True,
            "queued": True,
            "queue_key": "admin-agent-prompt:libvirt_vm:vm-1",
            "dispatch_id": "dispatch-admin-agent-prompt:libvirt_vm:vm-1-123",
            "thread_id": "thread-123",
            "execution_id": "execution-123",
            "agent_id": "vm-1",
            "backend": backend,
            "target_name": "vm-1",
            "action": "prompt-agent",
            "message": "Queued prompt for vm-1",
            "status": "queued",
            "session_id": "session-123",
        }

    monkeypatch.setattr(
        service.vm_agent_admin,
        "prompt_admin_agent",
        MethodType(prompt_admin_agent, service.vm_agent_admin),
    )

    client = TestClient(create_app(service))
    _unlock_privileged_operations(client)
    response = client.post(
        "/api/admin/agents/vm-1/actions/prompt-agent?backend=libvirt_vm",
        json={"prompt": "Summarize the latest changes", "resume_thread_id": "thread-keep-1"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "queued": True,
        "queue_key": "admin-agent-prompt:libvirt_vm:vm-1",
        "dispatch_id": "dispatch-admin-agent-prompt:libvirt_vm:vm-1-123",
        "thread_id": "thread-123",
        "execution_id": "execution-123",
        "agent_id": "vm-1",
        "backend": "libvirt_vm",
        "target_name": "vm-1",
        "action": "prompt-agent",
        "message": "Queued prompt for vm-1",
        "status": "queued",
        "session_id": "session-123",
    }
    assert calls == [
        {
            "agent_id": "vm-1",
            "backend": "libvirt_vm",
            "payload": {
                "prompt": "Summarize the latest changes",
                "resume_thread_id": "thread-keep-1",
            },
        }
    ]


def test_admin_web_agent_prompt_action_uses_existing_container_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path, execution_backend="container_worker_vm"))
    calls: list[dict[str, object]] = []

    def prompt_admin_agent(
        self: AdminRuntimeComponents,
        agent_id: str,
        *,
        backend: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        calls.append(
            {
                "agent_id": agent_id,
                "backend": backend,
                "payload": payload,
            }
        )
        return {
            "success": True,
            "queued": False,
            "queue_key": "admin-agent-prompt:container_worker_vm:container-session",
            "dispatch_id": "dispatch-admin-agent-prompt:container_worker_vm:container-session-123",
            "thread_id": "thread-456",
            "execution_id": "container-exec-1",
            "agent_id": "container-session",
            "backend": backend,
            "target_name": "container-target",
            "action": "prompt-agent",
            "message": "Dispatched prompt to container-target",
            "status": "dispatched",
            "session_id": "container-session",
        }

    monkeypatch.setattr(
        service.vm_agent_admin,
        "prompt_admin_agent",
        MethodType(prompt_admin_agent, service.vm_agent_admin),
    )

    client = TestClient(create_app(service))
    _unlock_privileged_operations(client)
    response = client.post(
        "/api/admin/agents/container-session/actions/prompt-agent?backend=container_worker_vm",
        json={"prompt": "Continue the container workflow", "resume_thread_id": "thread-keep-2"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "queued": False,
        "queue_key": "admin-agent-prompt:container_worker_vm:container-session",
        "dispatch_id": "dispatch-admin-agent-prompt:container_worker_vm:container-session-123",
        "thread_id": "thread-456",
        "execution_id": "container-exec-1",
        "agent_id": "container-session",
        "backend": "container_worker_vm",
        "target_name": "container-target",
        "action": "prompt-agent",
        "message": "Dispatched prompt to container-target",
        "status": "dispatched",
        "session_id": "container-session",
    }
    assert calls == [
        {
            "agent_id": "container-session",
            "backend": "container_worker_vm",
            "payload": {
                "prompt": "Continue the container workflow",
                "resume_thread_id": "thread-keep-2",
            },
        }
    ]


def test_admin_web_agent_prompt_action_rejects_blank_prompt(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    client = TestClient(create_app(service))
    _unlock_privileged_operations(client)

    response = client.post(
        "/api/admin/agents/vm-1/actions/prompt-agent?backend=libvirt_vm",
        json={"prompt": "   "},
    )

    assert response.status_code == 400
    assert response.json()["error"] == "prompt-agent requires a non-empty prompt"


def test_admin_web_issue_detail_preserves_local_editability(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    created_issue = service.issue_git_admin.create_local_issue(
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
    service = build_admin_runtime(write_config(tmp_path))
    created_issue = service.issue_git_admin.create_local_issue(
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
    service = build_admin_runtime(write_config(tmp_path))
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
    service = build_admin_runtime(write_config(tmp_path))
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
    service = build_admin_runtime(write_config(tmp_path))
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


def test_admin_web_operator_steering_note_flow_requires_privileged_unlock(
    tmp_path: Path,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-steer",
            directory_path="root",
            allowed_tools=[],
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    )
    register_model_usage_correlation_context(
        runtime_dir=service.config.runtime_dir,
        session_id="session-steer",
        dispatch_id="dispatch-steer",
        workflow_run_id="workflow-steer",
        effect_id="effect-steer",
        directory_path="root",
        model="gpt-5.2-codex",
    )
    client = TestClient(create_app(service))

    context_response = client.get(
        "/api/operator-steering/context",
        params={"session_id": "session-steer"},
    )
    assert context_response.status_code == 200
    context_payload = context_response.json()
    assert context_payload["directory_path"] == "root"
    assert context_payload["context"]["session_id"] == "session-steer"
    assert context_payload["context"]["dispatch_id"] == "dispatch-steer"

    locked = client.post(
        "/api/operator-steering/notes",
        json={
            "session_id": "session-steer",
            "dispatch_id": "dispatch-steer",
            "worker_execution_id": "worker-steer",
            "message": "Move from exploration to implementation.",
        },
    )
    assert locked.status_code == 403
    _unlock_privileged_operations(client)

    created = client.post(
        "/api/operator-steering/notes",
        json={
            "session_id": "session-steer",
            "dispatch_id": "dispatch-steer",
            "worker_execution_id": "worker-steer",
            "message": "Move from exploration to implementation.",
        },
    )
    assert created.status_code == 200
    created_payload = created.json()
    note = created_payload["note"]
    assert created_payload["directory_path"] == "root"
    assert note["status"] == "pending"
    assert note["session_id"] == "session-steer"
    assert note["dispatch_id"] == "dispatch-steer"
    assert note["worker_execution_id"] == "worker-steer"

    listed = client.get(
        "/api/operator-steering/notes",
        params={"session_id": "session-steer", "dispatch_id": "dispatch-steer"},
    )
    assert listed.status_code == 200
    assert [entry["note_id"] for entry in listed.json()["notes"]] == [note["note_id"]]

    cancelled = client.post(f"/api/operator-steering/notes/{note['note_id']}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["note"]["status"] == "cancelled"


def test_admin_web_creates_standalone_vm(tmp_path: Path, monkeypatch) -> None:
    service = build_admin_runtime(write_config(tmp_path))

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

    monkeypatch.setattr(
        service.vm_agent_admin,
        "create_vm",
        MethodType(create_vm, service.vm_agent_admin),
    )

    client = TestClient(create_app(service))
    _unlock_privileged_operations(client)

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
    service = build_admin_runtime(write_config(tmp_path))
    created_issue = service.issue_git_admin.create_local_issue(
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

    monkeypatch.setattr(
        service.issue_git_admin,
        "create_local_issue",
        MethodType(create_local_issue, service.issue_git_admin),
    )
    monkeypatch.setattr(
        service.vm_agent_admin,
        "create_issue_vm_from_local_issue",
        MethodType(create_issue_vm, service.vm_agent_admin),
    )
    threadpool_calls: list[str] = []

    async def fake_run_in_threadpool(func, *args, **kwargs):
        threadpool_calls.append(getattr(func, "__name__", repr(func)))
        return func(*args, **kwargs)

    monkeypatch.setattr(vms_issues_module, "run_in_threadpool", fake_run_in_threadpool)

    client = TestClient(create_app(service))
    _unlock_privileged_operations(client)

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
    assert threadpool_calls == ["_create_issue"]


def test_admin_web_create_vm_from_issue_runs_in_threadpool(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    created_issue = service.issue_git_admin.create_local_issue(
        title="Draft fix",
        body="body",
        directory_path="root/team-a",
    )

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

    monkeypatch.setattr(
        service.vm_agent_admin,
        "create_issue_vm_from_local_issue",
        MethodType(create_issue_vm, service.vm_agent_admin),
    )
    threadpool_calls: list[str] = []

    async def fake_run_in_threadpool(func, *args, **kwargs):
        threadpool_calls.append(getattr(func, "__name__", repr(func)))
        return func(*args, **kwargs)

    monkeypatch.setattr(vms_issues_module, "run_in_threadpool", fake_run_in_threadpool)

    client = TestClient(create_app(service))
    _unlock_privileged_operations(client)

    response = client.post(
        f"/api/admin/issues/{created_issue.issue_id}/create-vm",
        json={
            "model": "gpt-5.2-codex",
            "dotnet": True,
            "nuget": "/tmp/project.slnx",
            "playwright": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["vm_name"] == "issue-vm-1"
    assert threadpool_calls == ["create_issue_vm"]
