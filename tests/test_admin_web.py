from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MethodType
from types import SimpleNamespace

import httpx
import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from agent_sandbox.admin.service import (
    AdminService,
    SAMPLE_APPROVAL_BOUNDARY_NODE_ID,
    SAMPLE_AGENT_PROMPT_NODE_ID,
    SAMPLE_SAFE_STOP_NODE_ID,
    SAMPLE_TRIGGER_NODE_ID,
    SAMPLE_WORKFLOW_RUN_ID,
    SAMPLE_WORKFLOW_TRACE_ID,
)
from agent_sandbox.admin.service import (
    CapacityControlPlanePayload,
    CapacityControlPlaneStoragePayload,
    CapacityHealthPayload,
    CapacityPromptTelemetryPayload,
    CapacityQueueTelemetryPayload,
    CapacitySessionStoragePayload,
    CapacitySessionSummaryPayload,
    CapacityWorkerTelemetryEntryPayload,
    CapacityWorkerTelemetrySummaryPayload,
)
from agent_sandbox.admin import release_metadata as release_metadata_module
from agent_sandbox.admin.web.cache import AdminWebJsonResponseCache
from agent_sandbox.admin.web.app import _workflow_run_events, create_app
from agent_sandbox.admin.web.shared import AdminWebConfig
from agent_sandbox.admin.web.user_store import AdminWebUserStore
from agent_sandbox.capacity_health import (
    DiskThresholds,
    DiskUsageTelemetry,
    DirectorySizeTelemetry,
    HealthState,
    SessionStorageSample,
    TelemetryError,
    TelemetryStatus,
)
from agent_sandbox.config import GatewayConfig, TriggerOrchestratorWorkerGlobalConfig
from agent_sandbox.daemons.trigger_orchestrator import TriggerOrchestratorWorker
from agent_sandbox.communication_artifacts import CommunicationArtifactStore
from agent_sandbox.published_artifacts import PublishedArtifactStore
from agent_sandbox.execution.container_worker_repository import (
    ContainerWorkerExecutionRecord,
    ContainerWorkerExecutionRepository,
)
from agent_sandbox.execution.models import (
    ExecutionBackendKind,
    ExecutionStatus,
    ExecutionStatusKind,
)
from agent_sandbox.messaging.targets import (
    COMMUNICATION_TARGETS_DOMAIN,
    SqliteCommunicationTargetsRepository,
    communication_targets_db_path,
)
from agent_sandbox.models import (
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
from agent_sandbox.events.internal import InternalEventBus
from agent_sandbox.triggers.queue import SqliteTriggerEventQueue
from agent_sandbox.worker.models import WorkerSessionRecord


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


def _workflow_expected_revision(client: TestClient) -> str:
    response = client.get("/api/workflow-editor/document")
    assert response.status_code == 200
    revision = response.json()["revision"]
    assert isinstance(revision, str)
    return revision


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
    response = client.get(
        f"/api/configuration/state?directory_path={directory_path}"
    )
    assert response.status_code == 200
    payload = response.json()
    snapshot = payload["tab_snapshots"].get(tab_or_domain) or payload["snapshots"][tab_or_domain]
    revision = snapshot["revision"]
    assert isinstance(revision, str)
    return revision


def _set_azure_sso_env(
    monkeypatch,
    *,
    allowed_users: str | None = "operator@example.com",
    post_login_redirect: str | None = None,
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
    if allowed_users is None:
        monkeypatch.delenv("AGENT_SANDBOX_ADMIN_WEB_AZURE_ALLOWED_USERS", raising=False)
    else:
        monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AZURE_ALLOWED_USERS", allowed_users)
    if post_login_redirect is not None:
        monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AZURE_POST_LOGIN_REDIRECT", post_login_redirect)


def _admin_web_user_store(tmp_path: Path) -> AdminWebUserStore:
    return AdminWebUserStore(tmp_path / "runtime" / "admin-web-users.json")


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


def _signed_webhook_headers(body: bytes, *, secret: str) -> dict[str, str]:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Agent-Sandbox-Signature-256": f"sha256={digest}",
    }


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
    assert all(
        descriptions_by_type[node["type"]] for node in catalog_payload["nodes"]
    ), descriptions_by_type
    assert not any(
        descriptions_by_type[node["type"]] in generic_kind_tooltips
        for node in catalog_payload["nodes"]
    )
    assert (
        descriptions_by_type["effect:bash:script"]
        == "Run a host-side Bash script from the effect orchestrator."
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
    agent_copy_files = next(
        node
        for node in catalog_payload["nodes"]
        if node["type"] == "effect:agent:copy-files"
    )
    agent_copy_fields = {field["key"]: field for field in agent_copy_files["fields"]}
    assert all(
        agent_copy_fields[key]["description"]
        for key in ["target", "session_id", "vm_name", "vm_user", "files"]
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
        node
        for node in catalog_payload["nodes"]
        if node["type"] == "effect:issue:update-metadata"
    )
    assert [field["key"] for field in issue_metadata["fields"]] == [
        "issue_id",
        "patch",
        "merge_patch",
        "expected_updated_at",
        "sync_proxy_cache",
    ]
    ado_update = next(
        node
        for node in catalog_payload["nodes"]
        if node["type"] == "effect:ado:update-work-item"
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
    pr_update = next(
        node
        for node in catalog_payload["nodes"]
        if node["type"] == "effect:pull-request:update"
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
    event_emit = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:event:emit"
    )
    assert [field["key"] for field in event_emit["fields"]] == [
        "event_type",
        "correlation_key",
        "payload",
    ]
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
    assert next(
        field for field in communication["fields"] if field["key"] == "communication_target_id"
    )["kind"] == "enum"
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
        "resume_thread_id",
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
        "expires_in_seconds",
    ]
    assert any(
        field["key"] == "tools" and field["required"] and field["bindable"]
        for field in tool_approval["fields"]
    )
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
        node
        for node in catalog_payload["nodes"]
        if node["type"] == "trigger:issue:comment-created"
    )
    assert [field["key"] for field in issue_comment["fields"]] == [
        "provider",
        "organization_url",
        "project",
        "directories",
    ]
    issue_comment_alias = next(
        node for node in catalog_payload["nodes"] if node["type"] == "trigger:issue:new-comment"
    )
    assert [field["key"] for field in issue_comment_alias["fields"]] == [
        "provider",
        "organization_url",
        "project",
        "directories",
    ]
    teams_message = next(
        node for node in catalog_payload["nodes"] if node["type"] == "trigger:teams:new-message"
    )
    assert [field["key"] for field in teams_message["fields"]] == ["targets", "user_ids"]
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
    issue_get = next(node for node in catalog_payload["nodes"] if node["type"] == "query:issue:get")
    assert [field["key"] for field in issue_get["fields"]] == ["issue_id"]
    semantic_status = next(
        node
        for node in catalog_payload["nodes"]
        if node["type"] == "query:semantic:store-status"
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
        node
        for node in catalog_payload["nodes"]
        if node["type"] == "query:agent:artifact-text"
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
    pr_feedback = next(
        node
        for node in catalog_payload["nodes"]
        if node["type"] == "query:pull-request:feedback"
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
    vm_create = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:vm:create"
    )
    assert any(field["key"] == "allowed_script_names" for field in vm_create["fields"])
    agent_create = next(
        node for node in catalog_payload["nodes"] if node["type"] == "effect:agent:create"
    )
    assert {field["key"] for field in agent_create["fields"]} == {
        "provider",
        "execution_backend",
        "vm_name",
        "session_id",
        "existing_agent_behavior",
        "labels",
        "model",
        "vm_user",
        "ttl_hours",
        "allowed_tools",
        "tool_argument_policies",
        "allowed_script_names",
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
        "dotnet",
        "nuget",
        "mssql",
        "mssql_init_root",
        "playwright",
        "codex_skills",
        "directory_path",
    }
    agent_create_fields = {field["key"]: field for field in agent_create["fields"]}
    assert agent_create_fields["labels"]["kind"] == "string_map"
    assert agent_create_fields["labels"]["description"]
    assert agent_create_fields["directory_path"]["description"]
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
        "directory_path",
    ]
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
        "vm_user",
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
    assert {field["key"] for field in vm_create["fields"]} == {
        "vm_name",
        "session_id",
        "labels",
        "model",
        "vm_user",
        "ttl_hours",
        "allowed_tools",
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
    }


def test_admin_service_sample_workflow_launch_and_reset_are_idempotent(
    tmp_path: Path,
) -> None:
    service = AdminService(write_config(tmp_path))

    initial_state = service.read_workflow_sample_state()
    assert initial_state.active is False
    assert initial_state.launched_at is None
    assert initial_state.run is None
    assert initial_state.detail is None
    assert initial_state.current_step is not None
    assert initial_state.current_step.step_id == "launch"

    launched_once = service.launch_workflow_sample()
    launched_twice = service.launch_workflow_sample()
    assert launched_once.active is True
    assert launched_once.launched_at is not None
    assert launched_twice.active is True
    assert launched_twice.launched_at == launched_once.launched_at
    assert launched_once.run is not None
    assert launched_once.detail is not None
    assert launched_once.run.event_id == SAMPLE_WORKFLOW_RUN_ID
    assert launched_once.detail.event_id == SAMPLE_WORKFLOW_RUN_ID
    assert launched_once.detail.trace_id == SAMPLE_WORKFLOW_TRACE_ID
    assert launched_once.current_step is not None
    assert launched_once.current_step.step_id == "inspect"

    reset_once = service.reset_workflow_sample()
    reset_twice = service.reset_workflow_sample()
    assert reset_once.active is False
    assert reset_once.launched_at is None
    assert reset_once.run is None
    assert reset_once.detail is None
    assert reset_once.current_step is not None
    assert reset_once.current_step.step_id == "launch"
    assert reset_twice.active is False
    assert reset_twice.launched_at is None


def test_admin_web_sample_workflow_routes_are_in_memory_and_side_effect_free(
    tmp_path: Path,
) -> None:
    service = AdminService(write_config(tmp_path))
    runtime_dir = service.config.runtime_dir
    with TestClient(create_app(service)) as client:
        before_files = _snapshot_runtime_files(runtime_dir)

        inactive_response = client.get("/api/workflow-editor/sample-workflow")
        assert inactive_response.status_code == 200
        inactive_payload = inactive_response.json()
        assert inactive_payload["active"] is False
        assert inactive_payload["launched_at"] is None
        assert inactive_payload["run"] is None
        assert inactive_payload["detail"] is None
        assert [step["step_id"] for step in inactive_payload["tutorial_steps"]] == [
            "launch",
            "inspect",
            "approval",
            "safe-stop",
            "reset",
        ]
        assert inactive_payload["current_step"]["step_id"] == "launch"

        launch_response = client.post("/api/workflow-editor/sample-workflow/launch")
        assert launch_response.status_code == 200
        launch_payload = launch_response.json()
        assert launch_payload["active"] is True
        assert launch_payload["launched_at"] is not None
        assert launch_payload["run"]["event_id"] == SAMPLE_WORKFLOW_RUN_ID
        assert launch_payload["run"]["trace_id"] == SAMPLE_WORKFLOW_TRACE_ID
        assert launch_payload["detail"]["event_id"] == SAMPLE_WORKFLOW_RUN_ID
        assert launch_payload["detail"]["trace_id"] == SAMPLE_WORKFLOW_TRACE_ID
        assert launch_payload["current_step"]["step_id"] == "inspect"
        assert launch_payload["tutorial_steps"][0]["status"] == "completed"
        assert launch_payload["tutorial_steps"][1]["status"] == "active"

        repeat_launch_response = client.post("/api/workflow-editor/sample-workflow/launch")
        assert repeat_launch_response.status_code == 200
        assert repeat_launch_response.json()["launched_at"] == launch_payload["launched_at"]

        detail_queries = [
            ("event_id", {"event_id": SAMPLE_WORKFLOW_RUN_ID}, "event_id"),
            ("run_id", {"run_id": SAMPLE_WORKFLOW_RUN_ID}, "run_id"),
            ("trace_id", {"trace_id": SAMPLE_WORKFLOW_TRACE_ID}, "trace_id"),
        ]
        for lookup_key, params, matched_by in detail_queries:
            detail_response = client.get("/api/workflow-editor/run-detail", params=params)
            assert detail_response.status_code == 200
            detail_payload = detail_response.json()
            assert detail_payload["event_id"] == SAMPLE_WORKFLOW_RUN_ID
            assert detail_payload["trace_id"] == SAMPLE_WORKFLOW_TRACE_ID
            assert detail_payload["matched_by"] == [matched_by]
            assert detail_payload["lookup"][lookup_key] == params[lookup_key]
            assert [node["node_id"] for node in detail_payload["nodes"]] == [
                SAMPLE_TRIGGER_NODE_ID,
                SAMPLE_AGENT_PROMPT_NODE_ID,
                SAMPLE_APPROVAL_BOUNDARY_NODE_ID,
                SAMPLE_SAFE_STOP_NODE_ID,
            ]
            assert detail_payload["graph"]["snapshot"]["revision"] == "sample:first-run:revision"
            assert detail_payload["graph"]["snapshot"]["snapshot_type"] == "simulated_in_memory_sample"
            trigger_node = detail_payload["nodes"][0]
            agent_node = detail_payload["nodes"][1]
            approval_node = detail_payload["nodes"][2]
            safe_stop_node = detail_payload["nodes"][3]
            assert trigger_node["node_type"] == "trigger:event"
            assert agent_node["node_type"] == "effect:agent:prompt"
            assert approval_node["node_type"] == "gate:await-agent"
            assert safe_stop_node["node_type"] == "effect:notification:send"
            assert agent_node["tool_call_links"][0]["status"] == "simulated_in_memory"
            assert agent_node["agent_links"][0]["session_status"] == "session_missing"
            assert approval_node["approval_links"][0]["status"] == "pending"
            assert safe_stop_node["error"] == "Simulated safe stop: the sample never touches runtime stores."
            assert safe_stop_node["outputs"]["safe_stop"] is True
            assert detail_payload["tool_call_links"][0]["workflow_run_id"] == SAMPLE_WORKFLOW_RUN_ID
            assert detail_payload["tool_call_links"][0]["status"] == "simulated_in_memory"

        reset_response = client.delete("/api/workflow-editor/sample-workflow")
        assert reset_response.status_code == 200
        reset_payload = reset_response.json()
        assert reset_payload["active"] is False
        assert reset_payload["launched_at"] is None
        assert reset_payload["run"] is None
        assert reset_payload["detail"] is None
        assert reset_payload["current_step"]["step_id"] == "launch"

        repeat_reset_response = client.delete("/api/workflow-editor/sample-workflow")
        assert repeat_reset_response.status_code == 200
        assert repeat_reset_response.json()["active"] is False

        assert _snapshot_runtime_files(runtime_dir) == before_files


def test_admin_web_workflow_catalog_lists_directory_communication_send_targets(
    tmp_path: Path,
) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    created = service.create_directory_child("root", "ops")
    assert created.ok is True and created.data is not None
    root_write = service.write_directory_domain_config(
        "root",
        COMMUNICATION_TARGETS_DOMAIN,
        {
            "targets": [
                {
                    "id": "root-chat",
                    "provider": "teams",
                    "kind": "teams",
                    "address": "team/channel",
                    "policy": {"capabilities": ["write"]},
                },
                {
                    "id": "root-notify",
                    "provider": "teams",
                    "kind": "teams",
                    "address": "team/notify",
                    "policy": {"capabilities": ["notify"]},
                },
                {
                    "id": "root-email-notify",
                    "provider": "email",
                    "kind": "email",
                    "address": "root@example.com",
                    "policy": {"capabilities": ["notify"], "auth_profile_ref": "email.root"},
                },
                {
                    "id": "root-discord-reply-only",
                    "provider": "discord",
                    "kind": "discord",
                    "address": "123456789012345681",
                    "policy": {"capabilities": ["reply"]},
                },
                {
                    "id": "mail-write",
                    "provider": "email",
                    "kind": "email",
                    "address": "ops@example.com",
                    "policy": {"capabilities": ["write"], "auth_profile_ref": "email.ops"},
                },
            ]
        },
    )
    child_write = service.write_directory_domain_config(
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
                    "policy": {"capabilities": ["reply"]},
                },
                {
                    "id": "ops-discord",
                    "provider": "discord",
                    "kind": "discord",
                    "address": "123456789012345678",
                    "policy": {"capabilities": ["write"]},
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
            if isinstance(candidate, dict)
            and candidate["type"] == node_type
        )

    def get_field(
        payload: dict[str, object], node_type: str, field_key: str
    ) -> dict[str, object]:
        node = get_node(payload, node_type)
        field = next(
            candidate
            for candidate in node["fields"]
            if candidate["key"] == field_key
        )
        return field

    def get_options(
        payload: dict[str, object], node_type: str
    ) -> dict[str, dict[str, object]]:
        field = get_field(payload, node_type, "communication_target_id")
        assert field["kind"] in {"enum", "string"}
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

    send_target_description = get_field(
        root_payload, "effect:communication:send-message", "communication_target_id"
    )["description"]
    notify_target_description = get_field(
        root_payload, "effect:notification:send", "communication_target_id"
    )["description"]
    send_thread_description = get_field(
        root_payload, "effect:communication:send-message", "thread_id"
    )["description"]
    notify_thread_description = get_field(
        root_payload, "effect:notification:send", "thread_id"
    )["description"]

    assert "write or reply capability" in send_target_description
    assert "notify capability" in notify_target_description
    assert "top-level send" in send_thread_description
    assert "reply hint" in notify_thread_description

    assert set(send_root) == {"root-chat"}
    assert set(send_ops) == {"ops-chat", "ops-discord"}
    assert set(notify_root) == {"root-notify", "root-email-notify"}
    assert set(notify_ops) == {"root-notify", "root-email-notify", "ops-email-notify"}

    root_chat = send_root["root-chat"]
    assert root_chat["metadata"]["provider"] == "teams"
    assert root_chat["metadata"]["address"] == "team/channel"
    assert root_chat["metadata"]["target_shape"] == "teams_channel"
    assert root_chat["metadata"]["policy_capabilities"] == ["write"]
    assert root_chat["metadata"]["thread_id"]["top_level"]["supported"] is True
    assert root_chat["metadata"]["thread_id"]["reply"]["supported"] is False
    assert root_chat["metadata"]["thread_id"]["reply"]["supported_target_shapes"] == [
        "teams_channel"
    ]

    ops_chat = send_ops["ops-chat"]
    assert ops_chat["metadata"]["provider"] == "teams"
    assert ops_chat["metadata"]["target_shape"] == "teams_channel"
    assert ops_chat["metadata"]["policy_capabilities"] == ["reply"]
    assert ops_chat["metadata"]["thread_id"]["top_level"]["supported"] is False
    assert ops_chat["metadata"]["thread_id"]["reply"]["supported"] is True

    ops_discord = send_ops["ops-discord"]
    assert ops_discord["metadata"]["provider"] == "discord"
    assert ops_discord["metadata"]["target_shape"] == "discord_channel"
    assert ops_discord["metadata"]["policy_capabilities"] == ["write"]
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


def test_workflow_editor_default_dedupe_node_uses_supported_event_binding() -> None:
    app_source = (
        Path(__file__).resolve().parents[1] / "frontend" / "workflow-editor" / "src" / "App.tsx"
    ).read_text(encoding="utf-8")

    assert "dedupe:${{system.execution.event_id}}" in app_source
    assert "dedupe:${{system.event_id}}" not in app_source


def test_admin_web_exposes_deployment_status_from_runtime(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
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
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AUTH_MODE", "local_password")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_PASSWORD", "secret")
    service = AdminService(write_config(tmp_path))
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
    service = AdminService(write_config(tmp_path))
    monkeypatch.setenv("AGENT_SANDBOX_REPO_VERSION", "build-abc123")
    monkeypatch.setattr(
        release_metadata_module.importlib_metadata,
        "version",
        lambda name: (_ for _ in ()).throw(
            release_metadata_module.importlib_metadata.PackageNotFoundError(name)
        ),
    )
    (service.project_dir / "pyproject.toml").write_text(
        "[project]\nname = \"agent-sandbox\"\nversion = \"9.8.7\"\n",
        encoding="utf-8",
    )
    (service.project_dir / "CHANGELOG.md").write_text(
        "# Changelog\n\n"
        "## 2026-05-21 - Release Metadata\n\n"
        "- Added release metadata reader.\n"
        "- Fixed missing build identity fallback.\n",
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
    client = TestClient(create_app(service))

    response = client.get("/api/admin/release-info")
    older_response = client.get("/api/admin/release-info?news_index=1")
    invalid_response = client.get("/api/admin/release-info?news_index=-1")

    assert response.status_code == 200
    assert response.json() == {
        "version": "9.8.7",
        "version_source": str(service.project_dir / "pyproject.toml"),
        "build": "build-abc123",
        "build_source": "env:AGENT_SANDBOX_REPO_VERSION",
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
            "paragraphs": [
                "Operators now get release context and deployment hints in one place.",
                "The dashboard news feed explains what changed and how to use it.",
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
        "paragraphs": [
            "Older posts remain available without loading the whole news archive.",
        ],
        "has_previous": True,
        "has_next": False,
        "previous_index": 0,
        "next_index": None,
    }
    assert invalid_response.status_code == 400
    assert invalid_response.json()["error"] == "news_index must be greater than or equal to 0."

    download_response = client.get("/api/admin/release-info/changelog")
    assert download_response.status_code == 200
    assert download_response.content.startswith(b"# Changelog\n")
    assert "attachment" in download_response.headers["content-disposition"]
    assert "CHANGELOG.md" in download_response.headers["content-disposition"]


def test_admin_web_release_info_is_protected_when_auth_required(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AUTH_MODE", "local_password")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_PASSWORD", "secret")
    service = AdminService(write_config(tmp_path))
    client = TestClient(create_app(service))

    protected_response = client.get("/api/admin/release-info")
    protected_download_response = client.get("/api/admin/release-info/changelog")
    state_response = client.get("/api/admin/state")

    assert protected_response.status_code == 401
    assert protected_download_response.status_code == 401
    assert state_response.status_code == 401


def test_admin_web_ignores_stale_deployment_status(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
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
    service = AdminService(write_config(tmp_path))
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
    service = AdminService(write_config(tmp_path))
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
    service.read_capacity_health = MethodType(lambda self: payload, service)
    client = TestClient(create_app(service))

    response = client.get("/api/admin/capacity-health")

    assert response.status_code == 200
    response_payload = response.json()
    assert response_payload["control_plane"]["disk"]["scope"] == "control_plane_runtime"
    assert response_payload["sessions"]["directory_counts"] == {"root": 2}
    assert response_payload["workers"]["entries"][0]["telemetry_status"] == "missing"
    assert response_payload["queue"]["oldest_event_id"] == "evt-1"
    assert response_payload["prompts"]["status"] == "missing"


def test_admin_web_capacity_health_requires_auth_when_enabled(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AUTH_MODE", "local_password")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_PASSWORD", "secret")
    service = AdminService(write_config(tmp_path))
    client = TestClient(create_app(service))

    response = client.get("/api/admin/capacity-health")

    assert response.status_code == 401


def test_admin_web_release_info_uses_project_metadata(
    monkeypatch, tmp_path: Path
) -> None:
    service = AdminService(write_config(tmp_path))
    monkeypatch.setenv("AGENT_SANDBOX_REPO_VERSION", "build-abc123")
    monkeypatch.setattr(
        release_metadata_module.importlib_metadata,
        "version",
        lambda name: (_ for _ in ()).throw(
            release_metadata_module.importlib_metadata.PackageNotFoundError(name)
        ),
    )
    (service.project_dir / "pyproject.toml").write_text(
        "[project]\nname = \"agent-sandbox\"\nversion = \"9.8.7\"\n",
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

    release_info = service.read_release_info()
    older_release_info = service.read_release_info(news_index=1)

    assert release_info.version == "9.8.7"
    assert release_info.version_source == str(service.project_dir / "pyproject.toml")
    assert release_info.build == "build-abc123"
    assert release_info.build_source == "env:AGENT_SANDBOX_REPO_VERSION"
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
    assert release_info.news.paragraphs == [
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
    assert older_release_info.news.paragraphs == [
        "Older posts remain available without loading the whole news archive.",
    ]
    assert older_release_info.news.has_previous is True
    assert older_release_info.news.has_next is False
    assert older_release_info.news.previous_index == 0
    assert older_release_info.news.next_index is None


def test_admin_web_release_info_reports_missing_sources(
    monkeypatch, tmp_path: Path
) -> None:
    service = AdminService(write_config(tmp_path))
    monkeypatch.delenv("AGENT_SANDBOX_REPO_VERSION", raising=False)
    monkeypatch.delenv("agent_sandbox_repo_version", raising=False)
    monkeypatch.delenv("AGENT_SANDBOX_BUILD_VERSION", raising=False)
    monkeypatch.delenv("agent_sandbox_build_version", raising=False)
    monkeypatch.setattr(
        release_metadata_module.importlib_metadata,
        "version",
        lambda name: (_ for _ in ()).throw(
            release_metadata_module.importlib_metadata.PackageNotFoundError(name)
        ),
    )

    release_info = service.read_release_info()

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
    client = TestClient(create_app(AdminService(write_config(tmp_path))))

    create_response = client.post(
        "/api/admin/skills",
        json={"name": "demo.skill", "content": "# Demo\n", "replace": False},
    )

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


def test_admin_web_skills_api_rejects_json_errors(tmp_path: Path) -> None:
    client = TestClient(create_app(AdminService(write_config(tmp_path))))

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

    payload["expected_revision"] = _workflow_expected_revision(client)
    save_response = client.put("/api/workflow-editor/document", json=payload)

    assert save_response.status_code == 200
    assert save_response.json()["config"]["edges"] == []


def test_admin_web_workflow_editor_save_requires_matching_revision(tmp_path: Path) -> None:
    client = TestClient(create_app(AdminService(write_config(tmp_path))))
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
            "edges": [
                {"source_id": "trigger-ticket-created-main", "target_id": "prompt-agent"}
            ],
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
                    "repo_source": "git@example.invalid:repo.git",
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
    client = TestClient(create_app(AdminService(write_config(tmp_path))))
    payload = _workflow_binding_validation_payload(
        tmp_path,
        "Review ${{deleted-ticket-node.ticket.title}}",
    )

    validate_response = client.post("/api/workflow-editor/validate", json=payload)
    payload["expected_revision"] = _workflow_expected_revision(client)
    save_response = client.put("/api/workflow-editor/document", json=payload)

    assert validate_response.status_code == 200
    assert save_response.status_code == 200
    diagnostics = validate_response.json()["validation_diagnostics"]
    assert {
        "code": "invalid_binding_node",
        "node_id": "prompt-agent",
        "node_type": "effect:agent:prompt",
        "field_path": "prompt",
        "reference": "deleted-ticket-node.ticket.title",
        "message": "prompt references deleted node deleted-ticket-node",
    } in diagnostics
    assert save_response.json()["validation_diagnostics"] == diagnostics


def test_admin_web_save_seeds_new_ticket_created_trigger_cursor(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
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
    client = TestClient(create_app(AdminService(write_config(tmp_path))))
    payload = _workflow_binding_validation_payload(
        tmp_path,
        "Review ${{trigger-ticket-created-main.missing_output}}",
    )

    response = client.post("/api/workflow-editor/validate", json=payload)

    assert response.status_code == 200
    assert {
        "code": "missing_binding_output",
        "node_id": "prompt-agent",
        "node_type": "effect:agent:prompt",
        "field_path": "prompt",
        "reference": "trigger-ticket-created-main.missing_output",
        "message": "prompt references missing output trigger-ticket-created-main.missing_output",
    } in response.json()["validation_diagnostics"]


def test_admin_web_validate_preserves_domain_diagnostics_for_for_gate(
    tmp_path: Path,
) -> None:
    client = TestClient(create_app(AdminService(write_config(tmp_path))))
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
    } in diagnostics


def test_admin_web_validate_exposes_backend_output_schemas_for_agent_effects(
    tmp_path: Path,
) -> None:
    client = TestClient(create_app(AdminService(write_config(tmp_path))))
    payload = _agent_create_clone_validation_payload(tmp_path)

    response = client.post("/api/workflow-editor/validate", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["validation_diagnostics"] == []
    assert body["output_schemas"]["effect-agent-create-1"]["session_id"] == {"type": "string"}
    assert body["output_schemas"]["effect-agent-clone-repo-1"]["target_dir"] == {
        "type": "string"
    }


def test_admin_web_validate_scopes_binding_diagnostics_to_workflow_graphs(
    tmp_path: Path,
) -> None:
    client = TestClient(create_app(AdminService(write_config(tmp_path))))
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
            "repo_source": "git@example.invalid:repo.git",
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
    assert all(diagnostic.get("node_id") != "effect-agent-clone-repo-1" for diagnostic in diagnostics)
    assert {
        "code": "invalid_binding_node",
        "node_id": "effect-agent-clone-repo-copy",
        "node_type": "effect:agent:clone-repo",
        "field_path": "target",
        "reference": "effect-agent-create-1.session_id",
        "message": "target references deleted node effect-agent-create-1",
    } in diagnostics


def test_admin_web_validate_reports_malformed_binding_fallback(tmp_path: Path) -> None:
    client = TestClient(create_app(AdminService(write_config(tmp_path))))
    payload = _workflow_binding_validation_payload(
        tmp_path,
        "Review ${{trigger-ticket-created-main.ticket ?? {}}}",
    )

    response = client.post("/api/workflow-editor/validate", json=payload)

    assert response.status_code == 200
    assert {
        "code": "malformed_binding_fallback",
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

    client = TestClient(create_app(AdminService(write_config(tmp_path))))

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
    client = TestClient(create_app(AdminService(write_config(tmp_path))))

    list_response = client.get("/api/admin/artifacts?limit=1")
    detail_response = client.get("/api/admin/artifacts/runs%2Fsession-1%2Freport")
    filtered_response = client.get(
        "/api/admin/artifacts?published_after=2026-05-20T08:30:00Z"
    )
    download_response = client.get("/api/admin/artifact-downloads/runs%2Fsession-1%2Freport")

    assert list_response.status_code == 200
    assert list_response.json()["total"] == 2
    assert list_response.json()["items"][0]["key"] == "runs/session-2/report"
    assert list_response.json()["next_cursor"] == "1"
    assert detail_response.status_code == 200
    assert detail_response.json()["key"] == "runs/session-1/report"
    assert detail_response.json()["filename"] == "report.tar.gz"
    assert filtered_response.status_code == 200
    assert [item["key"] for item in filtered_response.json()["items"]] == [
        "runs/session-2/report"
    ]
    assert download_response.status_code == 200
    assert download_response.content == b"published archive"
    assert (
        download_response.headers["content-disposition"]
        == 'attachment; filename="report.tar.gz"'
    )


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
    client = TestClient(create_app(AdminService(write_config(tmp_path))))

    detail_response = client.get("/api/admin/artifacts/runs%2Ffoo%2Fdownload")
    download_response = client.get("/api/admin/artifact-downloads/runs%2Ffoo%2Fdownload")

    assert detail_response.status_code == 200
    assert detail_response.json()["key"] == "runs/foo/download"
    assert download_response.status_code == 200
    assert download_response.content == b"published archive"


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
                "inputs_by_source": {
                    "effect-prompt": {"issue_number": "99"}
                },
                "input_event_ids_by_source": {
                    "effect-prompt": "evt-detail"
                },
                "input_occurred_at_by_source": {
                    "effect-prompt": "2026-05-01T10:00:00Z"
                },
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
            }
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
    client = TestClient(create_app(AdminService(write_config(tmp_path))))

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
    assert rich_node["records"][0]["binding_trace"]["entries"][0]["resolved_value_preview"] == '"99"'


def test_admin_web_run_detail_requires_real_audit_identifiers(tmp_path: Path) -> None:
    _seed_workflow_run_detail_fixture(tmp_path)
    client = TestClient(create_app(AdminService(write_config(tmp_path))))

    response = client.get(
        "/api/workflow-editor/run-detail", params={"event_id": "evt-notrace"}
    )

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
    client = TestClient(create_app(AdminService(write_config(tmp_path))))

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
    client = TestClient(create_app(AdminService(write_config(tmp_path))))

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

    client = TestClient(create_app(AdminService(write_config(tmp_path))))

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
    assert response.json()["role"] == "admin"
    assert response.json()["can_manage_users"] is True


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
    assert login.json()["role"] == "admin"
    assert login.json()["can_manage_users"] is True
    assert authorized.status_code == 200


def test_workflow_event_api_mints_token_and_publishes_external_event(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_USERNAME", "operator")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_PASSWORD", "swordfish")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_SESSION_SECRET", "test-secret")
    service = AdminService(write_config(tmp_path))
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
    app = create_app(AdminService(write_config(tmp_path)))
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
        "AGENT_SANDBOX_WORKFLOW_EVENT_MACHINE_TOKENS",
        "ado-pipeline:ado-secret, github-action:github-secret",
    )
    client = TestClient(create_app(AdminService(write_config(tmp_path))))

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
    monkeypatch.setenv("AGENT_SANDBOX_WORKFLOW_EVENT_MACHINE_TOKENS", configured_tokens)

    with pytest.raises(ValueError, match=expected_error):
        AdminWebConfig.from_env()


def test_workflow_event_api_token_from_azure_session_publishes_user_metadata(
    tmp_path: Path, monkeypatch
) -> None:
    _set_azure_sso_env(monkeypatch)
    app = create_app(AdminService(write_config(tmp_path)))
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
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    service = AdminService(write_config(tmp_path))
    client = TestClient(create_app(service))
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
    assert result.events_published == 1
    assert len(pending) == 1
    assert pending[0].trigger_type == "trigger:event"
    assert pending[0].payload["event_id"] == publish_response.json()["event_id"]
    assert pending[0].payload["event_type"] == "external.deploy_requested"
    assert pending[0].payload["payload"] == {"target": "demo"}


def test_workflow_webhook_ingress_publishes_internal_event_and_bypasses_auth(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AUTH_MODE", "local_password")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_USERNAME", "operator")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_PASSWORD", "swordfish")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_SESSION_SECRET", "test-secret")
    monkeypatch.setenv("WEBHOOK_SECRET", "webhook-secret")
    _write_workflow_webhook_config(tmp_path)

    service = AdminService(write_config(tmp_path))
    client = TestClient(create_app(service))

    body = json.dumps(
        {"target": "demo", "options": {"dry_run": True}},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    headers = _signed_webhook_headers(body, secret="webhook-secret")

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
    assert response.json()["payload"]["slug"] == "build-notify"
    assert response.json()["payload"]["body"] == {"target": "demo", "options": {"dry_run": True}}
    assert response.json()["payload"]["query"] == {"source": "ci"}
    assert response.json()["payload"]["signature_header"] == headers["X-Agent-Sandbox-Signature-256"]
    assert response.json()["payload"]["received_at"].endswith("Z")
    assert response.json()["payload"]["remote_addr"]

    events = InternalEventBus(tmp_path / "runtime" / "internal-events.json").list_events()
    assert len(events) == 1
    assert events[0].event_type == "external.webhook.build-notify"
    assert events[0].idempotency_key == f"sha256={hashlib.sha256(body).hexdigest()}"


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
    client = TestClient(create_app(AdminService(write_config(tmp_path))))

    body = b"{\"target\":\"demo\"}"
    headers = {"Content-Type": "application/json"}
    if header_value is not None:
        headers["X-Agent-Sandbox-Signature-256"] = header_value

    response = client.post("/api/workflow-webhooks/build-notify", content=body, headers=headers)

    assert response.status_code == 401
    assert response.json()["error"] == expected_error


def test_workflow_webhook_ingress_rejects_bad_hmac(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("WEBHOOK_SECRET", "webhook-secret")
    _write_workflow_webhook_config(tmp_path)
    client = TestClient(create_app(AdminService(write_config(tmp_path))))

    body = b"{\"target\":\"demo\"}"
    response = client.post(
        "/api/workflow-webhooks/build-notify",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Agent-Sandbox-Signature-256": "sha256=" + ("0" * 64),
        },
    )

    assert response.status_code == 401
    assert response.json()["error"] == "Webhook signature is invalid or missing"


def test_workflow_webhook_ingress_rejects_invalid_json_and_non_object_body(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("WEBHOOK_SECRET", "webhook-secret")
    _write_workflow_webhook_config(tmp_path)
    client = TestClient(create_app(AdminService(write_config(tmp_path))))

    invalid_json = client.post(
        "/api/workflow-webhooks/build-notify",
        content=b"not-json",
        headers={
            "Content-Type": "application/json",
            "X-Agent-Sandbox-Signature-256": "sha256=" + ("1" * 64),
        },
    )
    non_object_json = client.post(
        "/api/workflow-webhooks/build-notify",
        content=b"[1,2,3]",
        headers={
            "Content-Type": "application/json",
            "X-Agent-Sandbox-Signature-256": "sha256=" + ("1" * 64),
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
    client = TestClient(create_app(AdminService(write_config(tmp_path))))

    body = b"{\"target\":\"demo\"}"
    response_missing_secret = client.post(
        "/api/workflow-webhooks/build-notify",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Agent-Sandbox-Signature-256": "sha256=" + ("1" * 64),
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
    assert response_unknown_slug.json()["error"] == "Webhook trigger not found for slug: build-notify"


def test_workflow_webhook_ingress_rejects_duplicate_active_slug(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("WEBHOOK_SECRET", "webhook-secret")
    _write_workflow_webhook_config(tmp_path, duplicate=True)
    client = TestClient(create_app(AdminService(write_config(tmp_path))))

    body = b"{\"target\":\"demo\"}"
    response = client.post(
        "/api/workflow-webhooks/build-notify",
        content=body,
        headers=_signed_webhook_headers(body, secret="webhook-secret"),
    )

    assert response.status_code == 409
    assert "Webhook trigger slugs must be globally unique across active config" in response.json()[
        "error"
    ]


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


def test_admin_web_config_treats_azure_allowed_users_as_bootstrap_input(monkeypatch) -> None:
    _set_azure_sso_env(monkeypatch, allowed_users=None)

    config = AdminWebConfig.from_env()

    assert config.auth_mode == "azure_sso"
    assert config.azure_sso is not None
    assert config.azure_sso.bootstrap_users == frozenset()


def test_admin_web_session_payload_reports_local_password_mode(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_PASSWORD", "swordfish")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_SESSION_SECRET", "test-secret")

    client = TestClient(create_app(AdminService(write_config(tmp_path))))
    response = client.get("/api/auth/session")

    assert response.status_code == 200
    assert response.json()["auth_mode"] == "local_password"
    assert response.json()["authenticated"] is False
    assert response.json()["role"] is None
    assert response.json()["can_manage_users"] is False
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
    assert response.json()["role"] is None
    assert response.json()["can_manage_users"] is False
    assert response.json()["available_login_methods"] == ["azure_sso"]
    assert response.json()["azure_sso_login_url"] == "/api/auth/azure-sso/start"


def test_admin_web_bootstraps_azure_users_from_env_on_first_startup(
    tmp_path: Path, monkeypatch
) -> None:
    _set_azure_sso_env(
        monkeypatch,
        allowed_users="Operator@Example.com,second@example.com, operator@example.com",
    )

    app = create_app(AdminService(write_config(tmp_path)))

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

    app = create_app(AdminService(config_path))

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
    app = create_app(AdminService(config_path))
    client = TestClient(app)

    callback = _complete_azure_login(app, client)
    session = client.get("/api/auth/session")
    admin_state = client.get("/api/admin/state")

    assert callback.status_code == 303
    assert session.json()["authenticated"] is True
    assert session.json()["role"] == "admin"
    assert session.json()["can_manage_users"] is True
    assert session.json()["display_name"] == "Runtime Operator"
    assert admin_state.json()["role"] == "admin"
    assert admin_state.json()["can_manage_users"] is True
    assert app.state.admin_user_store.get_user("operator@example.com").last_login_at is not None


def test_admin_web_state_exposes_effective_execution_backend(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    client = TestClient(create_app(service))

    root_state = client.get("/api/admin/state")
    create_directory = client.post(
        "/api/configuration/directories",
        json={"parent_path": "root", "name": "ops"},
    )
    write_backend = client.put(
        "/api/configuration/domains/providers?directory_path=root/ops",
        json={
            "config": {"execution_backend": "container_worker_vm"},
            "expected_revision": _configuration_expected_revision(
                client, "root/ops", "providers"
            ),
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
            AdminService(write_config(tmp_path, execution_backend="container_worker_vm"))
        )
    )

    response = client.get("/api/admin/state")

    assert response.status_code == 200
    assert response.json()["execution_backend"] == "container_worker_vm"


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
    assert session.json()["role"] == "admin"
    assert session.json()["can_manage_users"] is True


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


def test_admin_web_removed_azure_user_loses_existing_session(tmp_path: Path, monkeypatch) -> None:
    _set_azure_sso_env(monkeypatch, allowed_users="operator@example.com,backup@example.com")
    app = create_app(AdminService(write_config(tmp_path)))
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
    assert after_removal.status_code == 401


def test_admin_web_user_api_manages_users_as_builtin_admin(tmp_path: Path) -> None:
    client = TestClient(create_app(AdminService(write_config(tmp_path))))

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
    client = TestClient(create_app(AdminService(write_config(tmp_path))))
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
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    service.write_directory_domain_config(
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
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    service.create_directory_child("root", "team")
    service.write_directory_domain_config(
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


def test_admin_web_user_api_rejects_regular_azure_user(tmp_path: Path, monkeypatch) -> None:
    config_path = write_config(tmp_path)
    store = _admin_web_user_store(tmp_path)
    store.upsert_user(login="admin@example.com", role="admin")
    store.upsert_user(login="regular@example.com", role="regular")
    _set_azure_sso_env(monkeypatch, allowed_users=None)
    app = create_app(AdminService(config_path))
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


def test_admin_web_exposes_admin_domain_smoke_endpoints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = AdminService(write_config(tmp_path))
    monkeypatch.setattr(service, "list_vms", lambda **kwargs: [])
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
    assert responses["conformance_reports"].status_code == 200
    assert len(responses["conformance_reports"].json()["reports"]) == 2


def test_workflow_generator_returns_validated_draft_without_writing_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = AdminService(write_config(tmp_path))
    service.write_assistant_settings({"workflow_generation_model": "gpt-workflow-test"})
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

    service.workflow_generation_transport = httpx.MockTransport(handler)
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
    assert payload["config"]["edges"] == [
        {"source_id": "schedule", "target_id": "emit-summary"}
    ]
    assert payload["warnings"] == ["Review placeholder payload values before saving."]
    assert captured_requests
    request_payload = captured_requests[0]["payload"]
    assert request_payload["model"] == "gpt-workflow-test"
    assert request_payload["text"]["format"]["type"] == "json_schema"
    assert request_payload["text"]["format"]["strict"] is True
    assert captured_requests[0]["headers"]["authorization"] == "Bearer sk-test"
    assert not (tmp_path / "config" / "trigger-orchestrator" / "config.json").exists()
    assert not (tmp_path / "config" / "trigger-orchestrator" / "layout.json").exists()


def test_workflow_generator_rejects_cyclic_generated_graph_without_hanging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = AdminService(write_config(tmp_path))
    service.write_assistant_settings({"workflow_generation_model": "gpt-workflow-test"})
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

    service.workflow_generation_transport = httpx.MockTransport(handler)
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
    service = AdminService(write_config(tmp_path))
    service.write_assistant_settings({"workflow_generation_model": "gpt-workflow-test"})
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    service.workflow_generation_transport = httpx.MockTransport(handler)
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
    service = AdminService(write_config(tmp_path))
    service.write_assistant_settings({"workflow_generation_model": "gpt-workflow-test"})
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    model_request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal model_request_count
        model_request_count += 1
        return httpx.Response(200, json=_workflow_generator_success_response())

    service.workflow_generation_transport = httpx.MockTransport(handler)
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
    service = AdminService(write_config(tmp_path))
    service.write_assistant_settings({"workflow_generation_model": "gpt-workflow-test"})
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

        service.workflow_generation_transport = httpx.MockTransport(handler)
        first_task = asyncio.create_task(
            service.generate_workflow_editor_draft(
                description="Every night, emit a generated summary event.",
                directory_path="root",
            )
        )
        try:
            await asyncio.wait_for(started.wait(), timeout=1.0)
            with pytest.raises(ValueError, match="already in progress"):
                await service.generate_workflow_editor_draft(
                    description="Every morning, emit a generated summary event.",
                    directory_path="root",
                )
        finally:
            release.set()
        await first_task

    asyncio.run(run_case())
    assert model_request_count == 1


def test_workflow_generator_requires_configured_model(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
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
    service = AdminService(write_config(tmp_path))
    calls = 0
    capacity_calls = 0

    def list_background_workers() -> list[WorkerSummary]:
        nonlocal calls
        calls += 1
        return [
            WorkerSummary(
                worker_id="trigger",
                name="Trigger",
                unit="agent-sandbox-trigger-orchestrator.service",
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

    monkeypatch.setattr(service, "list_background_workers", list_background_workers)
    monkeypatch.setattr(service, "read_capacity_health", read_capacity_health)
    client = TestClient(create_app(service))

    first = client.get("/api/workers?_dashboard=1")
    second = client.get("/api/workers?_dashboard=1")
    live = client.get("/api/workers")
    refreshed = client.get("/api/workers?_dashboard=1&_dashboard_refresh=1")
    capacity_first = client.get("/api/admin/capacity-health?_dashboard=1")
    capacity_second = client.get("/api/admin/capacity-health?_dashboard=1")
    capacity_live = client.get("/api/admin/capacity-health")
    capacity_refreshed = client.get(
        "/api/admin/capacity-health?_dashboard=1&_dashboard_refresh=1"
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert live.status_code == 200
    assert refreshed.status_code == 200
    assert capacity_first.status_code == 200
    assert capacity_second.status_code == 200
    assert capacity_live.status_code == 200
    assert capacity_refreshed.status_code == 200
    assert first.headers["x-admin-web-cache"] == "miss"
    assert second.headers["x-admin-web-cache"] == "hit"
    assert "x-admin-web-cache" not in live.headers
    assert refreshed.headers["x-admin-web-cache"] == "refresh"
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
    client = TestClient(create_app(AdminService(config_path)))

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
    service = AdminService(write_config(tmp_path))
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

    monkeypatch.setattr(service, "run_conformance_report", fake_run)

    response = client.post("/api/admin/reports/conformance/trigger/run")

    assert response.status_code == 200
    assert calls == ["trigger"]
    assert response.json()["kind"] == "trigger"


def test_admin_web_active_issue_filter_excludes_done_issues(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    active_issue = service.create_local_issue(title="Active issue", body="body")
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
    assert [item["issue_id"] for item in active_response.json()["items"]] == [
        active_issue.issue_id
    ]
    assert {item["issue_id"] for item in all_response.json()["items"]} == {
        active_issue.issue_id,
        done_issue.issue_id,
    }


def test_admin_web_workers_expose_config_schema(tmp_path: Path) -> None:
    client = TestClient(create_app(AdminService(write_config(tmp_path))))

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
    client = TestClient(create_app(AdminService(write_config(tmp_path))))
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
    assert saved.json()["config"]["config"]["poll_interval_seconds"] == config[
        "poll_interval_seconds"
    ]


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
        create_app(AdminService(write_config(tmp_path, enable_directory_scoped_config=True)))
    )

    response = client.get("/api/configuration/state")

    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload["tab_snapshots"]["communications"]["revision"], str)
    assert isinstance(payload["snapshots"]["providers"]["revision"], str)
    communications = next(tab for tab in payload["tabs"] if tab["key"] == "communications")
    assert communications["title"] == "Communications"
    assert any(section["title"] == "Discord" for section in communications["sections"])
    communication_targets = next(
        section
        for section in communications["sections"]
        if section["title"] == "Communication targets"
    )
    target_fields = {field["field_name"]: field for field in communication_targets["fields"]}
    assert target_fields["targets"]["source_domain"] == "communication_targets"
    assert target_fields["targets"]["kind"] == "json"
    assert target_fields["targets"]["root_starts_editable"] is True
    assert "workflow notification" in target_fields["targets"]["tooltip"]
    assistant = next(
        section for section in communications["sections"] if section["title"] == "Assistant"
    )
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
    provider_fields = {
        field["field_name"]: field
        for tab in payload["tabs"]
        if tab["key"] == "providers"
        for section in tab["sections"]
        for field in section["fields"]
    }
    assert "container worker service" in provider_fields["container_worker_base_url"]["tooltip"]
    assert "Value: one of" in provider_fields["execution_backend"]["tooltip"]
    for tab in payload["tabs"]:
        for section in tab["sections"]:
            for field in section["fields"]:
                assert field["tooltip"], f"{tab['key']}.{field['field_name']} is missing a tooltip"
                assert "Value:" in field["tooltip"]
    access_policy = next(tab for tab in payload["tabs"] if tab["key"] == "access_policy")
    approval_notifications = next(
        section
        for section in access_policy["sections"]
        if section["title"] == "Approval notifications"
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
    communication_files = next(
        section
        for section in access_policy["sections"]
        if section["title"] == "Communication files"
    )
    communication_file_fields = {field["field_name"] for field in communication_files["fields"]}
    assert "communication_file_receive_enabled" in communication_file_fields
    assert "communication_file_require_approval_for_send" in communication_file_fields
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
    providers = next(tab for tab in payload["tabs"] if tab["key"] == "providers")
    agent_execution = next(
        section for section in providers["sections"] if section["title"] == "Agent execution"
    )
    agent_execution_fields = {
        field["field_name"]: field for field in agent_execution["fields"]
    }
    assert agent_execution_fields["execution_backend"]["label"] == "Execution backend"
    container_worker = next(
        section
        for section in providers["sections"]
        if section["title"] == "Container worker settings"
    )
    container_worker_fields = {field["field_name"] for field in container_worker["fields"]}
    assert "container_worker_base_url" in container_worker_fields
    assert "container_worker_mssql_enabled" in container_worker_fields
    semantic_store = next(
        section for section in providers["sections"] if section["title"] == "Semantic store"
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
    retell = next(section for section in providers["sections"] if section["title"] == "Retell")
    assert {field["field_name"] for field in retell["fields"]} == {
        "retell_api_base_url",
        "retell_api_key_env",
        "retell_from_number",
        "retell_default_agent_id",
        "retell_request_timeout_seconds",
    }
    contacts = next(tab for tab in payload["tabs"] if tab["key"] == "contacts")
    contact_list = next(
        section for section in contacts["sections"] if section["title"] == "Contact list"
    )
    assert {field["field_name"] for field in contact_list["fields"]} == {
        "contacts",
    }
    assert "communications" in payload["tab_snapshots"]
    assert payload["selected_directory"]["path"] == "root"


def test_admin_web_contacts_tab_save_preserves_user_contact_links(
    tmp_path: Path,
) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    service.write_directory_domain_config(
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

    response = client.put(
        "/api/configuration/domains/contacts?directory_path=root",
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
            "expected_revision": _configuration_expected_revision(client, "root", "contacts"),
        },
    )

    assert response.status_code == 200
    saved = service.read_directory_domain_config("root", "contacts")
    assert saved.ok
    assert saved.data.config["contacts"]["support"]["name"] == "Support"
    assert saved.data.config["user_contact_links"] == {"operator@example.com": "ops"}


def test_admin_web_configuration_state_exposes_unified_directory_overrides(
    tmp_path: Path,
) -> None:
    client = TestClient(
        create_app(AdminService(write_config(tmp_path, enable_directory_scoped_config=True)))
    )
    create_response = client.post(
        "/api/configuration/directories",
        json={"parent_path": "root", "name": "ops"},
    )
    assert create_response.status_code == 200

    communications_response = client.put(
        "/api/configuration/domains/communications?directory_path=root/ops",
        json={
            "config": {
                "discord_backend": "stub",
                "discord_operator_channel_allowlist": ["chan-1"],
                "discord_resume_prompt": "Resume this session.",
            },
            "expected_revision": _configuration_expected_revision(
                client, "root/ops", "communications"
            ),
        },
    )
    providers_response = client.put(
        "/api/configuration/domains/providers?directory_path=root/ops",
        json={
            "config": {"github_token_env": "GITHUB_TOKEN_OPS"},
            "expected_revision": _configuration_expected_revision(
                client, "root/ops", "providers"
            ),
        },
    )
    state_response = client.get("/api/configuration/state?directory_path=root/ops")

    assert communications_response.status_code == 200
    assert providers_response.status_code == 200
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
            },
            "expected_revision": _configuration_expected_revision(
                client, "ops", "communications"
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


def test_admin_web_configuration_tab_save_rejects_missing_and_stale_revision(
    tmp_path: Path,
) -> None:
    client = TestClient(
        create_app(AdminService(write_config(tmp_path, enable_directory_scoped_config=True)))
    )
    create_response = client.post(
        "/api/configuration/directories",
        json={"parent_path": "root", "name": "ops"},
    )
    assert create_response.status_code == 200
    initial_revision = _configuration_expected_revision(client, "ops", "communications")

    missing = client.put(
        "/api/configuration/domains/communications?directory_path=ops",
        json={"config": {"discord_backend": "stub"}},
    )
    first_save = client.put(
        "/api/configuration/domains/communications?directory_path=ops",
        json={
            "config": {"discord_backend": "stub"},
            "expected_revision": initial_revision,
        },
    )
    stale = client.put(
        "/api/configuration/domains/communications?directory_path=ops",
        json={
            "config": {"discord_operator_channel_allowlist": ["chan-2"]},
            "expected_revision": initial_revision,
        },
    )

    assert missing.status_code == 428
    assert first_save.status_code == 200
    assert first_save.json()["tab_snapshot"]["revision"] != initial_revision
    assert stale.status_code == 409
    assert stale.json()["details"]["current_revision"] == first_save.json()["tab_snapshot"][
        "revision"
    ]


def test_admin_web_communications_tab_saves_sqlite_communication_targets(
    tmp_path: Path,
) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    client = TestClient(create_app(service))

    root_response = client.put(
        "/api/configuration/domains/communications?directory_path=root",
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
                client, "root", "communications"
            ),
        },
    )
    create_response = client.post(
        "/api/configuration/directories",
        json={"parent_path": "root", "name": "ops"},
    )
    child_response = client.put(
        "/api/configuration/domains/communications?directory_path=root/ops",
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
                client, "root/ops", "communications"
            ),
        },
    )
    state_response = client.get("/api/configuration/state?directory_path=root/ops")

    assert root_response.status_code == 200
    assert create_response.status_code == 200
    assert child_response.status_code == 200
    payload = state_response.json()
    communications = payload["tab_snapshots"]["communications"]
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


def test_admin_web_communications_tab_saves_assistant_settings(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    client = TestClient(create_app(service))

    unlock = client.post("/api/admin/privileged/unlock")
    assert unlock.status_code == 200

    response = client.put(
        "/api/configuration/domains/communications?directory_path=root",
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
                client, "root", "communications"
            ),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    snapshot = payload["tab_snapshot"]
    assert snapshot["raw"]["config"]["enabled"] is True
    assert snapshot["raw"]["config"]["read_only"] is False
    assert snapshot["raw"]["config"]["custom_system_instructions"] == "Use the admin context."
    assert service.read_assistant_settings() == {
        "custom_system_instructions": "Use the admin context.",
        "default_model": "gpt-test",
        "default_profile": "full-admin",
        "enabled": True,
        "read_only": False,
        "show_launcher_when_disabled": True,
    }
    assert service.assistant_settings_path().exists()


def test_admin_web_communications_tab_assistant_only_save_preserves_directory_domains(
    tmp_path: Path,
) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    client = TestClient(create_app(service))

    create_response = client.post(
        "/api/configuration/directories",
        json={"parent_path": "root", "name": "ops"},
    )
    assert create_response.status_code == 200

    communications_response = client.put(
        "/api/configuration/domains/communications?directory_path=ops",
        json={
            "config": {
                "discord_backend": "stub",
                "discord_operator_channel_allowlist": ["chan-1"],
                "discord_resume_prompt": "Resume this session.",
            },
            "expected_revision": _configuration_expected_revision(
                client, "ops", "communications"
            ),
        },
    )
    assert communications_response.status_code == 200

    unlock = client.post("/api/admin/privileged/unlock")
    assert unlock.status_code == 200

    assistant_response = client.put(
        "/api/configuration/domains/communications?directory_path=ops",
        json={
            "config": {"enabled": True, "read_only": True, "default_model": "gpt-test"},
            "expected_revision": _configuration_expected_revision(
                client, "ops", "communications"
            ),
        },
    )
    assert assistant_response.status_code == 200

    providers_result = service.read_directory_domain_config("ops", "providers")
    policy_result = service.read_directory_domain_config("ops", "policy")
    wake_result = service.read_directory_domain_config("ops", "wake")
    assert providers_result.ok is True
    assert policy_result.ok is True
    assert wake_result.ok is True
    assert providers_result.data is not None
    assert policy_result.data is not None
    assert wake_result.data is not None
    assert providers_result.data.config["discord_backend"] == "stub"
    assert policy_result.data.config["discord_operator_channel_allowlist"] == ["chan-1"]
    assert wake_result.data.config["discord_resume_prompt"] == "Resume this session."
    assert service.read_assistant_settings()["default_model"] == "gpt-test"


def test_admin_web_assistant_settings_migrates_legacy_write_tool_profile(
    tmp_path: Path,
) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    path = service.assistant_settings_path()
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

    settings = service.read_assistant_settings()

    assert settings["default_profile"] == "full-admin"
    assert "allowed_mcp_tool_profile" not in settings


def test_admin_web_communications_tab_preserves_assistant_settings_when_absent(
    tmp_path: Path,
) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    service.write_assistant_settings(
        {
            "enabled": True,
            "show_launcher_when_disabled": True,
            "read_only": False,
            "default_model": "gpt-existing",
        }
    )
    client = TestClient(create_app(service))

    response = client.put(
        "/api/configuration/domains/communications?directory_path=root",
        json={
            "config": {"discord_backend": "stub"},
            "expected_revision": _configuration_expected_revision(
                client, "root", "communications"
            ),
        },
    )

    assert response.status_code == 200
    assert service.read_assistant_settings()["default_model"] == "gpt-existing"
    assert service.read_assistant_settings()["enabled"] is True


def test_admin_web_chat_settings_write_requires_privileged_unlock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_AUTH_MODE", "local_password")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_USERNAME", "operator")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_PASSWORD", "swordfish")
    monkeypatch.setenv("AGENT_SANDBOX_ADMIN_WEB_SESSION_SECRET", "test-secret")
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    client = TestClient(create_app(service))

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
    assert service.read_assistant_settings()["enabled"] is False

    unlock = client.post("/api/admin/privileged/unlock")
    assert unlock.status_code == 200
    saved = client.put(
        "/api/chat/settings",
        json={"enabled": True, "read_only": False, "default_profile": "full-admin"},
    )
    assert saved.status_code == 200
    assert service.read_assistant_settings()["default_profile"] == "full-admin"


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
        json={
            "config": {"openai_api_base_url": "http://relay.internal/v1"},
            "expected_revision": _configuration_expected_revision(client, "ops", "providers"),
        },
    )
    assert provider_response.status_code == 200

    communications_response = client.put(
        "/api/configuration/domains/communications?directory_path=ops",
        json={
            "config": {"discord_backend": "stub"},
            "expected_revision": _configuration_expected_revision(
                client, "ops", "communications"
            ),
        },
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
        json={
            "config": {},
            "expected_revision": _configuration_expected_revision(
                client, "ops", "communications"
            ),
        },
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


def test_admin_web_agents_list_includes_vms_and_container_sessions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    service.create_directory_child("root", "team-a")
    service.create_directory_child("root", "team-b")
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
    assert {item["agent_id"] for item in payload["items"]} == {"vm-a", "container-session-a"}
    container_item = next(
        item for item in payload["items"] if item["backend"] == "container_worker_vm"
    )
    assert container_item["status"] == "running"
    assert container_item["capabilities"]["logs"] is True
    assert container_item["host"]["execution_count"] == 2
    assert [item["execution_id"] for item in container_item["host"]["executions"]] == [
        "exec-a-2",
        "exec-a-1",
    ]
    assert list_vms_calls[0]["directory_path"] == "root/team-a"
    assert list_vms_calls[0]["direct_only"] is True

    root_response = client.get(
        "/api/admin/agents?directory_path=root&direct_only=true&include_inactive=true"
    )

    assert root_response.status_code == 200
    assert root_response.json()["items"] == []


def test_admin_web_agents_backend_filter_controls_vm_inventory_failures(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
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

    monkeypatch.setattr(service, "list_vms", MethodType(fail_list_vms, service))
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
    service = AdminService(write_config(tmp_path))
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
            if mode == "session":
                return "session transcript\n" if target == "exec-1" else ""
            if mode == "augmented":
                return "repo line for exec-1\n" if target == "exec-1" else "line 2\n"
            return "line 2\n"

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

    monkeypatch.setattr(service, "list_vms", MethodType(fail_list_vms, service))
    monkeypatch.setattr(
        service,
        "_container_worker_backend",
        MethodType(lambda self, **_kwargs: fake_backend, service),
    )

    client = TestClient(create_app(service))

    log_response = client.get("/api/admin/agents/session-1/log?backend=container_worker_vm&lines=4")
    paged_response = client.get(
        "/api/admin/agents/session-1/log?backend=container_worker_vm&limit=3&offset=0&query=repo"
    )
    stop_response = client.post(
        "/api/admin/agents/session-1/actions/stop?backend=container_worker_vm", json={}
    )
    destroy_response = client.post(
        "/api/admin/agents/exec-1/actions/destroy?backend=container_worker_vm", json={}
    )

    assert log_response.status_code == 200
    assert "session=session-1 log=codex_transcript" in log_response.json()["text"]
    assert "session transcript" in log_response.json()["text"]
    assert "execution=exec-2" in log_response.json()["text"]
    assert "execution=exec-1" in log_response.json()["text"]
    assert "log=raw" in log_response.json()["text"]
    assert paged_response.status_code == 200
    assert paged_response.json()["text"] == "repo line for exec-1\n"
    assert paged_response.json()["has_more_before"] is False
    assert paged_response.json()["total_lines"] == 1
    assert stop_response.status_code == 200
    assert stop_response.json()["status"] == "stopped"
    assert destroy_response.status_code == 200
    assert destroy_response.json()["status"] == "destroyed"
    assert fake_backend.inspected == []
    assert fake_backend.logged == [
        ("exec-1", 4, "session"),
        ("exec-2", 2, "raw"),
        ("exec-1", 2, "raw"),
        ("exec-2", 0, "raw"),
        ("exec-1", 0, "augmented"),
    ]
    assert fake_backend.paged == []
    assert fake_backend.stopped == ["exec-1"]
    assert fake_backend.destroyed == ["exec-1"]


def test_admin_web_agents_create_vm_uses_agent_crud_payload(tmp_path: Path, monkeypatch) -> None:
    service = AdminService(write_config(tmp_path))
    service.create_skill({"name": "exampleindustry-app-demo", "content": "# Demo\n"})
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

    monkeypatch.setattr(service, "create_vm", MethodType(create_vm, service))
    client = TestClient(create_app(service))

    response = client.post(
        "/api/admin/agents",
        json={
            "backend": "libvirt_vm",
            "provider": "libvirt",
            "vm_name": "lab-vm",
            "session_id": "lab-session",
            "directory_path": "root/team-a",
            "model": "gpt-5.2-codex",
            "ttl_hours": 12,
            "labels": {"team": "a"},
            "allowed_tools": ["run_script"],
            "allowed_script_names": ["demo"],
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
        "ttl_hours": 12,
        "allowed_tools": ["run_script"],
        "allowed_script_names": ["demo"],
        "discord_channels": ["ops"],
        "teams_channel_allowlist": ["team-a/channel-1", "chat:chat-1"],
        "teams_user_allowlist": None,
        "email_mailboxes": ["default"],
        "email_folders": ["inbox"],
        "communication_file_receive_enabled": True,
        "communication_file_send_enabled": True,
        "communication_file_max_bytes": 4096,
        "communication_file_require_approval_for_send": True,
        "dotnet": True,
        "nuget": True,
        "mssql": True,
        "mssql_init_root": "/tmp/init",
        "playwright": True,
        "codex_skills": ["exampleindustry-app-demo"],
        "directory_path": "root/team-a",
    }


def test_admin_web_agents_create_vm_forwards_supplied_teams_chat_allowlist(
    tmp_path: Path, monkeypatch
) -> None:
    service = AdminService(write_config(tmp_path))
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

    monkeypatch.setattr(service, "create_vm", MethodType(create_vm, service))
    client = TestClient(create_app(service))

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
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    created = service.create_directory_child("root", "team-a")
    assert created.ok is True and created.data is not None
    team_path = created.data.details.directory.path

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
        service,
        "_container_worker_backend",
        MethodType(lambda self, **_kwargs: fake_backend, service),
    )
    monkeypatch.setattr(service, "list_vms", MethodType(lambda self, **kwargs: [], service))
    client = TestClient(create_app(service))

    create_response = client.post(
        "/api/admin/agents",
        json={
            "backend": "container_worker_vm",
            "session_id": "container-session",
            "target_name": "container-target",
            "directory_path": team_path,
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
            },
        )
    ]
    assert fake_backend.written
    assert "/home/agent/.agent-sandbox/session.env" in fake_backend.written[0][1]
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


def test_admin_web_agents_patch_updates_session_policy_and_rejects_renames(
    tmp_path: Path,
) -> None:
    service = AdminService(write_config(tmp_path))
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
    assert response.json()["effective_tool_policy"]["tool_argument_policies"][
        "create_ado_backlog_item"
    ][0]["id"] == "policy"
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
    service = AdminService(write_config(tmp_path))
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

    monkeypatch.setattr("agent_sandbox.admin.service.subprocess.run", _fake_run)
    client = TestClient(create_app(service))

    response = client.delete("/api/admin/agents/lab-vm?backend=libvirt_vm")

    assert response.status_code == 200
    assert response.json()["status"] == "deleted"
    assert service.runtime.session_repository.get_session("lab-session") is None
    assert calls[0][-1] == "lab-vm"


def test_admin_web_agents_delete_container_cleans_mapping_and_session(
    tmp_path: Path, monkeypatch
) -> None:
    service = AdminService(write_config(tmp_path))
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
        service,
        "_container_worker_backend",
        MethodType(lambda self, **_kwargs: fake_backend, service),
    )
    client = TestClient(create_app(service))

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
    service = AdminService(write_config(tmp_path))
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
        service,
        "_container_worker_backend",
        MethodType(fake_container_worker_backend, service),
    )
    client = TestClient(create_app(service))

    response = client.delete("/api/admin/agents/container-session?backend=container_worker_vm")

    assert response.status_code == 200
    assert backend_kwargs == [{"directory_path": "root", "worker_url": "http://worker.test"}]
    assert fake_backend.deleted_sessions == ["container-session"]
    assert service.runtime.session_repository.get_session("container-session") is None
    assert repository.read_all() == {}


def test_admin_web_agents_delete_ambiguous_vm_session_link_fails(
    tmp_path: Path, monkeypatch
) -> None:
    service = AdminService(write_config(tmp_path))
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
        service,
        "destroy_vm",
        MethodType(lambda self, target: destroyed.append(target), service),
    )
    client = TestClient(create_app(service))

    response = client.delete("/api/admin/agents/lab-vm?backend=libvirt_vm")

    assert response.status_code == 400
    assert "multiple linked sessions" in response.json()["error"]
    assert destroyed == []
    assert service.runtime.session_repository.get_session("session-a") is not None
    assert service.runtime.session_repository.get_session("session-b") is not None


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
    response = client.post("/api/admin/agents/vm-1/actions/start?backend=libvirt_vm", json={})

    assert response.status_code == 200
    assert response.json()["backend"] == "libvirt_vm"
    assert called == ["vm-1"]


def test_admin_web_agent_prompt_action_uses_existing_vm_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = AdminService(write_config(tmp_path))
    calls: list[dict[str, object]] = []

    def prompt_admin_agent(
        self: AdminService,
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

    monkeypatch.setattr(service, "_prompt_admin_agent", MethodType(prompt_admin_agent, service))

    client = TestClient(create_app(service))
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
    service = AdminService(write_config(tmp_path, execution_backend="container_worker_vm"))
    calls: list[dict[str, object]] = []

    def prompt_admin_agent(
        self: AdminService,
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

    monkeypatch.setattr(service, "_prompt_admin_agent", MethodType(prompt_admin_agent, service))

    client = TestClient(create_app(service))
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
    service = AdminService(write_config(tmp_path))
    client = TestClient(create_app(service))

    response = client.post(
        "/api/admin/agents/vm-1/actions/prompt-agent?backend=libvirt_vm",
        json={"prompt": "   "},
    )

    assert response.status_code == 400
    assert response.json()["error"] == "prompt-agent requires a non-empty prompt"


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
