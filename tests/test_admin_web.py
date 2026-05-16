from __future__ import annotations

import asyncio
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MethodType
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from agent_sandbox.admin.service import AdminService
from agent_sandbox.admin.web.app import _workflow_run_events, create_app
from agent_sandbox.admin.web.shared import AdminWebConfig
from agent_sandbox.admin.web.user_store import AdminWebUserStore
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
    SqliteCommunicationTargetsRepository,
    communication_targets_db_path,
)
from agent_sandbox.models import (
    ApprovalRequest,
    LocalIssueRecord,
    SessionManifest,
    SessionSummary,
    ToolName,
    VmActionResult,
    VmSummary,
)
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
    ]
    agent_create_fields = {field["key"]: field for field in agent_create["fields"]}
    assert agent_create_fields["labels"]["kind"] == "string_map"
    assert agent_create_fields["labels"]["description"]
    assert agent_create_fields["directory_path"]["description"]
    assert agent_create_fields["allowed_tools"]["options"]
    assert {
        "value": ToolName.RUN_SCRIPT.value,
        "label": ToolName.RUN_SCRIPT.value.replace("_", " "),
    } in agent_create_fields["allowed_tools"]["options"]
    execution_backend = next(
        field for field in agent_create["fields"] if field["key"] == "execution_backend"
    )
    assert execution_backend["options"] == [
        {"value": "libvirt_vm", "label": "Libvirt VM"},
        {"value": "container_worker_vm", "label": "Container worker VM"},
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

    save_response = client.put("/api/workflow-editor/document", json=payload)

    assert save_response.status_code == 200
    assert save_response.json()["config"]["edges"] == []


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
        },
    )

    assert response.status_code == 200
    cursor_payload = json.loads(cursor_path.read_text(encoding="utf-8"))
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
        json={"config": {"execution_backend": "container_worker_vm"}},
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
    assert payload["config_schema"]["worker_id"] == worker_id
    assert any(field["field_name"] == "runtime_dir" for field in payload["config_schema"]["fields"])


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
    providers = next(tab for tab in payload["tabs"] if tab["key"] == "providers")
    container_worker = next(
        section for section in providers["sections"] if section["title"] == "Container worker"
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
            }
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
            }
        },
    )
    providers_response = client.put(
        "/api/configuration/domains/providers?directory_path=root/ops",
        json={"config": {"github_token_env": "GITHUB_TOKEN_OPS"}},
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
            }
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
            }
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
            }
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
            }
        },
    )
    assert communications_response.status_code == 200

    unlock = client.post("/api/admin/privileged/unlock")
    assert unlock.status_code == 200

    assistant_response = client.put(
        "/api/configuration/domains/communications?directory_path=ops",
        json={"config": {"enabled": True, "read_only": True, "default_model": "gpt-test"}},
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
        json={"config": {"discord_backend": "stub"}},
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
        "/api/admin/agents/session-1/log?backend=container_worker_vm&limit=3&offset=3&query=repo"
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
    assert paged_response.json()["text"] == "paged line\n"
    assert paged_response.json()["has_more_before"] is True
    assert paged_response.json()["total_lines"] == 8
    assert stop_response.status_code == 200
    assert stop_response.json()["status"] == "stopped"
    assert destroy_response.status_code == 200
    assert destroy_response.json()["status"] == "destroyed"
    assert fake_backend.inspected == []
    assert fake_backend.logged == [
        ("exec-1", 4, "session"),
        ("exec-2", 2, "raw"),
        ("exec-1", 2, "raw"),
    ]
    assert fake_backend.paged == [("exec-1", 3, 3, "repo")]
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
    updated = service.runtime.session_repository.get_session("lab-session")
    assert updated is not None
    assert updated.model == "new-model"
    assert updated.allowed_tools == [ToolName.RUN_SCRIPT]
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
