from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import fcntl
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import httpx
from pydantic import BaseModel, Field, TypeAdapter, ValidationError, field_validator
import yaml

from agent_sandbox.agent_control import AgentControlStore
from agent_sandbox.bootstrap import (
    load_host_bootstrap_config,
    load_runtime_gateway_config,
    resolve_project_dir,
)
from agent_sandbox.config import (
    AdoWorkerGlobalConfig,
    AssistantSettingsConfig,
    DiscordWakeWorkerGlobalConfig,
    DiscordIngestWorkerGlobalConfig,
    GatewayConfig,
    ProxyIssueSyncWorkerGlobalConfig,
    ScheduledWakeWorkerGlobalConfig,
    TeamsIngestWorkerGlobalConfig,
    TeamsMeetingIngestWorkerGlobalConfig,
    TriggerOrchestratorWorkerGlobalConfig,
)
from agent_sandbox.cron import cron_min_interval_seconds, validate_cron_expression
from agent_sandbox.admin.directory import DirectoryAdminService
from agent_sandbox.directory_config import (
    DirectoryConfigResolver,
    DirectoryConfigStore,
    get_domain_base_config,
    validate_effective_domain_payload,
)
from agent_sandbox.directories import DirectoryService, DirectoryStore
from agent_sandbox.git_pr_notes import agent_pr_notes_ref_for_session
from agent_sandbox.host_config import DEFAULT_RUNTIME_DIR
from agent_sandbox.bootstrap import resolve_runtime_dir
from agent_sandbox.ownership import DirectoryOwnershipQueries
from agent_sandbox.policy_config import EffectivePolicyConfig
from agent_sandbox.daemons.pr_feedback_worker import PrFeedbackStore
from agent_sandbox.provider_config import ProviderConfigResolver
from agent_sandbox.repositories.session_manifests import as_session_manifest_repository
from agent_sandbox.session_artifacts import (
    AGENT_ARTIFACTS_ENV,
    DEFAULT_AGENT_ARTIFACTS_DIR,
    normalize_artifact_relative_path,
)
from agent_sandbox.repositories.skills_library import (
    SkillLibraryEntry,
    SkillLibraryMetadata,
    SkillLibraryRepository,
    normalize_skill_names,
)
from agent_sandbox.issues.service import IssueService
from agent_sandbox.vm.defaults_config import EffectiveVmDefaultsConfig, VmDefaultsConfigResolver
from agent_sandbox.models import (
    ApprovalQueueHealth,
    ApprovalRequest,
    ApprovalStatus,
    AgentRecord,
    DirectoryPath,
    LocalIssueRecord,
    SessionActionResult,
    SessionManifest,
    SessionSummary,
    ToolName,
    VmActionResult,
    VmSummary,
    WakeScheduledRequest,
    WakeScheduleDefinition,
    WorkerActionResult,
    WorkerSummary,
)
from agent_sandbox.runtime import RuntimeContext, build_runtime
from agent_sandbox.storage import JsonStore
from agent_sandbox.workflow.events import (
    SqliteWorkflowEventJournal,
    WorkflowEventJournal,
    workflow_event_journal_path,
)
from agent_sandbox.workflow.migrations import (
    AGENT_PROMPT_COMPLETED_PORT,
    AGENT_PROMPT_STARTED_PORT,
    migrate_agent_prompt_lifecycle_ports,
)
from agent_sandbox.workflow.runtime_config import (
    _compose_configured_workflow_ir,
    compile_worker_global_config_with_diagnostics,
)
from agent_sandbox.repositories.triggers import (
    JsonTriggerEventRepository,
    TriggerConfigDocument,
    TriggerRepository,
    TriggerEventRepository,
    WorkerConfigTriggerRepository,
)
from agent_sandbox.messaging.teams_auth import (
    DeviceCodeLoginSession,
    JsonTeamsIdentityCredentialStore,
    MicrosoftDeviceCodeAuthConfig,
    TeamsAuthCredentials,
    TeamsDeviceCodeTokenProvider,
)
from agent_sandbox.events.models import (
    EdgeDefinition,
    EffectDefinitionDiscriminated,
    GateDefinitionDiscriminated,
    QueryDefinitionDiscriminated,
    TriggerEvent,
    TriggerDefinitionDiscriminated,
    materialize_node_definition,
)
from agent_sandbox.triggers.queue import (
    SqliteTriggerEventQueue,
    TriggerEventQueue,
    TriggerQueueRecord,
)
from agent_sandbox.workflow.validation import (
    WorkflowValidationDiagnostic,
    derive_workflow_output_schemas,
    validate_workflow_binding_diagnostics,
)
from agent_sandbox.services import (
    normalize_teams_chat_targets,
    normalize_teams_target,
    normalize_teams_user_target,
)

_WORKER_DEFINITIONS: tuple[tuple[str, str, str], ...] = (
    ("ado", "ADO Worker", "agent-sandbox-ado-worker.service"),
    (
        "proxy-issue-sync",
        "Proxy Issue Sync Worker",
        "agent-sandbox-proxy-issue-sync.service",
    ),
    ("discord-ingest", "Discord Ingest Worker", "agent-sandbox-discord-ingest.service"),
    ("discord-wake", "Discord Wake Worker", "agent-sandbox-discord-wake.service"),
    ("scheduled-wake", "Scheduled Wake Worker", "agent-sandbox-scheduled-wake.service"),
    ("teams-ingest", "Teams Ingest Worker", "agent-sandbox-teams-ingest.service"),
    (
        "teams-meeting-ingest",
        "Teams Meeting Ingest Worker",
        "agent-sandbox-teams-meeting-ingest.service",
    ),
    (
        "effect-orchestrator",
        "Effect Orchestrator Worker",
        "agent-sandbox-effect-orchestrator.service",
    ),
    (
        "trigger-orchestrator",
        "Trigger Orchestrator Worker",
        "agent-sandbox-trigger-orchestrator.service",
    ),
)

_WORKER_GLOBAL_CONFIG_PATHS: dict[str, str] = {
    "ado": "config/ado-worker/config.json",
    "proxy-issue-sync": "config/proxy-issue-sync/config.json",
    "discord-ingest": "config/discord-ingest/config.json",
    "discord-wake": "config/discord-wake/config.json",
    "scheduled-wake": "config/scheduled-wake/config.json",
    "teams-ingest": "config/teams-ingest/config.json",
    "teams-meeting-ingest": "config/teams-meeting-ingest/config.json",
    "effect-orchestrator": "config/trigger-orchestrator/config.json",
    "trigger-orchestrator": "config/trigger-orchestrator/config.json",
}

_WORKER_GLOBAL_CONFIG_MODELS: dict[str, type[BaseModel]] = {
    "ado": AdoWorkerGlobalConfig,
    "proxy-issue-sync": ProxyIssueSyncWorkerGlobalConfig,
    "discord-ingest": DiscordIngestWorkerGlobalConfig,
    "discord-wake": DiscordWakeWorkerGlobalConfig,
    "scheduled-wake": ScheduledWakeWorkerGlobalConfig,
    "teams-ingest": TeamsIngestWorkerGlobalConfig,
    "teams-meeting-ingest": TeamsMeetingIngestWorkerGlobalConfig,
    "effect-orchestrator": TriggerOrchestratorWorkerGlobalConfig,
    "trigger-orchestrator": TriggerOrchestratorWorkerGlobalConfig,
}
ISSUE_STATES: tuple[str, ...] = (
    "New",
    "Approved",
    "Committed",
    "In Progress",
    "Completed",
    "Closed",
)
_ISSUE_STATE_SET = set(ISSUE_STATES)
_ISSUE_PRIORITY_ORDER: dict[str, int] = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
_APPROVAL_STATUS_ORDER: dict[ApprovalStatus, int] = {
    ApprovalStatus.PENDING: 0,
    ApprovalStatus.EXPIRED: 1,
    ApprovalStatus.DENIED: 2,
    ApprovalStatus.APPROVED: 3,
}
_SENSITIVE_PAYLOAD_MARKERS: tuple[str, ...] = (
    "token",
    "secret",
    "password",
    "pat",
    "authorization",
    "cookie",
    "private_key",
    "ssh_key",
)


class EffectiveIssueSetupConfig(BaseModel):
    git_root: str
    git_host: str
    git_user: str
    git_ssh_alias: str
    default_model: str | None = None
    post_exec_verification_prompt: str | None = None
    enable_guest_heartbeat: bool = False
    guest_heartbeat_prompt: str = "Resume work for this session. Check for pending feedback first, then continue the most recent task."
    issue_setup_azure_devops_pr_pat_env: str = "AZURE_DEVOPS_PR_PAT"


def resolve_effective_issue_setup_config(
    *,
    config,
    runtime: RuntimeContext,
    directory_path: str,
    root_dir: Path,
) -> EffectiveIssueSetupConfig:
    resolver = DirectoryConfigResolver(
        DirectoryService(runtime.directories),
        base_gateway_config=config,
        config_store=DirectoryConfigStore(runtime.directories, runtime_dir=config.runtime_dir),
    )
    resolution = resolver.resolve_domain(
        directory_path,
        domain="issue_setup",
        base_config=get_domain_base_config(config, "issue_setup"),
        validator=lambda candidate: validate_effective_domain_payload("issue_setup", candidate),
    )
    effective = resolution.effective_config
    provider_config = ProviderConfigResolver(
        config,
        directory_store=runtime.directories,
        session_store=as_session_manifest_repository(runtime.session_repository),
    ).resolve_for_directory(directory_path)
    provider_pat_env = provider_config.azure_devops_pr_pat_env.strip() or "AZURE_DEVOPS_PR_PAT"
    issue_setup_pat_env_raw = effective.get("issue_setup_azure_devops_pr_pat_env")
    issue_setup_pat_env = (
        str(issue_setup_pat_env_raw).strip() if isinstance(issue_setup_pat_env_raw, str) else ""
    ) or provider_pat_env
    if not issue_setup_pat_env:
        raise ValueError(
            "Missing env-var name for Azure DevOps PR PAT lookup. "
            "Set issue_setup_azure_devops_pr_pat_env or providers.azure_devops_pr_pat_env."
        )
    return EffectiveIssueSetupConfig.model_validate(
        {
            "git_root": str(effective.get("git_root") or _resolve_git_root(root_dir)),
            "git_host": str(effective.get("git_host") or "192.168.250.1"),
            "git_user": str(effective.get("git_user") or os.environ.get("USER", "agent")),
            "git_ssh_alias": str(effective.get("git_ssh_alias") or "agent-sandbox-git-host"),
            "default_model": str(effective.get("default_model") or "").strip() or None,
            "post_exec_verification_prompt": effective.get("post_exec_verification_prompt"),
            "enable_guest_heartbeat": bool(effective.get("enable_guest_heartbeat", False)),
            "guest_heartbeat_prompt": str(
                effective.get("guest_heartbeat_prompt")
                or "Resume work for this session. Check for pending feedback first, then continue the most recent task."
            ),
            "issue_setup_azure_devops_pr_pat_env": issue_setup_pat_env,
        }
    )


def resolve_agent_model_for_manifest(
    session: SessionManifest,
    *,
    issue_setup: EffectiveIssueSetupConfig,
) -> str:
    session_id = session.session_id
    directory_path = session.directory_path
    session_model = str(session.model or "").strip()
    if session_model:
        return session_model
    default_model = str(issue_setup.default_model or "").strip()
    if default_model:
        return default_model
    raise ValueError(
        f"Cannot resolve AGENT_MODEL for session '{session_id}' in directory '{directory_path}': "
        "missing session.model and missing issue_setup.default_model. "
        f"Remediation: set session.model for session '{session_id}' or configure "
        f"issue_setup.default_model for directory '{directory_path}' (or an inherited parent)."
    )


class WorkerGlobalConfigPayload(BaseModel):
    worker_id: str
    path: str
    exists: bool
    config: dict[str, object]


class WorkerGlobalConfigWritePayload(BaseModel):
    worker_id: str
    path: str
    config: dict[str, object]


class TriggerConfigMutationPayload(BaseModel):
    worker_id: str = "trigger-orchestrator"
    trigger_id: str
    operation: str
    path: str
    config: dict[str, object]


class EffectConfigMutationPayload(BaseModel):
    worker_id: str = "trigger-orchestrator"
    effect_id: str
    operation: str
    path: str
    config: dict[str, object]


class WorkflowEditorNodeLayout(BaseModel):
    x: float
    y: float


class WorkflowEditorLayoutDocument(BaseModel):
    nodes: dict[str, WorkflowEditorNodeLayout] = Field(default_factory=dict)


class WorkflowEditorGraphMetadata(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str | None = None
    directory_paths: list[str] = Field(default_factory=lambda: ["root"])
    user_ids: list[str] = Field(default_factory=list)
    node_ids: list[str] = Field(default_factory=list)

    @field_validator("directory_paths", "user_ids", "node_ids", mode="before")
    @classmethod
    def _normalize_string_lists(cls, value: object) -> object:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return value


class WorkflowEditorDocumentPayload(BaseModel):
    worker_id: str = "trigger-orchestrator"
    config_path: str
    layout_path: str
    config_exists: bool
    layout_exists: bool
    config: dict[str, object]
    layout: dict[str, object]
    graphs: list[WorkflowEditorGraphMetadata] = Field(default_factory=list)
    output_schemas: dict[str, dict[str, dict[str, Any]]] = Field(default_factory=dict)
    saved_at: datetime | None = None
    revision: str | None = None


class WorkflowEditorValidationPayload(BaseModel):
    worker_id: str = "trigger-orchestrator"
    config: dict[str, object]
    layout: dict[str, object]
    graphs: list[WorkflowEditorGraphMetadata] = Field(default_factory=list)
    output_schemas: dict[str, dict[str, dict[str, Any]]] = Field(default_factory=dict)
    validation_diagnostics: list[dict[str, object]] = Field(default_factory=list)


class WorkflowEditorSavePayload(BaseModel):
    worker_id: str = "trigger-orchestrator"
    config_path: str
    layout_path: str
    config: dict[str, object]
    layout: dict[str, object]
    graphs: list[WorkflowEditorGraphMetadata] = Field(default_factory=list)
    output_schemas: dict[str, dict[str, dict[str, Any]]] = Field(default_factory=dict)
    validation_diagnostics: list[dict[str, object]] = Field(default_factory=list)
    saved_at: datetime | None = None
    revision: str | None = None


class WorkflowRunNodeActivityPayload(BaseModel):
    node_id: str
    status: str
    count: int = 1
    source: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    dispatch_id: str | None = None
    error: str | None = None


class WorkflowRunSummaryPayload(BaseModel):
    event_id: str
    run_state: str = "active"
    trace_id: str | None = None
    source_node_id: str | None = None
    source_trigger_type: str | None = None
    delivery_state: str | None = None
    occurred_at: datetime | None = None
    latest_activity_at: datetime | None = None
    active_nodes: list[WorkflowRunNodeActivityPayload] = Field(default_factory=list)
    failed_node_id: str | None = None
    failed_node_status: str | None = None
    failed_at: datetime | None = None
    error: str | None = None
    resumable: bool = False


class WorkflowRunSnapshotPayload(BaseModel):
    worker_id: str = "trigger-orchestrator"
    generated_at: datetime
    runs: list[WorkflowRunSummaryPayload] = Field(default_factory=list)
    node_activity: dict[str, dict[str, int]] = Field(default_factory=dict)


class WorkflowGraphActivityNodePayload(BaseModel):
    node_id: str
    node_type: str | None = None
    display_name: str | None = None
    activity_count: int = 0
    status_counts: dict[str, int] = Field(default_factory=dict)
    first_activity_at: datetime | None = None
    latest_activity_at: datetime | None = None


class WorkflowGraphActivityGraphPayload(BaseModel):
    graph_id: str
    name: str
    description: str | None = None
    directory_paths: list[str] = Field(default_factory=list)
    user_ids: list[str] = Field(default_factory=list)
    node_ids: list[str] = Field(default_factory=list)
    node_count: int = 0
    run_count: int = 0
    activity_count: int = 0
    trigger_event_count: int = 0
    gate_execution_count: int = 0
    effect_execution_count: int = 0
    status_counts: dict[str, int] = Field(default_factory=dict)
    first_activity_at: datetime | None = None
    latest_activity_at: datetime | None = None
    nodes: list[WorkflowGraphActivityNodePayload] = Field(default_factory=list)


class WorkflowGraphActivityReportPayload(BaseModel):
    worker_id: str = "trigger-orchestrator"
    generated_at: datetime
    period_start: datetime
    period_end: datetime
    graphs: list[WorkflowGraphActivityGraphPayload] = Field(default_factory=list)


class ConformanceRequirementSnippetPayload(BaseModel):
    requirement_id: str
    status: str
    details: str | None = None
    remediation: str | None = None
    coverage: list[str] = Field(default_factory=list)


class ConformanceReportPayload(BaseModel):
    kind: str
    label: str
    artifact_path: str
    state: str
    status: str | None = None
    generated_at: datetime | None = None
    requirement_counts: dict[str, int] = Field(default_factory=dict)
    requirement_count: int = 0
    missing_coverage_count: int = 0
    missing_coverage: dict[str, list[str]] = Field(default_factory=dict)
    traceability_task_count: int = 0
    failing_requirements: list[ConformanceRequirementSnippetPayload] = Field(default_factory=list)
    remediation_snippets: list[ConformanceRequirementSnippetPayload] = Field(default_factory=list)
    error: str | None = None
    guidance: str | None = None


class ConformanceReportsPayload(BaseModel):
    generated_at: datetime
    stale_after_seconds: int
    reports: list[ConformanceReportPayload] = Field(default_factory=list)


class WorkflowRunClearPayload(BaseModel):
    worker_id: str = "trigger-orchestrator"
    event_id: str
    cleared_at: datetime
    queue_records_updated: int = 0
    effect_records_updated: int = 0
    gate_records_updated: int = 0
    await_agent_records_removed: int = 0


class DeploymentStatusPayload(BaseModel):
    active: bool = False
    message: str | None = None
    source: str | None = None
    started_at: datetime | None = None
    updated_at: datetime | None = None
    ttl_seconds: int = Field(default=21600, ge=60, le=86400)
    stale: bool = False


class WorkflowRunResumePayload(BaseModel):
    worker_id: str = "trigger-orchestrator"
    event_id: str
    resumed_at: datetime
    resume_event_id: str
    source_node_id: str
    target_node_id: str


class WorkflowEditorFieldOption(BaseModel):
    value: str
    label: str


class WorkflowEditorFieldDefinition(BaseModel):
    key: str
    label: str
    kind: str
    required: bool = False
    bindable: bool = False
    read_only: bool = False
    multiline: bool = False
    description: str | None = None
    placeholder: str | None = None
    options: list[WorkflowEditorFieldOption] = Field(default_factory=list)
    item_fields: list["WorkflowEditorFieldDefinition"] = Field(default_factory=list)


class WorkflowEditorNodePortDefinition(BaseModel):
    key: str
    label: str
    description: str | None = None


class WorkflowEditorNodeCatalogEntry(BaseModel):
    kind: str
    type: str
    label: str
    description: str | None = None
    fields: list[WorkflowEditorFieldDefinition] = Field(default_factory=list)
    input_ports: list[WorkflowEditorNodePortDefinition] = Field(default_factory=list)
    output_ports: list[WorkflowEditorNodePortDefinition] = Field(default_factory=list)


class WorkflowEditorCatalogPayload(BaseModel):
    worker_id: str = "trigger-orchestrator"
    nodes: list[WorkflowEditorNodeCatalogEntry]


_TRIGGER_DEFINITION_ADAPTER = TypeAdapter(TriggerDefinitionDiscriminated)
_GATE_DEFINITION_ADAPTER = TypeAdapter(GateDefinitionDiscriminated)
_EFFECT_DEFINITION_ADAPTER = TypeAdapter(EffectDefinitionDiscriminated)
_QUERY_DEFINITION_ADAPTER = TypeAdapter(QueryDefinitionDiscriminated)
_EDGE_DEFINITION_ADAPTER = TypeAdapter(EdgeDefinition)
_SYSTEM_CHAT_ASSISTANT_TRIGGER_ID = "system.chat_assistant"
_SYSTEM_CHAT_ASSISTANT_ENSURE_SESSION_ID = "system.chat_assistant.ensure_session"
_SYSTEM_CHAT_ASSISTANT_ENQUEUE_PROMPT_ID = "system.chat_assistant.enqueue_prompt"
_SYSTEM_MANAGED_WORKFLOW_NODE_IDS = frozenset(
    {
        _SYSTEM_CHAT_ASSISTANT_TRIGGER_ID,
        _SYSTEM_CHAT_ASSISTANT_ENSURE_SESSION_ID,
        _SYSTEM_CHAT_ASSISTANT_ENQUEUE_PROMPT_ID,
    }
)


def _module_project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _resolve_project_dir_for_admin_service(
    *,
    runtime_dir: Path,
    legacy_config_path: Path | None,
) -> Path:
    if legacy_config_path is not None:
        return resolve_project_dir(legacy_config_path=legacy_config_path)
    runtime_root = runtime_dir.expanduser()
    if not runtime_root.is_absolute():
        repo_relative_runtime = _module_project_root() / runtime_root
        if repo_relative_runtime.exists() or (repo_relative_runtime.parent / "config").exists():
            runtime_root = repo_relative_runtime
        else:
            runtime_root = runtime_root.resolve()
    else:
        runtime_root = runtime_root.resolve()
    if runtime_root.name == "runtime":
        return runtime_root.parent
    return runtime_root.parent if runtime_root.parent != runtime_root else runtime_root


def _guest_script_helper_b64(project_dir: Path, helper_name: str) -> str:
    helper_path = project_dir / "scripts" / "guest" / helper_name
    if not helper_path.exists():
        helper_path = _module_project_root() / "scripts" / "guest" / helper_name
    return base64.b64encode(helper_path.read_bytes()).decode("ascii")


def _guest_agent_pr_note_helper_b64(project_dir: Path) -> str:
    return _guest_script_helper_b64(project_dir, "agent_pr_note.sh")


def _guest_push_agent_helper_b64(project_dir: Path) -> str:
    return _guest_script_helper_b64(project_dir, "push_agent.sh")


def _guest_sync_base_helper_b64(project_dir: Path) -> str:
    return _guest_script_helper_b64(project_dir, "sync_base.sh")


def _strip_default_edge_fields(payload: dict[str, object]) -> dict[str, object]:
    edges = payload.get("edges")
    if not isinstance(edges, list):
        return payload
    normalized_edges: list[object] = []
    for entry in edges:
        if not isinstance(entry, dict):
            normalized_edges.append(entry)
            continue
        normalized = dict(entry)
        if normalized.get("source_port") is None:
            normalized.pop("source_port", None)
        if normalized.get("allow_cycle") is False:
            normalized.pop("allow_cycle", None)
        normalized_edges.append(normalized)
    payload["edges"] = normalized_edges
    return payload


def _workflow_binding_diagnostics_for_graphs(
    config: dict[str, object],
    graphs: list[WorkflowEditorGraphMetadata],
) -> list[WorkflowValidationDiagnostic]:
    if not graphs:
        return validate_workflow_binding_diagnostics(config)

    diagnostics: list[WorkflowValidationDiagnostic] = []
    seen: set[tuple[str, str | None, str | None, str | None, str | None, str]] = set()
    for graph in graphs:
        graph_node_ids = set(graph.node_ids)
        if not graph_node_ids:
            continue
        graph_config = _workflow_config_for_graph_node_ids(config, graph_node_ids)
        for diagnostic in validate_workflow_binding_diagnostics(graph_config):
            key = (
                diagnostic.code,
                diagnostic.node_id,
                diagnostic.node_type,
                diagnostic.field_path,
                diagnostic.reference,
                diagnostic.message,
            )
            if key in seen:
                continue
            seen.add(key)
            diagnostics.append(diagnostic)
    return diagnostics


def _workflow_config_for_graph_node_ids(
    config: dict[str, object], node_ids: set[str]
) -> dict[str, object]:
    graph_config = dict(config)
    for collection_name in ("triggers", "gates", "effects", "queries"):
        entries = config.get(collection_name)
        if not isinstance(entries, list):
            graph_config[collection_name] = []
            continue
        graph_config[collection_name] = [
            entry
            for entry in entries
            if isinstance(entry, dict) and str(entry.get("id") or "").strip() in node_ids
        ]
    edges = config.get("edges")
    if isinstance(edges, list):
        graph_config["edges"] = [
            edge
            for edge in edges
            if isinstance(edge, dict)
            and str(edge.get("source_id") or "").strip() in node_ids
            and str(edge.get("target_id") or "").strip() in node_ids
        ]
    else:
        graph_config["edges"] = []
    return graph_config


def _flatten_workflow_node_for_legacy_response(
    entry: dict[str, object], adapter: TypeAdapter
) -> dict[str, object]:
    try:
        materialized = materialize_node_definition(adapter.validate_python(entry))
        payload = materialized.model_dump(mode="json", exclude_none=True)
    except ValidationError:
        payload = dict(entry)
    config = payload.pop("config", None)
    if isinstance(config, dict):
        payload.update(config)
    return payload


def _workflow_config_for_legacy_mutation_response(config: dict[str, object]) -> dict[str, object]:
    response = json.loads(json.dumps(config))
    if not isinstance(response, dict):
        return config
    adapters = {
        "triggers": _TRIGGER_DEFINITION_ADAPTER,
        "gates": _GATE_DEFINITION_ADAPTER,
        "effects": _EFFECT_DEFINITION_ADAPTER,
        "queries": _QUERY_DEFINITION_ADAPTER,
    }
    for collection_name, adapter in adapters.items():
        entries = response.get(collection_name)
        if not isinstance(entries, list):
            continue
        response[collection_name] = [
            _flatten_workflow_node_for_legacy_response(entry, adapter)
            if isinstance(entry, dict)
            else entry
            for entry in entries
        ]
    return response


def _is_system_managed_workflow_node_id(node_id: str) -> bool:
    return str(node_id or "").strip() in _SYSTEM_MANAGED_WORKFLOW_NODE_IDS


def _system_chat_assistant_workflow_fragment() -> dict[str, list[dict[str, object]]]:
    return {
        "triggers": [
            {
                "id": _SYSTEM_CHAT_ASSISTANT_TRIGGER_ID,
                "display_name": "System Chat Assistant",
                "type": "trigger:system:chat-assistant",
                "config": {},
            }
        ],
        "effects": [
            {
                "id": _SYSTEM_CHAT_ASSISTANT_ENSURE_SESSION_ID,
                "display_name": "Resolve chat assistant session",
                "type": "effect:chat-assistant:ensure-session",
                "config": {
                    "user_id": "${{system.chat_assistant.user_id}}",
                    "thread_id": "${{system.chat_assistant.thread_id}}",
                    "user_message_id": "${{system.chat_assistant.user_message_id}}",
                    "content": "${{system.chat_assistant.content}}",
                    "backend": "${{system.chat_assistant.backend}}",
                    "assistant_capability_mode": (
                        "${{system.chat_assistant.assistant_capability_mode}}"
                    ),
                    "assistant_scope": "admin_chat",
                },
            },
            {
                "id": _SYSTEM_CHAT_ASSISTANT_ENQUEUE_PROMPT_ID,
                "display_name": "Enqueue chat assistant prompt",
                "type": "effect:agent:enqueue-prompt",
                "config": {
                    "target": "${{system.chat_assistant.ensure_session.session_id}}",
                    "session_id": "${{system.chat_assistant.ensure_session.session_id}}",
                    "vm_name": "${{system.chat_assistant.ensure_session.vm_name}}",
                    "prompt": "${{system.chat_assistant.ensure_session.prompt}}",
                    "model": "${{system.chat_assistant.ensure_session.model}}",
                    "vm_user": "agent",
                    "resume_thread_id": (
                        "${{system.chat_assistant.ensure_session.agent_thread_id}}"
                    ),
                    "queue_key": (
                        "chat:${{system.chat_assistant.ensure_session.thread_id}}:"
                        "${{system.chat_assistant.ensure_session.user_message_id}}"
                    ),
                    "execution_backend": (
                        "${{system.chat_assistant.ensure_session.execution_backend}}"
                    ),
                },
            },
        ],
        "edges": [
            {
                "source_id": _SYSTEM_CHAT_ASSISTANT_TRIGGER_ID,
                "target_id": _SYSTEM_CHAT_ASSISTANT_ENSURE_SESSION_ID,
            },
            {
                "source_id": _SYSTEM_CHAT_ASSISTANT_ENSURE_SESSION_ID,
                "target_id": _SYSTEM_CHAT_ASSISTANT_ENQUEUE_PROMPT_ID,
            },
        ],
    }


def _field_option(value: str, label: str | None = None) -> WorkflowEditorFieldOption:
    return WorkflowEditorFieldOption(value=value, label=label or value)


def _tool_field_options() -> list[WorkflowEditorFieldOption]:
    return [
        _field_option(tool.value, tool.value.replace("_", " "))
        for tool in sorted(ToolName, key=lambda item: item.value)
    ]


def _port(
    key: str, label: str, *, description: str | None = None
) -> WorkflowEditorNodePortDefinition:
    return WorkflowEditorNodePortDefinition(key=key, label=label, description=description)


def _field(
    key: str,
    label: str,
    kind: str,
    *,
    required: bool = False,
    bindable: bool = False,
    read_only: bool = False,
    multiline: bool = False,
    description: str | None = None,
    placeholder: str | None = None,
    options: list[WorkflowEditorFieldOption] | None = None,
    item_fields: list[WorkflowEditorFieldDefinition] | None = None,
) -> WorkflowEditorFieldDefinition:
    return WorkflowEditorFieldDefinition(
        key=key,
        label=label,
        kind=kind,
        required=required,
        bindable=bindable,
        read_only=read_only,
        multiline=multiline,
        description=description,
        placeholder=placeholder,
        options=options or [],
        item_fields=item_fields or [],
    )


_WORKFLOW_EDITOR_NODE_DESCRIPTIONS: dict[str, str] = {
    "trigger:cron": "Start a workflow on a recurring cron schedule.",
    "trigger:event": "Start when an internal workflow event is emitted.",
    "trigger:ticket:created": "Start when a tracked ticket is created.",
    "trigger:ticket:changed": "Start when a tracked ticket changes state or metadata.",
    "trigger:issue:comment-created": "Start when a tracked issue receives a new comment.",
    "trigger:issue:new-comment": "Start when a tracked issue receives a new comment.",
    "trigger:script-run-request": "Start from an approved run_script request submitted by a session.",
    "trigger:discord:new-message": (
        "Start from new Discord messages that match the configured filters."
    ),
    "trigger:teams:new-message": (
        "Start from new Microsoft Teams messages that match the configured targets."
    ),
    "trigger:email:new-message": (
        "Start from newly ingested email messages for the configured mailbox scope."
    ),
    "trigger:pull-request:new-feedback": (
        "Start when new pull-request feedback arrives from the configured provider."
    ),
    "trigger:pull-request:completed": (
        "Start when a tracked pull request reaches a terminal state."
    ),
    "trigger:review:new-request": "Start when a review request is queued for orchestration.",
    "trigger:git:push": "Start from Git push events captured by the host-side bare-repo hook.",
    "trigger:agent:run:completed": "Start when an agent run completion event is recorded.",
    "gate:filter": "Allow only matching events to continue downstream.",
    "gate:debounce": "Wait for a quiet period, then forward the latest activation.",
    "gate:map": "Project upstream outputs into a new payload shape.",
    "gate:deserialize": (
        "Parse a JSON or YAML string into object properties for downstream bindings."
    ),
    "gate:serialize": "Serialize a bindable value as JSON or YAML text for downstream nodes.",
    "gate:dedupe": "Suppress duplicate activations for the same key until a TTL expires.",
    "gate:merge": "Merge compatible inbound event streams into one downstream path.",
    "gate:await-agent": "Pause the workflow until a selected agent session completes.",
    "gate:for": "Iterate over an array and run the body branch for each item.",
    "effect:vm:create": "Create a dedicated libvirt VM and session record.",
    "effect:vm:clone-repo": "Clone a repository into a VM-backed agent workspace.",
    "effect:vm:bootstrap-python": "Bootstrap a Python checkout and environment in a VM.",
    "effect:vm:bootstrap-frontend": "Bootstrap frontend dependencies in a VM.",
    "effect:vm:start": "Start an existing VM-backed agent target.",
    "effect:vm:stop": "Stop an existing VM-backed agent target.",
    "effect:agent:create": "Create an agent session using the selected execution backend.",
    "effect:agent:destroy": "Destroy an agent session and its backend resources.",
    "effect:agent:clone-repo": "Clone a repository into an agent session workspace.",
    "effect:agent:bootstrap-frontend": "Bootstrap frontend dependencies for an agent session.",
    "effect:agent:copy-files": "Copy rendered file content into an agent session workspace.",
    "effect:agent:prompt": "Dispatch a prompt to an agent session and wait for completion.",
    "effect:agent:enqueue-prompt": "Queue a prompt for asynchronous agent-session execution.",
    "effect:agent:exec": "Run a shell command inside an agent session workspace.",
    "effect:semantic:ensure-collection": (
        "Ensure a host-side semantic collection exists with the configured vector schema."
    ),
    "effect:semantic:upsert": "Embed one text string and upsert it as one Qdrant point.",
    "effect:semantic:delete": "Delete semantic points by explicit point IDs.",
    "effect:bash:script": "Run a host-side Bash script from the effect orchestrator.",
    "effect:vm:exec": "Run a shell command inside a VM-backed agent target.",
    "effect:vm:copy-files": "Copy rendered file content into a VM-backed agent target.",
    "effect:vm:delete-files": "Delete files from a VM-backed agent target.",
    "effect:issue:update-state": "Update the workflow state for a tracked issue.",
    "effect:issue:update-metadata": "Patch metadata on a tracked issue.",
    "effect:ado:update-work-item": (
        "Update an Azure DevOps work item with an optional expected-state guard."
    ),
    "effect:event:emit": "Emit an internal workflow event for other graphs.",
    "effect:communication:send-message": (
        "Send a message through a configured communication target."
    ),
    "effect:notification:send": "Send a notification through a configured communication target.",
    "effect:session:update-metadata": "Patch metadata on a host-side session record.",
    "effect:git:promote-agent-branch": (
        "Promote an agent branch into a trusted host checkout."
    ),
    "effect:pr:create": "Create a pull request from a host-side checkout.",
    "query:vm:run-state": "Read the current run state for a VM-backed agent target.",
    "query:git:pr-note": "Read pull-request note context from a host checkout.",
    "query:session-context": "Read session context and metadata.",
    "query:agent:artifact-text": "Read a UTF-8 text file from an agent session artifacts folder.",
    "query:issue:get": "Read a local issue record by id.",
    "query:ado:work-items": "Query Azure DevOps work items.",
    "query:semantic:store-status": "Read Qdrant health and semantic collection status.",
    "query:semantic:search": "Search a semantic collection with an embedded text query.",
    "query:semantic:get": "Retrieve semantic points by explicit point IDs.",
}

_VM_ARTIFACT_TEXT_READ_COMMAND = f"""python3 - <<'PY'
from pathlib import Path, PurePosixPath
import os
import sys

root = Path(os.environ.get({AGENT_ARTIFACTS_ENV!r}) or {DEFAULT_AGENT_ARTIFACTS_DIR!r})
relative_text = os.environ.get("AGENT_SANDBOX_ARTIFACT_RELATIVE_PATH") or ""
relative = PurePosixPath(relative_text)
if not relative_text or relative.is_absolute() or any(part in {{"", ".", ".."}} for part in relative.parts):
    print("artifact path must be relative and must not contain dot or parent segments", file=sys.stderr)
    raise SystemExit(64)
try:
    root_resolved = root.resolve(strict=True)
    target = (root / relative.as_posix()).resolve(strict=True)
    target.relative_to(root_resolved)
except FileNotFoundError:
    print(f"artifact file not found: {{relative.as_posix()}}", file=sys.stderr)
    raise SystemExit(66)
except ValueError:
    print("artifact path must stay within the session artifacts folder", file=sys.stderr)
    raise SystemExit(64)
if not target.is_file():
    print(f"artifact path is not a regular file: {{relative.as_posix()}}", file=sys.stderr)
    raise SystemExit(66)
try:
    sys.stdout.write(target.read_text(encoding="utf-8"))
except UnicodeDecodeError:
    print("artifact file is not valid UTF-8 text", file=sys.stderr)
    raise SystemExit(65)
PY
"""


_WORKFLOW_EDITOR_NODE_CATALOG: tuple[WorkflowEditorNodeCatalogEntry, ...] = (
    WorkflowEditorNodeCatalogEntry(
        kind="trigger",
        type="trigger:cron",
        label="Cron",
        fields=[
            _field(
                "expression", "Cron expression", "string", required=True, placeholder="*/15 * * * *"
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="trigger",
        type="trigger:event",
        label="Internal event",
        fields=[
            _field("event_type", "Event type", "string", required=True),
            _field("correlation_key", "Correlation key", "string"),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="trigger",
        type="trigger:ticket:created",
        label="Ticket created",
        fields=[
            _field("directories", "Directories", "string_list", required=True, placeholder="root"),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="trigger",
        type="trigger:ticket:changed",
        label="Ticket changed",
        fields=[
            _field("directories", "Directories", "string_list", required=True, placeholder="root"),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="trigger",
        type="trigger:issue:comment-created",
        label="Issue comment created",
        fields=[
            _field(
                "provider",
                "Provider",
                "enum",
                options=[
                    _field_option("ado", "Azure DevOps"),
                ],
            ),
            _field("organization_url", "Organization URL", "string"),
            _field("project", "Project", "string"),
            _field("directories", "Directories", "string_list", placeholder="root"),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="trigger",
        type="trigger:issue:new-comment",
        label="Issue new comment",
        fields=[
            _field(
                "provider",
                "Provider",
                "enum",
                options=[
                    _field_option("ado", "Azure DevOps"),
                ],
            ),
            _field("organization_url", "Organization URL", "string"),
            _field("project", "Project", "string"),
            _field("directories", "Directories", "string_list", placeholder="root"),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="trigger",
        type="trigger:script-run-request",
        label="Script run request",
        fields=[],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="trigger",
        type="trigger:discord:new-message",
        label="Discord message",
        fields=[
            _field("discord_bot_token_env", "Bot token env var", "string", required=True),
            _field("channel_ids", "Channel IDs", "string_list"),
            _field("user_ids", "User IDs", "string_list"),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="trigger",
        type="trigger:teams:new-message",
        label="Teams message",
        fields=[
            _field(
                "targets",
                "Targets",
                "object_list",
                required=True,
                description="Teams targets with optional per-target message filters.",
                item_fields=[
                    _field(
                        "target",
                        "Target",
                        "string",
                        required=True,
                        placeholder="team-id/channel-id or chat:<chat-id>",
                    ),
                    _field(
                        "message_filter",
                        "Message filter",
                        "json",
                        description='Optional per-target wake filter, for example {"mentions":{"user_ids":["aad-user-id"]}} or {"content":{"contains":"literal text","case_sensitive":false}}.',
                    ),
                ],
            ),
            _field("user_ids", "User IDs", "string_list"),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="trigger",
        type="trigger:email:new-message",
        label="Email message",
        fields=[
            _field(
                "provider",
                "Provider",
                "enum",
                required=True,
                options=[
                    _field_option("json_store", "JSON store"),
                    _field_option("gmail", "Gmail"),
                    _field_option("graph", "Microsoft Graph"),
                ],
            ),
            _field("account_identifier", "Account identifier", "string", required=True),
            _field("mailbox_ids", "Mailbox IDs", "string_list", required=True),
            _field("auth_secret_refs", "Auth secret refs", "json"),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="trigger",
        type="trigger:pull-request:new-feedback",
        label="PR feedback",
        fields=[
            _field(
                "provider",
                "Provider",
                "enum",
                required=True,
                options=[
                    _field_option("local", "Local"),
                    _field_option("github", "GitHub"),
                    _field_option("ado", "Azure DevOps"),
                ],
            ),
            _field("organization_url", "Organization URL", "string"),
            _field("project", "Project", "string"),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="trigger",
        type="trigger:pull-request:completed",
        label="PR completed",
        fields=[
            _field(
                "provider",
                "Provider",
                "enum",
                required=True,
                options=[
                    _field_option("local", "Local"),
                    _field_option("github", "GitHub"),
                    _field_option("ado", "Azure DevOps"),
                ],
            ),
            _field("organization_url", "Organization URL", "string"),
            _field("project", "Project", "string"),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="trigger",
        type="trigger:review:new-request",
        label="Review request",
        fields=[],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="trigger",
        type="trigger:git:push",
        label="Git push",
        fields=[
            _field(
                "path_to_repo",
                "Repository path",
                "string",
                description="Exact bare-repo path. Mutually exclusive with Repository glob.",
            ),
            _field(
                "path_glob",
                "Repository glob",
                "string",
                description="Glob pattern for matching multiple bare repos, for example /path/to/git-remotes/*.git.",
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="trigger",
        type="trigger:agent:run:completed",
        label="Agent run completed",
        fields=[
            _field("vm_user", "VM user", "string"),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="gate",
        type="gate:filter",
        label="Filter",
        fields=[
            _field(
                "match",
                "Match",
                "json",
                bindable=True,
                description="Recursive subset match against the immediate incoming payload. Mutually exclusive with When.",
            ),
            _field(
                "when",
                "When",
                "json",
                bindable=True,
                description='Structured predicate using bindings, for example {"all":[{"left":"${{effect-1.success}}","equals":true}]} or {"left":"${{query-1.count}}","greater_than":8}. Mutually exclusive with Match.',
            ),
            _field(
                "outputs",
                "Outputs",
                "json",
                read_only=True,
                description="Derived from the single upstream node and validated automatically.",
            ),
        ],
    ),

    WorkflowEditorNodeCatalogEntry(
        kind="gate",
        type="gate:debounce",
        label="Debounce",
        fields=[
            _field(
                "key",
                "Key",
                "string",
                required=True,
                bindable=True,
                description="Groups activations to debounce. Supports workflow bindings.",
                placeholder="issue-comment:${{trigger.issue_id}}",
            ),
            _field(
                "quiet_period",
                "Quiet period",
                "string",
                required=True,
                description="ISO-8601 duration to wait after the latest activation, for example PT2M.",
                placeholder="PT2M",
            ),
            _field(
                "max_delay",
                "Max delay",
                "string",
                required=True,
                description="ISO-8601 duration cap from the first activation in a burst, for example PT10M.",
                placeholder="PT10M",
            ),
            _field(
                "outputs",
                "Outputs",
                "json",
                read_only=True,
                description="Derived from the single upstream node and emitted from the latest activation in a burst.",
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="gate",
        type="gate:map",
        label="Map",
        fields=[
            _field("template", "Template", "json", required=True, bindable=True),
            _field(
                "outputs",
                "Outputs",
                "json",
                read_only=True,
                description="Derived from the top-level keys in the map template.",
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="gate",
        type="gate:deserialize",
        label="Deserialize",
        fields=[
            _field(
                "content",
                "Content",
                "string",
                required=True,
                bindable=True,
                multiline=True,
                description=(
                    "JSON or YAML string to parse. Bind to an upstream string when content "
                    "is produced by another node."
                ),
                placeholder="${{node.payload_text}}",
            ),
            _field(
                "format",
                "Format",
                "enum",
                options=[
                    _field_option("auto", "Auto"),
                    _field_option("json", "JSON"),
                    _field_option("yaml", "YAML"),
                ],
                description="Parser to use. Auto tries JSON first, then YAML.",
            ),
            _field(
                "outputs",
                "Outputs",
                "json",
                description=(
                    "Optional output schema for binding validation when Content is dynamic. "
                    "Literal object content is inferred automatically."
                ),
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="gate",
        type="gate:serialize",
        label="Serialize",
        fields=[
            _field(
                "value",
                "Value",
                "json",
                required=True,
                bindable=True,
                description="Value to serialize. Use an exact binding to serialize an upstream object unchanged.",
                placeholder='{"title":"${{gate-map-1.title}}"}',
            ),
            _field(
                "format",
                "Format",
                "enum",
                options=[
                    _field_option("json", "JSON"),
                    _field_option("yaml", "YAML"),
                ],
                description="Serializer to use. JSON emits compact text; YAML emits block-style text.",
            ),
            _field(
                "outputs",
                "Outputs",
                "json",
                read_only=True,
                description="Emits content and format for downstream bindings.",
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="gate",
        type="gate:dedupe",
        label="Dedupe",
        fields=[
            _field(
                "key",
                "Key",
                "string",
                required=True,
                bindable=True,
                description=(
                    "Stable idempotency key. Duplicate activations with the same key "
                    "are dropped until TTL expires."
                ),
                placeholder="issue-comment:${{trigger.comment_id}}",
            ),
            _field(
                "ttl",
                "TTL",
                "string",
                required=True,
                bindable=True,
                description="ISO-8601 dedupe window, for example P7D or PT1H.",
                placeholder="P7D",
            ),
            _field(
                "outputs",
                "Outputs",
                "json",
                read_only=True,
                description="Derived from the single upstream node.",
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="gate",
        type="gate:merge",
        label="Merge",
        fields=[
            _field(
                "outputs",
                "Outputs",
                "json",
                read_only=True,
                description="Derived from the compatible intersection of upstream outputs.",
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="gate",
        type="gate:await-agent",
        label="Await agent",
        fields=[
            _field(
                "session_id",
                "Session ID",
                "string",
                bindable=True,
                description="Select the session to watch. Provide either Session ID or VM name.",
                placeholder="session-id",
            ),
            _field(
                "vm_name",
                "VM name",
                "string",
                bindable=True,
                description="Optional alternative selector when Session ID is unavailable.",
                placeholder="vm-name",
            ),
            _field(
                "outputs",
                "Outputs",
                "json",
                read_only=True,
                description="Derived from agent completion events.",
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="gate",
        type="gate:for",
        label="For",
        fields=[
            _field(
                "items",
                "Items",
                "json",
                required=True,
                bindable=True,
                description="Array expression to iterate. The body branch receives current_element/current_index outputs.",
            ),
            _field(
                "outputs",
                "Outputs",
                "json",
                read_only=True,
                description="Derived from for-loop runtime outputs.",
            ),
        ],
        output_ports=[
            _port("body", "Body", description="Loop body path while items remain."),
            _port("done", "Done", description="Exit path after the final item."),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:vm:create",
        label="VM create",
        fields=[
            _field("vm_name", "VM name", "string", bindable=True),
            _field("session_id", "Session ID", "string", bindable=True),
            _field("labels", "Labels", "json", bindable=True),
            _field("model", "Model", "string", bindable=True),
            _field("vm_user", "VM user", "string", bindable=True),
            _field("ttl_hours", "TTL hours", "number", bindable=True),
            _field(
                "allowed_tools",
                "Allowed tools",
                "string_list",
                description="Additional MCP tools to allow for the created session.",
            ),
            _field(
                "allowed_script_names",
                "Allowed script names",
                "string_list",
                description="Additional script names the created session may invoke with run_script.",
            ),
            _field("discord_channels", "Discord channels", "string_list"),
            _field("email_mailboxes", "Email mailboxes", "string_list"),
            _field("email_folders", "Email folders", "string_list"),
            _field("dotnet", "Dotnet", "boolean", bindable=True),
            _field("nuget", "NuGet", "boolean", bindable=True),
            _field("mssql", "MSSQL", "boolean", bindable=True),
            _field("mssql_init_root", "MSSQL init root", "string", bindable=True),
            _field("playwright", "Playwright", "boolean", bindable=True),
            _field("codex_skills", "Codex skills", "string_list"),
            _field("directory_path", "Directory path", "string", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:vm:clone-repo",
        label="VM clone repo",
        fields=[
            _field("target", "Target", "string", bindable=True),
            _field("session_id", "Session ID", "string", bindable=True),
            _field("vm_name", "VM name", "string", bindable=True),
            _field("repo_source", "Repo source", "string", required=True, bindable=True),
            _field("clone_branch", "Clone branch", "string", bindable=True),
            _field("vm_user", "VM user", "string", bindable=True),
            _field("directory_path", "Directory path", "string", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:vm:bootstrap-python",
        label="VM bootstrap Python",
        fields=[
            _field("target", "Target", "string", bindable=True),
            _field("session_id", "Session ID", "string", bindable=True),
            _field("vm_name", "VM name", "string", bindable=True),
            _field("host_checkout", "Host checkout", "string", required=True, bindable=True),
            _field("target_dir", "Target dir", "string", required=True, bindable=True),
            _field("vm_user", "VM user", "string", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:vm:bootstrap-frontend",
        label="VM bootstrap frontend",
        fields=[
            _field("target", "Target", "string", bindable=True),
            _field("session_id", "Session ID", "string", bindable=True),
            _field("vm_name", "VM name", "string", bindable=True),
            _field("host_checkout", "Host checkout", "string", required=True, bindable=True),
            _field("install_roots", "Install roots", "string_list"),
            _field("vm_user", "VM user", "string", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:vm:start",
        label="VM start",
        fields=[
            _field("target", "Target", "string", bindable=True),
            _field("session_id", "Session ID", "string", bindable=True),
            _field("vm_name", "VM name", "string", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:vm:stop",
        label="VM stop",
        fields=[
            _field("target", "Target", "string", bindable=True),
            _field("session_id", "Session ID", "string", bindable=True),
            _field("vm_name", "VM name", "string", bindable=True),
            _field("timeout_seconds", "Timeout seconds", "number", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:agent:create",
        label="Agent create",
        fields=[
            _field(
                "provider",
                "Provider",
                "string",
                bindable=True,
                description="Optional agent provider override. Leave blank to use the directory default.",
            ),
            _field(
                "execution_backend",
                "Execution backend",
                "enum",
                description="Runtime backend that will host the created agent session.",
                options=[
                    _field_option("libvirt_vm", "Libvirt VM"),
                    _field_option("container_worker_vm", "Container worker VM"),
                ],
            ),
            _field(
                "vm_name",
                "VM name",
                "string",
                bindable=True,
                description="Optional VM name for libvirt-backed agents. Container-backed agents usually leave this blank.",
            ),
            _field(
                "session_id",
                "Session ID",
                "string",
                bindable=True,
                description="Optional durable session id. Leave blank to let the runtime allocate one.",
            ),
            _field(
                "labels",
                "Labels",
                "string_map",
                bindable=True,
                description="String key/value labels stored on the created agent session.",
            ),
            _field(
                "model",
                "Model",
                "string",
                bindable=True,
                description="Model name the agent should use. Leave blank to inherit the directory default.",
            ),
            _field(
                "vm_user",
                "VM user",
                "string",
                bindable=True,
                description="Guest user for libvirt-backed sessions. Defaults to agent.",
            ),
            _field(
                "ttl_hours",
                "TTL hours",
                "number",
                bindable=True,
                description="Session lifetime in hours before the host considers it expired.",
            ),
            _field(
                "allowed_tools",
                "Allowed tools",
                "string_list",
                description="MCP tools the created session may invoke through the host gateway.",
            ),
            _field(
                "allowed_script_names",
                "Allowed script names",
                "string_list",
                description="Additional script names the created session may invoke with run_script.",
            ),
            _field(
                "discord_channels",
                "Discord channels",
                "string_list",
                description="Discord channel ids the created session may read or post to.",
            ),
            _field(
                "teams_channel_allowlist",
                "Teams targets",
                "string_list",
                description="Teams channels or chats the created session may use.",
                placeholder="team-id/channel-id or chat:<chat-id>",
            ),
            _field(
                "teams_chat_allowlist",
                "Teams chats",
                "string_list",
                placeholder="chat:<chat-id>",
            ),
            _field(
                "teams_user_allowlist",
                "Teams users",
                "string_list",
                description="Teams users the created session may interact with.",
                placeholder="user:<user-id-or-upn>",
            ),
            _field(
                "email_mailboxes",
                "Email mailboxes",
                "string_list",
                description="Email mailboxes the created session may inspect.",
            ),
            _field(
                "email_folders",
                "Email folders",
                "string_list",
                description="Email folders the created session may inspect.",
            ),
            _field(
                "communication_file_receive_enabled",
                "Receive inbound files",
                "boolean",
                bindable=True,
                description="Allow the session to download inbound communication attachments into host-managed artifacts.",
            ),
            _field(
                "communication_file_send_enabled",
                "Send files",
                "boolean",
                bindable=True,
                description="Allow the session to send staged communication artifacts through approved targets.",
            ),
            _field(
                "communication_file_require_approval_for_send",
                "Require approval for file sends",
                "boolean",
                bindable=True,
                description="Require an approval before the session can send staged communication files.",
            ),
            _field(
                "communication_file_max_bytes",
                "Communication file max bytes",
                "number",
                bindable=True,
                description="Maximum size in bytes for a single communication file. Leave blank to inherit policy defaults.",
            ),
            _field(
                "dotnet",
                "Dotnet",
                "boolean",
                bindable=True,
                description="Bootstrap the .NET toolchain for libvirt-backed agents.",
            ),
            _field(
                "nuget",
                "NuGet",
                "boolean",
                bindable=True,
                description="Bootstrap NuGet relay support for libvirt-backed agents.",
            ),
            _field(
                "mssql",
                "MSSQL",
                "boolean",
                bindable=True,
                description="Bootstrap SQL Server tooling for libvirt-backed agents.",
            ),
            _field(
                "mssql_init_root",
                "MSSQL init root",
                "string",
                bindable=True,
                description="Optional host init-script root for MSSQL provisioning.",
            ),
            _field(
                "playwright",
                "Playwright",
                "boolean",
                bindable=True,
                description="Bootstrap Playwright for browser automation in libvirt-backed agents.",
            ),
            _field(
                "codex_skills",
                "Codex skills",
                "string_list",
                description="Managed Codex skills to install into the created session.",
            ),
            _field(
                "directory_path",
                "Directory path",
                "string",
                bindable=True,
                description="Directory scope that owns the created session and resolves inherited configuration.",
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:agent:destroy",
        label="Agent destroy",
        fields=[
            _field("target", "Target", "string", bindable=True),
            _field("session_id", "Session ID", "string", bindable=True),
            _field("vm_name", "VM name", "string", bindable=True),
            _field(
                "execution_backend",
                "Execution backend",
                "enum",
                options=[
                    _field_option("libvirt_vm", "Libvirt VM"),
                    _field_option("container_worker_vm", "Container worker VM"),
                ],
            ),
            _field("directory_path", "Directory path", "string", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:agent:clone-repo",
        label="Agent clone repo",
        fields=[
            _field("target", "Target", "string", bindable=True),
            _field("session_id", "Session ID", "string", bindable=True),
            _field("vm_name", "VM name", "string", bindable=True),
            _field("repo_source", "Repo source", "string", required=True, bindable=True),
            _field("clone_branch", "Clone branch", "string", bindable=True),
            _field("vm_user", "VM user", "string", bindable=True),
            _field("directory_path", "Directory path", "string", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:agent:bootstrap-frontend",
        label="Agent bootstrap frontend",
        fields=[
            _field("target", "Target", "string", bindable=True),
            _field("session_id", "Session ID", "string", bindable=True),
            _field("vm_name", "VM name", "string", bindable=True),
            _field("host_checkout", "Host checkout", "string", required=True, bindable=True),
            _field("install_roots", "Install roots", "string_list"),
            _field("vm_user", "VM user", "string", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:agent:copy-files",
        label="Agent copy files",
        fields=[
            _field(
                "target",
                "Target",
                "string",
                bindable=True,
                description="Agent session selector. Usually bind this to an upstream session_id.",
            ),
            _field(
                "session_id",
                "Session ID",
                "string",
                bindable=True,
                description="Explicit session id to copy into when Target is not used.",
            ),
            _field(
                "vm_name",
                "VM name",
                "string",
                bindable=True,
                description="Legacy VM selector for libvirt-backed agent sessions.",
            ),
            _field(
                "vm_user",
                "VM user",
                "string",
                bindable=True,
                description="Guest user for libvirt-backed copies. Defaults to agent.",
            ),
            _field(
                "files",
                "Files",
                "object_list",
                required=True,
                description=(
                    "Files to write into the agent session. Each item needs either "
                    "Source path or Content."
                ),
                item_fields=[
                    _field(
                        "source_path",
                        "Source path",
                        "string",
                        bindable=True,
                        description="Host-side file path to copy. Mutually exclusive with Content.",
                    ),
                    _field(
                        "source_format",
                        "Source format",
                        "enum",
                        description=(
                            "How to read Source path. File copies the source; Template renders "
                            "workflow bindings before writing."
                        ),
                        options=[
                            _field_option("file", "File"),
                            _field_option("template", "Template"),
                        ],
                    ),
                    _field(
                        "content",
                        "Content",
                        "string",
                        bindable=True,
                        multiline=True,
                        description="Inline file content. Mutually exclusive with Source path.",
                    ),
                    _field(
                        "content_format",
                        "Content format",
                        "enum",
                        description="How to serialize inline Content before writing it.",
                        options=[
                            _field_option("text", "Text"),
                            _field_option("json", "JSON"),
                            _field_option("yaml", "YAML"),
                        ],
                    ),
                    _field(
                        "destination_path",
                        "Destination path",
                        "string",
                        required=True,
                        bindable=True,
                        description=(
                            "Absolute destination path inside the agent session. "
                            "Container-backed sessions require /home/agent/..."
                        ),
                    ),
                    _field(
                        "mode",
                        "Mode",
                        "string",
                        bindable=True,
                        description=(
                            "Optional Unix file permissions to apply after copying, "
                            'for example "0644" or "0755".'
                        ),
                    ),
                    _field(
                        "overwrite",
                        "Overwrite",
                        "boolean",
                        bindable=True,
                        description=(
                            "When enabled, replace an existing destination file. "
                            "When disabled, fail if the destination already exists."
                        ),
                    ),
                ],
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:agent:prompt",
        label="Agent prompt",
        fields=[
            _field("target", "Target", "string", bindable=True),
            _field("session_id", "Session ID", "string", bindable=True),
            _field("vm_name", "VM name", "string", bindable=True),
            _field("prompt", "Prompt", "string", required=True, bindable=True, multiline=True),
            _field("model", "Model", "string", bindable=True),
            _field("vm_user", "VM user", "string", bindable=True),
            _field("resume_thread_id", "Resume thread ID", "string", bindable=True),
        ],
        output_ports=[
            _port(
                AGENT_PROMPT_STARTED_PORT,
                "Started",
                description="Emits after the prompt has been dispatched.",
            ),
            _port(
                AGENT_PROMPT_COMPLETED_PORT,
                "Completed",
                description="Emits after the agent run has completed.",
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:agent:enqueue-prompt",
        label="Agent enqueue prompt",
        fields=[
            _field("target", "Target", "string", bindable=True),
            _field("session_id", "Session ID", "string", bindable=True),
            _field("vm_name", "VM name", "string", bindable=True),
            _field("prompt", "Prompt", "string", required=True, bindable=True, multiline=True),
            _field("model", "Model", "string", bindable=True),
            _field("vm_user", "VM user", "string", bindable=True),
            _field("resume_thread_id", "Resume thread ID", "string", bindable=True),
            _field("queue_key", "Queue key", "string", bindable=True),
        ],
        output_ports=[
            _port(
                AGENT_PROMPT_STARTED_PORT,
                "Started",
                description="Emits after the queued prompt has been dispatched.",
            ),
            _port(
                AGENT_PROMPT_COMPLETED_PORT,
                "Completed",
                description="Emits after the dispatched agent run has completed.",
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:agent:exec",
        label="Agent exec",
        fields=[
            _field("target", "Target", "string", bindable=True),
            _field("session_id", "Session ID", "string", bindable=True),
            _field("vm_name", "VM name", "string", bindable=True),
            _field("vm_user", "VM user", "string", bindable=True),
            _field("command", "Command", "string", required=True, bindable=True),
            _field("working_directory", "Working directory", "string", bindable=True),
            _field("environment", "Environment", "json", bindable=True),
            _field("stdin", "Stdin", "string", bindable=True, multiline=True),
            _field("timeout_seconds", "Timeout seconds", "number", bindable=True),
            _field("check", "Check exit code", "boolean", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:semantic:ensure-collection",
        label="Semantic ensure collection",
        fields=[
            _field(
                "collection_name",
                "Collection name",
                "string",
                required=True,
                bindable=True,
                description="Qdrant collection name to create if missing.",
            ),
            _field(
                "directory_path",
                "Directory path",
                "string",
                bindable=True,
                description="Directory-scoped provider config to use for semantic settings.",
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:semantic:upsert",
        label="Semantic upsert",
        fields=[
            _field(
                "collection_name",
                "Collection name",
                "string",
                required=True,
                bindable=True,
                description="Qdrant collection name to create if missing and upsert into.",
            ),
            _field(
                "point_id",
                "Point ID",
                "string",
                required=True,
                bindable=True,
                description=(
                    "Caller-supplied Qdrant point ID used for idempotent updates. "
                    "Use an unsigned 64-bit integer or UUID string."
                ),
            ),
            _field("text", "Text", "string", required=True, bindable=True, multiline=True),
            _field(
                "payload",
                "Payload",
                "json",
                bindable=True,
                description="Optional Qdrant payload. The _semantic_text key is reserved.",
            ),
            _field("directory_path", "Directory path", "string", bindable=True),
            _field("wait", "Wait", "boolean", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:semantic:delete",
        label="Semantic delete",
        fields=[
            _field("collection_name", "Collection name", "string", required=True, bindable=True),
            _field(
                "point_ids",
                "Point IDs",
                "string_list",
                required=True,
                bindable=True,
                description=(
                    "Explicit Qdrant point IDs to delete. IDs must be unsigned "
                    "64-bit integers or UUID strings. Filter deletes are not supported."
                ),
            ),
            _field("directory_path", "Directory path", "string", bindable=True),
            _field("wait", "Wait", "boolean", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:bash:script",
        label="Bash script",
        fields=[
            _field("script_path", "Script path", "string", required=True, bindable=True),
            _field("args", "Args", "string_list", bindable=True),
            _field("working_directory", "Working directory", "string", bindable=True),
            _field("environment", "Environment", "json", bindable=True),
            _field("stdin", "Stdin", "string", bindable=True, multiline=True),
            _field("timeout_seconds", "Timeout seconds", "number", bindable=True),
            _field("check", "Check exit code", "boolean", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:vm:exec",
        label="VM exec",
        fields=[
            _field("target", "Target", "string", bindable=True),
            _field("session_id", "Session ID", "string", bindable=True),
            _field("vm_name", "VM name", "string", bindable=True),
            _field("vm_user", "VM user", "string", bindable=True),
            _field("command", "Command", "string", required=True, bindable=True),
            _field("working_directory", "Working directory", "string", bindable=True),
            _field("environment", "Environment", "json", bindable=True),
            _field("stdin", "Stdin", "string", bindable=True, multiline=True),
            _field("timeout_seconds", "Timeout seconds", "number", bindable=True),
            _field("check", "Check exit code", "boolean", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:vm:copy-files",
        label="VM copy files",
        fields=[
            _field("target", "Target", "string", bindable=True),
            _field("session_id", "Session ID", "string", bindable=True),
            _field("vm_name", "VM name", "string", bindable=True),
            _field("vm_user", "VM user", "string", bindable=True),
            _field(
                "files",
                "Files",
                "object_list",
                required=True,
                item_fields=[
                    _field("source_path", "Source path", "string", bindable=True),
                    _field(
                        "source_format",
                        "Source format",
                        "enum",
                        options=[
                            _field_option("file", "File"),
                            _field_option("template", "Template"),
                        ],
                    ),
                    _field("content", "Content", "string", bindable=True),
                    _field(
                        "content_format",
                        "Content format",
                        "enum",
                        options=[
                            _field_option("text", "Text"),
                            _field_option("json", "JSON"),
                            _field_option("yaml", "YAML"),
                        ],
                    ),
                    _field(
                        "destination_path",
                        "Destination path",
                        "string",
                        required=True,
                        bindable=True,
                    ),
                    _field("mode", "Mode", "string", bindable=True),
                    _field("overwrite", "Overwrite", "boolean", bindable=True),
                ],
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:vm:delete-files",
        label="VM delete files",
        fields=[
            _field("target", "Target", "string", bindable=True),
            _field("session_id", "Session ID", "string", bindable=True),
            _field("vm_name", "VM name", "string", bindable=True),
            _field("vm_user", "VM user", "string", bindable=True),
            _field(
                "paths",
                "Paths",
                "string_list",
                required=True,
                description="Absolute guest paths to delete.",
            ),
            _field("recursive", "Recursive", "boolean", bindable=True),
            _field("missing_ok", "Ignore missing", "boolean", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:issue:update-state",
        label="Issue update state",
        fields=[
            _field("issue_id", "Issue ID", "string", required=True, bindable=True),
            _field("state", "State", "string", required=True, bindable=True),
            _field("sync_proxy_cache", "Sync proxy cache", "boolean", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:issue:update-metadata",
        label="Issue update metadata",
        fields=[
            _field("issue_id", "Issue ID", "string", required=True, bindable=True),
            _field("patch", "Patch", "json", bindable=True),
            _field("merge_patch", "Merge patch", "json", bindable=True),
            _field("expected_updated_at", "Expected updated at", "string", bindable=True),
            _field("sync_proxy_cache", "Sync proxy cache", "boolean", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:ado:update-work-item",
        label="ADO update work item",
        fields=[
            _field("directory_path", "Directory path", "string", required=True, bindable=True),
            _field("project", "Project", "string", required=True, bindable=True),
            _field("work_item_id", "Work item ID", "string", required=True, bindable=True),
            _field("state", "State", "string", bindable=True),
            _field("fields", "Fields", "json", bindable=True),
            _field("expected_states", "Expected states", "string_list", bindable=True),
            _field("sync_proxy_cache", "Sync proxy cache", "boolean", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:event:emit",
        label="Emit internal event",
        fields=[
            _field("event_type", "Event type", "string", required=True, bindable=True),
            _field("correlation_key", "Correlation key", "string", bindable=True),
            _field("payload", "Payload", "json", required=True, bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:communication:send-message",
        label="Send message",
        fields=[
            _field("communication_target_id", "Communication target ID", "string", required=True),
            _field("message", "Message", "string", required=True, bindable=True, multiline=True),
            _field("thread_id", "Thread ID", "string", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:notification:send",
        label="Send notification",
        fields=[
            _field("communication_target_id", "Communication target ID", "string", required=True),
            _field("message", "Message", "string", required=True, bindable=True, multiline=True),
            _field(
                "severity",
                "Severity",
                "enum",
                options=[
                    _field_option("info", "Info"),
                    _field_option("success", "Success"),
                    _field_option("warning", "Warning"),
                    _field_option("error", "Error"),
                ],
            ),
            _field("subject", "Subject", "string", bindable=True),
            _field("thread_id", "Thread ID", "string", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:session:update-metadata",
        label="Session update metadata",
        fields=[
            _field("target", "Target", "string", bindable=True),
            _field("session_id", "Session ID", "string", bindable=True),
            _field("vm_name", "VM name", "string", bindable=True),
            _field("metadata", "Metadata", "json", required=True, bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:git:promote-agent-branch",
        label="Promote agent branch",
        fields=[
            _field("host_repo_path", "Host repo path", "string", required=True, bindable=True),
            _field("bare_repo_path", "Bare repo path", "string", bindable=True),
            _field("bare_repo_name", "Bare repo name", "string", bindable=True),
            _field("agent_branch", "Agent branch", "string", required=True, bindable=True),
            _field("review_branch", "Review branch", "string", bindable=True),
            _field("remote_name", "Remote name", "string", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:pr:create",
        label="PR create",
        fields=[
            _field("host_repo_path", "Host repo path", "string", required=True, bindable=True),
            _field("local_branch", "Local branch", "string", bindable=True),
            _field("origin_branch", "Origin branch", "string", bindable=True),
            _field("canonical_origin_url", "Canonical origin URL", "string", bindable=True),
            _field("provider", "Provider", "string", bindable=True),
            _field("project", "Project", "string", bindable=True),
            _field("base_branch", "Base branch", "string", bindable=True),
            _field("session_id", "Session ID", "string", bindable=True),
            _field("work_item_id", "Work item ID", "string", bindable=True),
            _field("title", "Title", "string", bindable=True),
            _field("body", "Body", "string", bindable=True, multiline=True),
            _field("body_file", "Body file", "string", bindable=True),
            _field("body_artifact_ids", "Body artifact IDs", "json", bindable=True),
            _field("draft", "Draft", "boolean", bindable=True),
            _field("fill", "Fill", "boolean", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="query",
        type="query:vm:run-state",
        label="VM run state",
        fields=[
            _field("session_id", "Session ID", "string", bindable=True),
            _field("vm_name", "VM name", "string", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="query",
        type="query:git:pr-note",
        label="Git PR note",
        fields=[
            _field("host_repo_path", "Host repo path", "string", required=True, bindable=True),
            _field("local_branch", "Local branch", "string", required=True, bindable=True),
            _field("remote_name", "Remote name", "string", bindable=True),
            _field("notes_ref", "Notes ref", "string", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="query",
        type="query:session-context",
        label="Session context",
        fields=[
            _field("target", "Target", "string", bindable=True),
            _field("session_id", "Session ID", "string", bindable=True),
            _field("vm_name", "VM name", "string", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="query",
        type="query:agent:artifact-text",
        label="Agent artifact text",
        fields=[
            _field("session_id", "Session ID", "string", required=True, bindable=True),
            _field(
                "path",
                "Artifact path",
                "string",
                required=True,
                bindable=True,
                description="Relative path inside the resolved session artifacts folder.",
                placeholder="report/summary.txt",
            ),
            _field("vm_user", "VM user", "string", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="query",
        type="query:issue:get",
        label="Issue get",
        fields=[
            _field("issue_id", "Issue ID", "string", required=True, bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="query",
        type="query:ado:work-items",
        label="ADO work items",
        fields=[
            _field("directory_path", "Directory path", "string", bindable=True),
            _field("project", "Project", "string", bindable=True),
            _field(
                "wiql",
                "WIQL",
                "string",
                bindable=True,
                multiline=True,
                description="Explicit WIQL query. When set, filter fields are not applied.",
            ),
            _field(
                "current_iteration",
                "Current iteration",
                "boolean",
                bindable=True,
                description="Resolve the team's current iteration before querying.",
            ),
            _field("team", "Team", "string", bindable=True),
            _field("iteration", "Iteration path", "string", bindable=True),
            _field("area_path", "Area path", "string", bindable=True),
            _field("states", "States", "string_list"),
            _field("types", "Types", "string_list"),
            _field("tags_any", "Tags any", "string_list"),
            _field("text_search", "Text search", "string", bindable=True),
            _field("top", "Top", "number", bindable=True),
            _field("skip", "Skip", "number", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="query",
        type="query:semantic:store-status",
        label="Semantic store status",
        fields=[
            _field("collection_name", "Collection name", "string", bindable=True),
            _field("directory_path", "Directory path", "string", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="query",
        type="query:semantic:search",
        label="Semantic search",
        fields=[
            _field("collection_name", "Collection name", "string", required=True, bindable=True),
            _field("query_text", "Query text", "string", required=True, bindable=True),
            _field("filter", "Filter", "json", bindable=True),
            _field("limit", "Limit", "number", bindable=True),
            _field("score_threshold", "Score threshold", "number", bindable=True),
            _field("directory_path", "Directory path", "string", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="query",
        type="query:semantic:get",
        label="Semantic get",
        fields=[
            _field("collection_name", "Collection name", "string", required=True, bindable=True),
            _field(
                "point_ids",
                "Point IDs",
                "string_list",
                required=True,
                bindable=True,
                description="Explicit Qdrant point IDs as unsigned 64-bit integers or UUID strings.",
            ),
            _field("directory_path", "Directory path", "string", bindable=True),
        ],
    ),
)


def _safe_validation_error(exc: ValidationError, *, context: str) -> ValueError:
    details: list[str] = []
    for item in exc.errors(include_input=False):
        location = ".".join(str(part) for part in item.get("loc", ()))
        message = str(item.get("msg", "invalid value"))
        details.append(f"{location}: {message}" if location else message)
    suffix = "; ".join(details) if details else "invalid value"
    return ValueError(f"{context}: {suffix}")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _coerce_optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _coerce_optional_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _read_json_object(path: Path | None) -> dict[str, object]:
    if path is None or not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


CONFORMANCE_REPORT_STALE_AFTER = timedelta(days=7)
CONFORMANCE_REPORT_SNIPPET_LIMIT = 10
CONFORMANCE_REPORT_TEXT_LIMIT = 500
TRIGGER_CONFORMANCE_GUIDANCE = (
    "PYTHONPATH=src python -m pytest -q tests/test_trigger_conformance_suite.py"
)
EFFECT_CONFORMANCE_GUIDANCE = (
    "PYTHONPATH=src python -m pytest -q tests/test_effect_conformance_suite.py"
)


def _trim_conformance_text(
    value: object, *, limit: int = CONFORMANCE_REPORT_TEXT_LIMIT
) -> str | None:
    text = _coerce_optional_string(value)
    if text is None:
        return None
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3].rstrip()}..."


def _resolve_path_setting(*, base_dir: Path, value: object | None, fallback: Path) -> Path:
    if value is None:
        candidate = fallback
    elif isinstance(value, Path):
        candidate = value
    else:
        text = str(value).strip()
        candidate = Path(text) if text else fallback
    candidate = candidate.expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    return (base_dir / candidate).resolve()


def _ticket_created_trigger_ids(config: dict[str, object]) -> set[str]:
    return {trigger["id"] for trigger in _ticket_created_trigger_definitions(config)}


def _ticket_created_trigger_definitions(
    config: dict[str, object],
) -> list[dict[str, object]]:
    triggers = config.get("triggers")
    if not isinstance(triggers, list):
        return []
    results: list[dict[str, object]] = []
    for entry in triggers:
        if not isinstance(entry, dict):
            continue
        try:
            materialized = materialize_node_definition(
                _TRIGGER_DEFINITION_ADAPTER.validate_python(entry)
            )
        except ValidationError:
            continue
        if str(getattr(materialized, "type", "")).strip() != "trigger:ticket:created":
            continue
        trigger_id = str(getattr(materialized, "id", "")).strip()
        if not trigger_id:
            continue
        raw_directories = getattr(materialized, "directories", [])
        directories = [
            str(directory).strip().strip("/")
            for directory in raw_directories
            if str(directory).strip().strip("/")
        ]
        results.append({"id": trigger_id, "directories": directories})
    return results


def _max_ticket_created_at_for_directories(
    issues: object, directories: list[object]
) -> datetime | None:
    max_created_at: datetime | None = None
    for issue in issues:
        if not isinstance(issue, LocalIssueRecord):
            continue
        if not _issue_within_ticket_created_directories(issue.directory_path, directories):
            continue
        created_at = _as_utc(issue.created_at)
        if max_created_at is None or created_at > max_created_at:
            max_created_at = created_at
    return max_created_at


def _issue_within_ticket_created_directories(candidate: str, directories: list[object]) -> bool:
    if not directories:
        return True
    normalized_candidate = str(candidate).strip().strip("/")
    for directory in directories:
        normalized_directory = str(directory).strip().strip("/")
        if not normalized_directory:
            continue
        if (
            normalized_candidate == normalized_directory
            or normalized_candidate.startswith(f"{normalized_directory}/")
        ):
            return True
    return False


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _merge_run_node_activity(items: object) -> list[WorkflowRunNodeActivityPayload]:
    if not isinstance(items, list):
        return []
    merged: dict[tuple[str, str], WorkflowRunNodeActivityPayload] = {}
    for item in items:
        try:
            activity = (
                item
                if isinstance(item, WorkflowRunNodeActivityPayload)
                else WorkflowRunNodeActivityPayload.model_validate(item)
            )
        except ValidationError:
            continue
        key = (activity.node_id, activity.status)
        existing = merged.get(key)
        if existing is None:
            merged[key] = activity.model_copy(deep=True)
            continue
        existing.count += activity.count
        if existing.started_at is None or (
            activity.started_at is not None and activity.started_at < existing.started_at
        ):
            existing.started_at = activity.started_at
        if existing.finished_at is None or (
            activity.finished_at is not None and activity.finished_at > existing.finished_at
        ):
            existing.finished_at = activity.finished_at
        if existing.source is None and activity.source is not None:
            existing.source = activity.source
        if existing.dispatch_id is None and activity.dispatch_id is not None:
            existing.dispatch_id = activity.dispatch_id
    order = {"running": 0, "waiting": 1, "inflight": 2, "pending": 3}
    return sorted(
        merged.values(),
        key=lambda item: (
            order.get(item.status, 99),
            -(item.count or 0),
            item.node_id,
        ),
    )


def _normalize_resume_mode(value: str | None) -> str:
    normalized = str(value or "").strip() or "same_context"
    if normalized not in {"same_context", "new_context"}:
        raise ValueError("resume_mode must be one of: same_context, new_context")
    return normalized


def _require_valid_cron(cron: str) -> str:
    try:
        return validate_cron_expression(cron)
    except ValueError as exc:
        raise ValueError(f"Invalid cron expression. {exc}") from exc


def _cron_min_interval_seconds(expression: str) -> float | None:
    return cron_min_interval_seconds(expression, _utc_now().replace(second=0, microsecond=0))


def _normalize_local_issue_state(state: str | None, *, allow_empty: bool = False) -> str | None:
    normalized = str(state or "").strip()
    if not normalized:
        return None if allow_empty else "New"
    if normalized not in _ISSUE_STATE_SET:
        allowed = ", ".join(ISSUE_STATES)
        raise ValueError(f"Invalid issue state: {normalized!r}. Allowed values: {allowed}.")
    return normalized


def _normalize_issue_priority(priority: str | None) -> str | None:
    normalized = str(priority or "").strip().upper()
    if not normalized:
        return None
    if normalized not in _ISSUE_PRIORITY_ORDER:
        allowed = ", ".join(sorted(_ISSUE_PRIORITY_ORDER))
        raise ValueError(f"Invalid issue priority: {normalized!r}. Allowed values: {allowed}.")
    return normalized


def _local_issue_fields(issue: LocalIssueRecord) -> dict[str, object]:
    fields = issue.metadata.get("fields")
    if isinstance(fields, dict):
        return dict(fields)
    return {}


def _issue_priority_sort_key(issue: LocalIssueRecord) -> tuple[int, datetime, datetime, str]:
    fields = _local_issue_fields(issue)
    priority_value = fields.get("priority")
    if priority_value is None:
        priority_value = fields.get("Priority")
    priority = _normalize_issue_priority(str(priority_value or "").strip()) or "P3"
    return (
        _ISSUE_PRIORITY_ORDER[priority],
        issue.updated_at,
        issue.created_at,
        issue.issue_number,
    )


def _issue_number_sort_key(issue: LocalIssueRecord) -> tuple[int, int | str, str]:
    normalized = issue.issue_number.strip()
    if normalized.isdigit():
        return (0, int(normalized), issue.issue_id)
    return (1, normalized, issue.issue_id)


def _approval_sort_key(request: ApprovalRequest) -> tuple[int, datetime, str]:
    return (_APPROVAL_STATUS_ORDER[request.status], request.created_at, request.request_id)


def _redact_sensitive_payload(value: Any, *, key_name: str | None = None) -> Any:
    normalized_key = (key_name or "").strip().lower()
    if normalized_key and any(marker in normalized_key for marker in _SENSITIVE_PAYLOAD_MARKERS):
        return "<redacted>"
    if isinstance(value, dict):
        return {
            str(key): _redact_sensitive_payload(item, key_name=str(key))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_sensitive_payload(item) for item in value]
    return value


def _extract_issue_number(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip()
    if normalized.isdigit():
        return normalized
    if normalized.startswith("vm-") and normalized[3:].isdigit():
        return normalized[3:]
    match = re.match(r"^issue-([0-9]+)(?:-|$)", normalized)
    if match:
        return match.group(1)
    return None


def _read_issue_number_from_yaml(issue_path: Path) -> str:
    text = issue_path.read_text(encoding="utf-8")
    patterns = [
        r"(?m)^\s*issue_number\s*:\s*[\"']?([0-9]+)[\"']?\s*$",
        r"(?m)^\s*number\s*:\s*[\"']?([0-9]+)[\"']?\s*$",
        r"(?m)^\s*id\s*:\s*[\"']?([0-9]+)[\"']?\s*$",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    raise ValueError(
        f"Could not read issue number from YAML file: {issue_path}. "
        "Expected one of: issue_number:, number:, or id:."
    )


def _extract_issue_number_from_payload(payload: dict[str, Any]) -> str | None:
    for key in ("issue_number", "number", "id"):
        value = payload.get(key)
        if value is None:
            continue
        normalized = str(value).strip()
        if normalized.isdigit():
            return normalized
    source = payload.get("source")
    if isinstance(source, dict):
        for key in ("work_item_id", "issue_id", "work_item_url"):
            value = str(source.get(key) or "").strip()
            if value.isdigit():
                return value
            parsed = _extract_issue_number(value)
            if parsed is not None:
                return parsed
    for key in ("url",):
        parsed = _extract_issue_number(str(payload.get(key) or "").strip())
        if parsed is not None:
            return parsed
    return None


def _resolve_issue_number(issue_input: str) -> str:
    candidate = Path(issue_input)
    if candidate.is_file():
        return _read_issue_number_from_yaml(candidate)
    return issue_input


def _resolve_vm_name(target: str) -> str:
    normalized = target.strip()
    if normalized.isdigit():
        return f"vm-{normalized}"
    return normalized


def _resolve_vm_name_from_target(target: str, sessions: dict[str, SessionManifest]) -> str:
    normalized = target.strip()
    if not normalized:
        return ""
    if normalized in sessions:
        return sessions[normalized].vm_id or normalized
    return _resolve_vm_name(normalized)


def _issue_session_id_prefix(issue_number: str) -> str:
    return f"issue-{issue_number}-"


def _scoped_issue_id(
    issue_number: str,
    *,
    directory_path: str,
    provider: str,
    organization: str | None = None,
    project: str | None = None,
) -> str:
    normalized_directory_path = DirectoryPath(segments=directory_path).as_posix()
    parts = [normalized_directory_path, provider]
    if organization and project:
        parts.extend([organization.rstrip("/"), project])
    parts.append(issue_number)
    return "::".join(parts)


def _parse_session_extension(value: str | None) -> timedelta:
    raw_value = (value or "1d").strip().lower()
    match = re.fullmatch(r"(\d+)([hd])", raw_value)
    if match is None:
        raise ValueError("Extension must use the format <number><h|d>, for example 8h or 2d.")
    amount = int(match.group(1))
    unit = match.group(2)
    return timedelta(hours=amount) if unit == "h" else timedelta(days=amount)


def _default_session_ttl(policy: EffectivePolicyConfig) -> timedelta:
    return timedelta(seconds=policy.default_session_ttl_seconds)


def _resolve_session_ttl(policy: EffectivePolicyConfig, ttl_hours: int | None) -> timedelta:
    if ttl_hours is not None:
        return timedelta(hours=ttl_hours)
    return _default_session_ttl(policy)


def _load_optional_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        values[key] = value.strip().strip('"').strip("'")
    return values


def _resolve_git_root(root_dir: Path) -> Path:
    env_value = os.environ.get("AGENT_SANDBOX_GIT_ROOT", "").strip()
    if env_value:
        return Path(env_value)
    return root_dir / "git-remotes"


def _matching_issue_session_ids(
    issue_number: str, sessions: dict[str, SessionManifest]
) -> set[str]:
    vm_name = f"vm-{issue_number}"
    prefix = _issue_session_id_prefix(issue_number)
    matches: set[str] = set()
    for session_id, session in sessions.items():
        if session.vm_id == vm_name or session_id == issue_number or session_id.startswith(prefix):
            matches.add(session_id)
    return matches


def _session_sort_key(item: tuple[str, SessionManifest]) -> tuple[datetime, datetime, str]:
    session_id, manifest = item
    return (manifest.created_at, manifest.expires_at, session_id)


def _resolve_session_target(
    target: str, sessions: dict[str, SessionManifest]
) -> tuple[str, SessionManifest]:
    normalized = target.strip()
    if not normalized:
        raise ValueError("Target must be a non-empty issue number, vm name, or session id.")

    direct_match = sessions.get(normalized)
    if direct_match is not None:
        return normalized, direct_match

    matching_ids: set[str] = set()
    issue_number = _extract_issue_number(normalized)
    if issue_number is not None:
        matching_ids.update(_matching_issue_session_ids(issue_number, sessions))
    matching_ids.update(_matching_vm_session_ids(normalized, sessions))

    if not matching_ids:
        raise ValueError(f"Unknown session target: {target}")

    resolved_session_id = max(
        matching_ids,
        key=lambda session_id: _session_sort_key((session_id, sessions[session_id])),
    )
    return resolved_session_id, sessions[resolved_session_id]


def _default_base_branch_for_repo(repo_path: str) -> str | None:
    return _default_base_branch_for_repo_remote(repo_path, "origin")


def _default_base_branch_for_repo_remote(repo_path: str, remote_name: str) -> str | None:
    candidates = [
        [
            "git",
            "-C",
            repo_path,
            "symbolic-ref",
            "--quiet",
            "--short",
            f"refs/remotes/{remote_name}/HEAD",
        ],
        ["git", "-C", repo_path, "branch", "--show-current"],
    ]
    for cmd in candidates:
        result = _run_subprocess(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            continue
        value = (result.stdout or "").strip()
        if not value:
            continue
        remote_prefix = f"{remote_name}/"
        if value.startswith(remote_prefix):
            value = value[len(remote_prefix) :]
        return value or None
    return None


def _resolve_canonical_origin_url_for_repo(repo_path: str, remote_name: str = "origin") -> str:
    remote_result = _run_subprocess(
        ["git", "-C", repo_path, "remote", "get-url", remote_name],
        capture_output=True,
        text=True,
    )
    remote_url = (remote_result.stdout or "").strip() if remote_result.returncode == 0 else ""
    if not remote_url:
        return ""

    try:
        resolved_remote = str(Path(remote_url).resolve(strict=False))
    except OSError:
        resolved_remote = remote_url

    if "/runtime/repo-cache/" in resolved_remote and resolved_remote.endswith(".git"):
        mirror_origin_result = _run_subprocess(
            ["git", f"--git-dir={resolved_remote}", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
        )
        mirror_origin_url = (
            (mirror_origin_result.stdout or "").strip()
            if mirror_origin_result.returncode == 0
            else ""
        )
        if mirror_origin_url:
            return mirror_origin_url

    return remote_url


def _is_remote_git_source(source: str) -> bool:
    text = str(source or "").strip()
    if not text:
        return False
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", text):
        return True
    return bool(re.match(r"^[^/@:\s]+@[^:\s]+:.+", text))


def _mirror_remote_name_for_repo(repo_path: str) -> str:
    current_branch_result = _run_subprocess(
        ["git", "-C", repo_path, "branch", "--show-current"],
        capture_output=True,
        text=True,
    )
    current_branch = (
        (current_branch_result.stdout or "").strip()
        if current_branch_result.returncode == 0
        else ""
    )
    if current_branch:
        tracking_remote_result = _run_subprocess(
            ["git", "-C", repo_path, "config", "--get", f"branch.{current_branch}.remote"],
            capture_output=True,
            text=True,
        )
        tracking_remote = (
            (tracking_remote_result.stdout or "").strip()
            if tracking_remote_result.returncode == 0
            else ""
        )
        if tracking_remote:
            return tracking_remote

    remote_head_result = _run_subprocess(
        ["git", "-C", repo_path, "for-each-ref", "--format=%(refname)", "refs/remotes/*/HEAD"],
        capture_output=True,
        text=True,
    )
    if remote_head_result.returncode == 0:
        remote_heads = []
        for line in (remote_head_result.stdout or "").splitlines():
            remote_head_ref = line.strip()
            if not remote_head_ref:
                continue
            ref_parts = remote_head_ref.split("/")
            if (
                len(ref_parts) >= 4
                and ref_parts[:2] == ["refs", "remotes"]
                and ref_parts[-1] == "HEAD"
            ):
                remote_heads.append(ref_parts[2])
        if remote_heads:
            return remote_heads[0]

    remotes_result = _run_subprocess(
        ["git", "-C", repo_path, "remote"],
        capture_output=True,
        text=True,
    )
    if remotes_result.returncode == 0:
        remotes = [
            line.strip() for line in (remotes_result.stdout or "").splitlines() if line.strip()
        ]
        if remotes:
            return remotes[0]

    return "origin"


def _prune_pr_map(path: Path, *, session_ids: set[str]) -> tuple[int, set[tuple[str, str, str]]]:
    if not path.exists():
        return 0, set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0, set()
    entries = payload.get("entries")
    if not isinstance(entries, dict):
        return 0, set()

    removed_streams: set[tuple[str, str, str]] = set()
    kept_entries: dict[str, object] = {}
    removed_count = 0
    for entry_key, raw_entry in entries.items():
        if not isinstance(raw_entry, dict):
            kept_entries[entry_key] = raw_entry
            continue
        session_id = str(raw_entry.get("session_id", "")).strip()
        if session_id in session_ids:
            provider = str(raw_entry.get("provider", "")).strip()
            repo = str(raw_entry.get("repo", "")).strip()
            pr_id = str(raw_entry.get("pr_id", "")).strip()
            if provider and repo and pr_id:
                removed_streams.add((provider, repo, pr_id))
            removed_count += 1
            continue
        kept_entries[entry_key] = raw_entry

    if removed_count:
        payload["entries"] = kept_entries
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return removed_count, removed_streams


def _prune_pr_feedback_store(path: Path, *, streams: set[tuple[str, str, str]]) -> tuple[int, int]:
    if not streams:
        return 0, 0
    if not path.exists():
        return 0, 0
    return PrFeedbackStore(path).prune_streams(streams)


def _prune_approvals_for_sessions(runtime: RuntimeContext, *, session_ids: set[str]) -> int:
    if not session_ids:
        return 0
    approvals = runtime.approvals.read_all()
    removed_count = 0
    remaining = {}
    for request_id, approval in approvals.items():
        if approval.session_id in session_ids:
            removed_count += 1
            continue
        remaining[request_id] = approval
    if removed_count:
        runtime.approvals.write_all(remaining)
    return removed_count


def _remove_issue_bare_repo(
    issue_number: str, *, root_dir: Path, runtime_dir: Path
) -> tuple[bool, str | None]:
    git_root = _resolve_git_root(root_dir)
    bare_repo_path = git_root / f"{issue_number}_repo.git"
    hook_log_paths = [
        root_dir / "runtime" / "hook-logs" / f"{bare_repo_path.name}.post-receive.log",
        runtime_dir / "hook-logs" / f"{bare_repo_path.name}.post-receive.log",
    ]
    trusted_repo_path: str | None = None
    if bare_repo_path.exists():
        try:
            config_result = subprocess.run(
                [
                    "git",
                    f"--git-dir={bare_repo_path}",
                    "config",
                    "--get",
                    "agent-sandbox.trustedRepoPath",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError:
            config_result = None
        if config_result is not None and config_result.returncode == 0:
            trusted_repo_path = (config_result.stdout or "").strip() or None
        shutil.rmtree(bare_repo_path, ignore_errors=True)
    for hook_log_path in hook_log_paths:
        if hook_log_path.exists():
            hook_log_path.unlink()
    if trusted_repo_path:
        mirror_remote_name = f"{issue_number}-mirror"
        try:
            subprocess.run(
                ["git", "-C", trusted_repo_path, "remote", "remove", mirror_remote_name],
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError:
            pass
    return bare_repo_path.exists() is False, str(
        bare_repo_path
    ) if bare_repo_path.exists() else str(bare_repo_path)


def _caller_home() -> Path:
    caller_user = os.environ.get("SUDO_USER") or os.environ.get("USER") or ""
    caller_home = os.environ.get("HOME", "")
    if caller_user:
        try:
            passwd_entry = subprocess.run(
                ["getent", "passwd", caller_user],
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError:
            passwd_entry = None
        if passwd_entry and passwd_entry.returncode == 0:
            fields = (passwd_entry.stdout or "").strip().split(":")
            if len(fields) >= 6 and fields[5]:
                caller_home = fields[5]
    return Path(caller_home or Path.home())


def _resolve_private_key_path() -> Path | None:
    configured = os.environ.get("SSH_PRIVATE_KEY_PATH", "").strip()
    if configured:
        path = Path(configured)
        return path if path.is_file() else None

    home = _caller_home()
    for candidate in (home / ".ssh" / "id_ed25519", home / ".ssh" / "id_rsa"):
        if candidate.is_file():
            return candidate
    return None


def _known_hosts_path() -> Path:
    configured = os.environ.get("AGENT_SANDBOX_GUEST_KNOWN_HOSTS_FILE", "").strip()
    if configured:
        path = Path(configured)
    else:
        path = _caller_home() / ".ssh" / "agent-sandbox-known_hosts"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return path
    except OSError:
        fallback = (
            Path("/tmp")
            / f"agent-sandbox-known_hosts-{os.environ.get('SUDO_USER') or os.environ.get('USER') or 'agent'}"
        )
        fallback.parent.mkdir(parents=True, exist_ok=True)
        fallback.touch(exist_ok=True)
        try:
            fallback.chmod(0o600)
        except OSError:
            pass
        return fallback


def _run_subprocess(
    cmd: list[str],
    *,
    check: bool = False,
    capture_output: bool = True,
    text: bool = True,
    timeout: float | None = None,
    input: str | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        check=check,
        capture_output=capture_output,
        text=text,
        timeout=timeout,
        input=input,
        env=env,
    )


def _combined_process_message(stdout: str | None, stderr: str | None) -> str:
    stdout_text = (stdout or "").strip()
    stderr_text = (stderr or "").strip()
    if stdout_text and stderr_text:
        if stdout_text == stderr_text:
            return stdout_text
        return f"{stdout_text}\n{stderr_text}"
    return stdout_text or stderr_text


def _python_has_pip(python_bin: str) -> bool:
    version_check = _run_subprocess([python_bin, "--version"], capture_output=True, text=True)
    if version_check.returncode != 0:
        return False
    pip_check = _run_subprocess(
        [python_bin, "-m", "pip", "--version"], capture_output=True, text=True
    )
    return pip_check.returncode == 0


def _ensure_python_has_pip(python_bin: str) -> bool:
    if _python_has_pip(python_bin):
        return True
    ensurepip_result = _run_subprocess(
        [python_bin, "-m", "ensurepip", "--upgrade", "--default-pip"],
        capture_output=True,
        text=True,
    )
    if ensurepip_result.returncode != 0:
        return False
    return _python_has_pip(python_bin)


def _resolve_host_python_with_pip() -> str:
    configured = os.environ.get("HOST_PYTHON_BIN", "").strip()
    if configured:
        if _ensure_python_has_pip(configured):
            return configured
        raise RuntimeError(f"Host Python must provide pip: {configured}")

    candidates: list[str] = []
    if sys.executable:
        candidates.append(sys.executable)
    candidates.extend(["python3", "python"])

    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if _ensure_python_has_pip(candidate):
            return candidate

    preferred = sys.executable or "python3"
    raise RuntimeError(f"Host Python must provide pip: {preferred}")


def _run_virsh(args: list[str]) -> subprocess.CompletedProcess[str]:
    # These VMs are managed under the system libvirt daemon. Service processes may
    # not inherit LIBVIRT_DEFAULT_URI/XDG_RUNTIME_DIR, so prefer an explicit
    # connection target before falling back to ambient virsh behavior.
    commands = [
        ["virsh", "-c", "qemu:///system", *args],
        ["sudo", "-n", "virsh", "-c", "qemu:///system", *args],
        ["virsh", *args],
        ["sudo", "-n", "virsh", *args],
    ]
    last_result: subprocess.CompletedProcess[str] | None = None
    for cmd in commands:
        try:
            result = _run_subprocess(cmd)
        except OSError:
            continue
        last_result = result
        if result.returncode == 0:
            return result
    if last_result is None:
        raise RuntimeError(f"Unable to execute virsh command: {' '.join(args)}")
    return last_result


def _run_system_service_command(args: list[str]) -> subprocess.CompletedProcess[str]:
    commands = [args, ["sudo", "-n", *args]]
    last_result: subprocess.CompletedProcess[str] | None = None
    for cmd in commands:
        try:
            result = _run_subprocess(cmd)
        except OSError:
            continue
        last_result = result
        if result.returncode == 0:
            return result
    if last_result is None:
        raise RuntimeError(f"Unable to execute command: {' '.join(args)}")
    return last_result


def _systemctl_show(unit: str) -> dict[str, str]:
    properties = [
        "LoadState",
        "ActiveState",
        "SubState",
        "Result",
        "MainPID",
        "ExecMainStatus",
        "ExecMainCode",
        "UnitFileState",
        "Description",
    ]
    result = _run_system_service_command(
        ["systemctl", "show", "--no-pager", f"--property={','.join(properties)}", unit]
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        detail = stderr or stdout or f"systemctl show failed for {unit}"
        raise RuntimeError(detail)

    payload: dict[str, str] = {}
    for line in (result.stdout or "").splitlines():
        key, _, value = line.partition("=")
        if key:
            payload[key] = value
    return payload


def _journalctl_tail(unit: str, *, lines: int) -> str:
    result = _run_system_service_command(
        ["journalctl", "--no-pager", "--output=short-iso", "-u", unit, "-n", str(lines)]
    )
    output = (result.stdout or "").strip()
    error = (result.stderr or "").strip()
    if result.returncode != 0 and error:
        return f"Worker log unavailable: {error}"
    return output or error or "No journal entries found."


def _worker_definition(worker_id: str) -> tuple[str, str, str] | None:
    normalized = worker_id.strip()
    return next((entry for entry in _WORKER_DEFINITIONS if entry[0] == normalized), None)


def _run_systemd_control(action: str, unit: str) -> subprocess.CompletedProcess[str]:
    commands = [
        ["systemctl", action, unit],
        ["sudo", "-n", "systemctl", action, unit],
        ["pkexec", "systemctl", action, unit],
    ]
    last_result: subprocess.CompletedProcess[str] | None = None
    for cmd in commands:
        try:
            result = _run_subprocess(cmd)
        except OSError:
            continue
        last_result = result
        if result.returncode == 0:
            return result
    if last_result is None:
        raise RuntimeError("Neither systemctl, sudo, nor pkexec was available.")
    return last_result


def _virsh_lines(args: list[str]) -> list[str]:
    result = _run_virsh(args)
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise RuntimeError(stderr or f"virsh {' '.join(args)} failed")
    return [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]


def _dominfo_field(vm_name: str, field_name: str) -> str | None:
    result = _run_virsh(["dominfo", vm_name])
    if result.returncode != 0:
        return None
    for line in (result.stdout or "").splitlines():
        key, _, value = line.partition(":")
        if key.strip() == field_name:
            stripped = value.strip()
            return stripped or None
    return None


def _resolve_vm_ip(vm_name: str) -> str | None:
    result = _run_virsh(["domifaddr", vm_name])
    if result.returncode != 0:
        return None
    for line in (result.stdout or "").splitlines():
        parts = line.split()
        if "ipv4" in parts and len(parts) >= 4:
            address = parts[3]
            return address.split("/", 1)[0]
    return None


def _resolve_vm_ip_with_pkexec(vm_name: str) -> str | None:
    result = _run_subprocess(
        ["pkexec", "virsh", "domifaddr", vm_name], capture_output=True, text=True
    )
    if result.returncode != 0:
        return None
    for line in (result.stdout or "").splitlines():
        parts = line.split()
        if "ipv4" in parts and len(parts) >= 4:
            address = parts[3]
            return address.split("/", 1)[0]
    return None


def _wait_for_vm_ip(
    vm_name: str, *, timeout_seconds: int = 180, allow_pkexec: bool = False
) -> str | None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        ip = _resolve_vm_ip(vm_name)
        if ip:
            return ip
        if allow_pkexec:
            ip = _resolve_vm_ip_with_pkexec(vm_name)
            if ip:
                return ip
        time.sleep(2)
    return None


def _wait_for_vm_ssh(
    vm_name: str,
    vm_ip: str,
    vm_user: str,
    *,
    timeout_seconds: int = 180,
) -> None:
    ssh_private_key_path = _resolve_private_key_path()
    if ssh_private_key_path is None:
        raise RuntimeError(
            "Missing SSH private key. Set SSH_PRIVATE_KEY_PATH to the private key that matches the VM's authorized key."
        )

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        completed = _run_subprocess(
            [
                "ssh",
                *_ssh_options(vm_name, vm_ip, ssh_private_key_path),
                f"{vm_user}@{vm_ip}",
                "true",
            ],
            capture_output=True,
            text=True,
        )
        if completed.returncode == 0:
            return
        time.sleep(2)

    raise RuntimeError(
        f"Timed out waiting for SSH on '{vm_user}@{vm_ip}' after {timeout_seconds}s (VM '{vm_name}')."
    )


def _ssh_options(vm_name: str, vm_ip: str, ssh_private_key_path: Path) -> list[str]:
    return [
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        f"UserKnownHostsFile={_known_hosts_path()}",
        "-o",
        f"HostKeyAlias={vm_name}",
        "-o",
        "CheckHostIP=no",
        "-o",
        "ConnectTimeout=5",
        "-i",
        str(ssh_private_key_path),
    ]


def _resolve_vm_ssh_access(
    vm_name: str, vm_user: str, *, timeout_seconds: int = 180
) -> tuple[str, Path]:
    vm_ip = _resolve_vm_ip(vm_name)
    if vm_ip is None:
        vm_ip = _wait_for_vm_ip(vm_name, timeout_seconds=timeout_seconds)
    if vm_ip is None:
        raise RuntimeError(f"Timed out waiting for an IP address for VM '{vm_name}'.")
    _wait_for_vm_ssh(vm_name, vm_ip, vm_user, timeout_seconds=timeout_seconds)
    ssh_private_key_path = _resolve_private_key_path()
    if ssh_private_key_path is None:
        raise RuntimeError(
            "Missing SSH private key. Set SSH_PRIVATE_KEY_PATH to the private key that matches the VM's authorized key."
        )
    return vm_ip, ssh_private_key_path


def _remote_shell_script(
    *,
    command: str | list[str],
    working_directory: str | None = None,
    environment: dict[str, str] | None = None,
) -> str:
    script_lines = ["set -euo pipefail"]
    for key, value in sorted((environment or {}).items()):
        script_lines.append(f"export {key}={shlex.quote(value)}")
    if working_directory:
        script_lines.append(f"cd {shlex.quote(working_directory)}")
    if isinstance(command, str):
        script_lines.append(command)
    else:
        script_lines.append(shlex.join(command))
    return "\n".join(script_lines) + "\n"


def _probe_agent_state(
    vm_name: str, vm_ip: str, vm_user: str
) -> tuple[str, list[str], datetime | None, str | None, int | None]:
    ssh_private_key_path = _resolve_private_key_path()
    if ssh_private_key_path is None:
        return "unknown", [], None, None, None

    remote_script = r"""
set -euo pipefail

LOG_DIR="${HOME}/.agent-sandbox"
CONTROL_STATE_PATH="${LOG_DIR}/agent-control.state"
STATE_MAX_AGE_SECONDS="${VM_AGENT_STATE_MAX_AGE_SECONDS:-600}"

read_state_value() {
  local state_path="$1"
  local key="$2"
  [[ -f "${state_path}" ]] || return 1
  awk -F= -v target_key="${key}" '$1 == target_key {print substr($0, index($0, "=") + 1); exit}' "${state_path}" 2>/dev/null
}

is_active_agent_pid() {
  local pid="$1"
  local cmdline_path="/proc/${pid}/cmdline"
  local cmdline=""

  [[ -n "${pid}" && "${pid}" =~ ^[0-9]+$ ]] || return 1
  kill -0 "${pid}" >/dev/null 2>&1 || return 1
  [[ -r "${cmdline_path}" ]] || return 1
  cmdline="$(tr '\000' ' ' < "${cmdline_path}" 2>/dev/null || true)"
  [[ -n "${cmdline}" ]] || return 1
  [[ "${cmdline}" == *"agent_runner_exec"* || "${cmdline}" == *"agent_runner_resume_last"* ]]
}

iso_to_epoch() {
  local raw="$1"
  [[ -n "${raw}" ]] || return 1
  date -u -d "${raw}" +%s 2>/dev/null
}

state_is_fresh() {
  local updated_at="$1"
  local updated_at_epoch now
  updated_at_epoch="$(iso_to_epoch "${updated_at}")" || return 1
  now="$(date -u +%s)"
  (( now - updated_at_epoch <= STATE_MAX_AGE_SECONDS ))
}

if [[ ! -d "${LOG_DIR}" ]]; then
  printf '{"state":"not_started","repos":[],"updated_at":null}\n'
  exit 0
fi

control_status=""
control_updated_at=""
if [[ -f "${CONTROL_STATE_PATH}" ]]; then
  control_status="$(read_state_value "${CONTROL_STATE_PATH}" "status" || true)"
  control_updated_at="$(read_state_value "${CONTROL_STATE_PATH}" "updated_at" || true)"
  if [[ "${control_status}" == "disabled" ]]; then
    printf '{"state":"disabled","repos":[],"updated_at":%s}\n' "$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1] or None))' "${control_updated_at}")"
    exit 0
  fi
fi

shopt -s nullglob
active_entries=()
newest_updated_at="${control_updated_at}"
newest_dispatch_id=""
active_pid=""
found_any_state=0
for state_path in "${LOG_DIR}"/agent-*.state; do
  found_any_state=1
  status="$(read_state_value "${state_path}" "status" || true)"
  updated_at="$(read_state_value "${state_path}" "updated_at" || true)"
  dispatch_id="$(read_state_value "${state_path}" "dispatch_id" || true)"
  pid="$(read_state_value "${state_path}" "pid" || true)"
  if [[ -n "${updated_at}" && ( -z "${newest_updated_at}" || "${updated_at}" > "${newest_updated_at}" ) ]]; then
    newest_updated_at="${updated_at}"
    newest_dispatch_id="${dispatch_id}"
  fi
  if [[ "${status}" != "running" ]]; then
    continue
  fi
  if ! state_is_fresh "${updated_at}" && ! is_active_agent_pid "${pid}"; then
    continue
  fi
  name="$(basename "${state_path}")"
  name="${name#agent-}"
  name="${name%.state}"
  active_entries+=("${name}")
  if [[ -z "${active_pid}" ]]; then
    active_pid="${pid}"
  fi
done
codex_state_path="${LOG_DIR}/codex-resume-last.state"
if [[ -f "${codex_state_path}" ]]; then
  found_any_state=1
  status="$(read_state_value "${codex_state_path}" "status" || true)"
  updated_at="$(read_state_value "${codex_state_path}" "updated_at" || true)"
  dispatch_id="$(read_state_value "${codex_state_path}" "dispatch_id" || true)"
  pid="$(read_state_value "${codex_state_path}" "pid" || true)"
  if [[ -n "${updated_at}" && ( -z "${newest_updated_at}" || "${updated_at}" > "${newest_updated_at}" ) ]]; then
    newest_updated_at="${updated_at}"
    newest_dispatch_id="${dispatch_id}"
  fi
  if [[ "${status}" == "running" ]] && ( state_is_fresh "${updated_at}" || is_active_agent_pid "${pid}" ); then
    active_entries+=("codex-heartbeat")
    if [[ -z "${active_pid}" ]]; then
      active_pid="${pid}"
    fi
  fi
fi
shopt -u nullglob

if [[ ${#active_entries[@]} -gt 0 ]]; then
  repos_json="$(printf '%s\n' "${active_entries[@]}" | python3 -c 'import json,sys; print(json.dumps([line.strip() for line in sys.stdin if line.strip()]))')"
  updated_json="$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1] or None))' "${newest_updated_at}")"
  dispatch_json="$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1] or None))' "${newest_dispatch_id}")"
  pid_json="$(python3 -c 'import json,sys; value=sys.argv[1].strip(); print(json.dumps(int(value) if value.isdigit() else None))' "${active_pid}")"
  printf '{"state":"running","repos":%s,"updated_at":%s,"dispatch_id":%s,"pid":%s}\n' "${repos_json}" "${updated_json}" "${dispatch_json}" "${pid_json}"
  exit 0
fi

if [[ "${found_any_state}" == "1" || -n "${control_updated_at}" ]]; then
  printf '{"state":"idle","repos":[],"updated_at":%s,"dispatch_id":%s,"pid":null}\n' \
    "$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1] or None))' "${newest_updated_at}")" \
    "$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1] or None))' "${newest_dispatch_id}")"
  exit 0
fi

printf '{"state":"not_started","repos":[],"updated_at":null,"dispatch_id":null,"pid":null}\n'
"""

    result = _run_subprocess(
        [
            "ssh",
            *_ssh_options(vm_name, vm_ip, ssh_private_key_path),
            f"{vm_user}@{vm_ip}",
            "bash",
            "-s",
        ],
        capture_output=True,
        text=True,
        timeout=15.0,
        input=remote_script,
    )
    if result.returncode != 0:
        return "unknown", [], None, None, None
    try:
        payload = json.loads((result.stdout or "").strip() or "{}")
    except json.JSONDecodeError:
        return "unknown", [], None, None, None
    updated_at = payload.get("updated_at")
    return (
        str(payload.get("state") or "unknown"),
        [str(item) for item in payload.get("repos", []) if str(item).strip()],
        datetime.fromisoformat(updated_at) if isinstance(updated_at, str) and updated_at else None,
        str(payload.get("dispatch_id")).strip()
        if payload.get("dispatch_id") is not None and str(payload.get("dispatch_id")).strip()
        else None,
        int(payload["pid"]) if isinstance(payload.get("pid"), int) else None,
    )


def _default_issue_session_id(issue_number: str) -> str:
    return f"issue-{issue_number}-{_utc_now():%Y%m%dT%H%M%SZ}-{os.getpid()}"


def _default_vm_session_id(vm_name: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9._-]+", "-", vm_name.strip()).strip("-") or "vm"
    return f"{normalized}-{_utc_now():%Y%m%dT%H%M%SZ}-{os.getpid()}"


def _copilot_host_auth_enabled() -> bool:
    return str(os.environ.get("AGENT_PROVIDER", "")).strip().lower() == "copilot"


def _build_issue_session_manifest(
    *,
    policy: EffectivePolicyConfig,
    session_id: str,
    vm_id: str,
    issue_number: str | None,
    directory_path: str,
    ttl_hours: int | None,
    extra_allowed_tools: list[str | ToolName] | None = None,
    allowed_script_names: list[str] | None = None,
    discord_channels: list[str],
    teams_channel_allowlist: list[str] | None = None,
    teams_chat_allowlist: list[str] | None = None,
    teams_user_allowlist: list[str] | None = None,
    email_mailboxes: list[str],
    email_folders: list[str],
    communication_file_receive_enabled: bool | None = None,
    communication_file_send_enabled: bool | None = None,
    communication_file_max_bytes: int | None = None,
    communication_file_require_approval_for_send: bool | None = None,
) -> SessionManifest:
    resolved_allowed_tools: list[ToolName] = [ToolName.APPROVAL_SUBMIT, ToolName.APPROVAL_CHECK]
    if discord_channels:
        resolved_allowed_tools.extend(
            [
                ToolName.DISCORD_GET_COMMANDS,
                ToolName.DISCORD_POST_STATUS,
                ToolName.ADD_MESSAGE_REACTION,
                ToolName.HAS_PENDING_DISCORD_MESSAGES,
                ToolName.LIST_UNREAD_DISCORD_MESSAGES,
                ToolName.LIST_ALL_DISCORD_MESSAGES,
            ]
        )

    email_requested = bool(email_mailboxes or email_folders)
    resolved_email_mailboxes = email_mailboxes or (
        policy.email_mailbox_allowlist if email_requested else []
    )
    resolved_email_folders = email_folders or (
        sorted(
            {
                folder
                for folders in policy.email_folder_allowlist_by_mailbox.values()
                for folder in folders
            }
        )
        if email_requested
        else []
    )
    if email_requested:
        resolved_allowed_tools.extend(
            [ToolName.EMAIL_LIST_UNREAD, ToolName.EMAIL_GET_MESSAGE, ToolName.EMAIL_CREATE_DRAFT]
        )
        if policy.email_send_enabled:
            resolved_allowed_tools.append(ToolName.EMAIL_SEND_APPROVED)

    metadata = {"issue_number": issue_number} if issue_number is not None else {}
    if _copilot_host_auth_enabled():
        resolved_allowed_tools.append(ToolName.COPILOT_HOST_AUTH)
        metadata["agent_provider"] = "copilot"

    if extra_allowed_tools:
        seen = set(resolved_allowed_tools)
        for tool in _normalize_allowed_tools(extra_allowed_tools):
            if tool in seen:
                continue
            seen.add(tool)
            resolved_allowed_tools.append(tool)

    return SessionManifest(
        session_id=session_id,
        vm_id=vm_id,
        directory_path=directory_path,
        allowed_tools=list(dict.fromkeys(resolved_allowed_tools)),
        allowed_script_names=_normalize_allowed_script_names(allowed_script_names or []),
        discord_channel_allowlist=discord_channels,
        teams_channel_allowlist=_normalize_teams_channel_allowlist(
            teams_channel_allowlist or [], field_name="teams_channel_allowlist"
        ),
        teams_chat_allowlist=normalize_teams_chat_targets(teams_chat_allowlist),
        teams_user_allowlist=_normalize_teams_user_allowlist(
            teams_user_allowlist or [], field_name="teams_user_allowlist"
        ),
        email_folder_allowlist=resolved_email_folders,
        email_mailbox_allowlist=resolved_email_mailboxes,
        email_folder_allowlist_by_mailbox=policy.email_folder_allowlist_by_mailbox,
        email_draft_recipient_allowlist=policy.email_draft_recipient_allowlist,
        email_draft_domain_allowlist=policy.email_draft_domain_allowlist,
        email_send_enabled=policy.email_send_enabled,
        communication_file_receive_enabled=(
            policy.communication_file_receive_enabled
            if communication_file_receive_enabled is None
            else communication_file_receive_enabled
        ),
        communication_file_send_enabled=(
            policy.communication_file_send_enabled
            if communication_file_send_enabled is None
            else communication_file_send_enabled
        ),
        communication_file_max_bytes=(
            policy.communication_file_max_bytes
            if communication_file_max_bytes is None
            else communication_file_max_bytes
        ),
        communication_file_allowed_mime_types=policy.communication_file_allowed_mime_types,
        communication_file_allowed_extensions=policy.communication_file_allowed_extensions,
        communication_file_blocked_extensions=policy.communication_file_blocked_extensions,
        communication_file_require_approval_for_send=(
            policy.communication_file_require_approval_for_send
            if communication_file_require_approval_for_send is None
            else communication_file_require_approval_for_send
        ),
        expires_at=_utc_now() + _resolve_session_ttl(policy, ttl_hours),
        metadata=metadata,
    )


def _matching_vm_session_ids(vm_name: str, sessions: dict[str, SessionManifest]) -> set[str]:
    return {session_id for session_id, session in sessions.items() if session.vm_id == vm_name}


def _normalize_session_labels(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key).strip()
        if not key or raw_value is None:
            continue
        mapped_value = str(raw_value).strip()
        if mapped_value:
            normalized[key] = mapped_value
    return normalized


def _normalize_allowed_tools(value: list[str | ToolName]) -> list[ToolName]:
    normalized_tools: list[ToolName] = []
    seen: set[ToolName] = set()
    for tool in value:
        normalized_tool = tool if isinstance(tool, ToolName) else ToolName(str(tool).strip())
        if normalized_tool in seen:
            continue
        seen.add(normalized_tool)
        normalized_tools.append(normalized_tool)
    return normalized_tools


def _normalize_allowed_script_names(value: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for script_name in value:
        candidate = str(script_name).strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        normalized.append(candidate)
    return normalized


def _normalize_plain_string_list(value: object, *, field_name: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be a list of strings")
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        candidate = str(item).strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        normalized.append(candidate)
    return normalized


def _normalize_teams_channel_allowlist(value: object, *, field_name: str) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for target in _normalize_plain_string_list(value, field_name=field_name):
        normalized_target = normalize_teams_target(target)
        if normalized_target.startswith("user:"):
            raise ValueError(
                f"{field_name} accepts channel/chat targets only; use teams_user_allowlist for user targets"
            )
        if normalized_target in seen:
            continue
        seen.add(normalized_target)
        normalized.append(normalized_target)
    return normalized


def _normalize_teams_user_allowlist(value: object, *, field_name: str) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for target in _normalize_plain_string_list(value, field_name=field_name):
        normalized_target = normalize_teams_user_target(target)
        if normalized_target in seen:
            continue
        seen.add(normalized_target)
        normalized.append(normalized_target)
    return normalized


COMMUNICATION_FILE_SESSION_POLICY_FIELDS: tuple[str, ...] = (
    "communication_file_receive_enabled",
    "communication_file_send_enabled",
    "communication_file_max_bytes",
    "communication_file_allowed_mime_types",
    "communication_file_allowed_extensions",
    "communication_file_blocked_extensions",
    "communication_file_require_approval_for_send",
)


def _normalize_session_policy_bool(value: object, *, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(f"{field_name} must be a boolean")


def _normalize_session_policy_non_negative_int_or_none(
    value: object, *, field_name: str
) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be a non-negative integer or null")
    if value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer or null")
    return value


def _communication_file_session_policy_update(payload: Mapping[str, Any]) -> dict[str, Any]:
    update: dict[str, Any] = {}
    for field_name in (
        "communication_file_receive_enabled",
        "communication_file_send_enabled",
        "communication_file_require_approval_for_send",
    ):
        if field_name in payload:
            update[field_name] = _normalize_session_policy_bool(
                payload.get(field_name), field_name=field_name
            )
    if "communication_file_max_bytes" in payload:
        update["communication_file_max_bytes"] = (
            _normalize_session_policy_non_negative_int_or_none(
                payload.get("communication_file_max_bytes"),
                field_name="communication_file_max_bytes",
            )
        )
    for field_name in (
        "communication_file_allowed_mime_types",
        "communication_file_allowed_extensions",
        "communication_file_blocked_extensions",
    ):
        if field_name in payload:
            update[field_name] = _normalize_plain_string_list(
                payload.get(field_name), field_name=field_name
            )
    return update


def _session_policy_update_from_payload(
    session: SessionManifest, payload: Mapping[str, Any]
) -> dict[str, Any]:
    allowed_fields = {
        "allowed_script_names",
        "discord_channel_allowlist",
        "discord_channels",
        "teams_channel_allowlist",
        "teams_channels",
        "teams_chat_allowlist",
        "teams_user_allowlist",
        "metadata",
        *COMMUNICATION_FILE_SESSION_POLICY_FIELDS,
    }
    unsupported = sorted(set(payload) - allowed_fields)
    if unsupported:
        raise ValueError("Unsupported session policy fields: " + ", ".join(unsupported))

    update: dict[str, Any] = {}
    if "allowed_script_names" in payload:
        update["allowed_script_names"] = _normalize_allowed_script_names(
            _normalize_plain_string_list(
                payload.get("allowed_script_names"), field_name="allowed_script_names"
            )
        )

    discord_field = (
        "discord_channel_allowlist"
        if "discord_channel_allowlist" in payload
        else "discord_channels"
        if "discord_channels" in payload
        else None
    )
    if discord_field is not None:
        update["discord_channel_allowlist"] = _normalize_plain_string_list(
            payload.get(discord_field),
            field_name=discord_field,
        )

    teams_field = (
        "teams_channel_allowlist"
        if "teams_channel_allowlist" in payload
        else "teams_channels"
        if "teams_channels" in payload
        else None
    )
    if teams_field is not None:
        update["teams_channel_allowlist"] = _normalize_teams_channel_allowlist(
            payload.get(teams_field),
            field_name=teams_field,
        )
    if "teams_chat_allowlist" in payload:
        update["teams_chat_allowlist"] = normalize_teams_chat_targets(
            _normalize_plain_string_list(
                payload.get("teams_chat_allowlist"), field_name="teams_chat_allowlist"
            )
        )
    if "teams_user_allowlist" in payload:
        update["teams_user_allowlist"] = _normalize_teams_user_allowlist(
            payload.get("teams_user_allowlist"),
            field_name="teams_user_allowlist",
        )

    update.update(_communication_file_session_policy_update(payload))

    if "metadata" in payload:
        metadata = payload.get("metadata")
        if not isinstance(metadata, Mapping):
            raise ValueError("metadata must be a JSON object")
        update["metadata"] = dict(metadata)

    return update


def _deep_merge_mapping(base: Mapping[str, Any], update: Mapping[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base)
    for key, value in update.items():
        existing = merged.get(key)
        if isinstance(existing, Mapping) and isinstance(value, Mapping):
            merged[key] = _deep_merge_mapping(existing, value)
            continue
        merged[key] = value
    return merged


def _pkexec_env_command(
    base_cmd: list[str], env: dict[str, str], allowed_keys: tuple[str, ...]
) -> list[str]:
    cmd = ["pkexec", "env"]
    for key in allowed_keys:
        value = env.get(key)
        if value:
            cmd.append(f"{key}={value}")
    cmd.extend(base_cmd)
    return cmd


def _sudo_env_command(
    base_cmd: list[str], env: dict[str, str], allowed_keys: tuple[str, ...]
) -> list[str]:
    preserved_keys = [key for key in allowed_keys if env.get(key)]
    cmd = ["sudo", "-n"]
    if preserved_keys:
        cmd.append(f"--preserve-env={','.join(preserved_keys)}")
    cmd.extend(base_cmd)
    return cmd


def _can_prompt_for_privileges() -> bool:
    if sys.stdin is not None and sys.stdin.isatty():
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


class PrivilegedHelperSession:
    def __init__(self, *, python_executable: str, project_dir: Path) -> None:
        self.python_executable = python_executable
        self.project_dir = project_dir
        self._process: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()

    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self) -> None:
        with self._lock:
            if self.is_running():
                return
            env_cmd = [
                "pkexec",
                "env",
                f"PYTHONPATH={self.project_dir / 'src'}",
                self.python_executable,
                "-m",
                "agent_sandbox.admin.privileged_helper",
            ]
            self._process = subprocess.Popen(
                env_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
            response = self._request_locked({"op": "ping"})
            if response.get("ok") is not True:
                raise RuntimeError(
                    str(response.get("error") or "Failed to start privileged helper.")
                )

    def close(self) -> None:
        with self._lock:
            if not self.is_running():
                self._process = None
                return
            try:
                self._request_locked({"op": "exit"})
            except Exception:
                pass
            process = self._process
            self._process = None
            if process is not None and process.poll() is None:
                process.terminate()

    def call(self, op: str, **payload: object) -> dict[str, object]:
        with self._lock:
            if not self.is_running():
                raise RuntimeError("Privileged helper is not running.")
            response = self._request_locked({"op": op, **payload})
            if response.get("ok") is not True:
                raise RuntimeError(
                    str(response.get("error") or f"Privileged helper op {op} failed.")
                )
            return response

    def _request_locked(self, payload: dict[str, object]) -> dict[str, object]:
        process = self._process
        if process is None or process.stdin is None or process.stdout is None:
            raise RuntimeError("Privileged helper is unavailable.")
        process.stdin.write(json.dumps(payload) + "\n")
        process.stdin.flush()
        line = process.stdout.readline()
        if not line:
            stderr = process.stderr.read().strip() if process.stderr is not None else ""
            raise RuntimeError(stderr or "Privileged helper exited unexpectedly.")
        return json.loads(line)


class AdminService:
    def __init__(
        self,
        runtime_dir: str | Path = DEFAULT_RUNTIME_DIR,
        *,
        legacy_config_path: str | Path | None = None,
        config_path: str | Path | None = None,
    ) -> None:

        from agent_sandbox.admin.facades import (
            SessionAdminDependencies,
            SessionAdminFacade,
            TeamsAdminDependencies,
            TeamsAdminFacade,
            WorkerAdminDependencies,
            WorkerAdminFacade,
        )

        from agent_sandbox.admin.services import (
            IssueGitAdminDependencies,
            IssueGitAdminService,
            IssueGitAdminSupport,
            OperationsAdminService,
            VmAgentAdminDependencies,
            VmAgentAdminService,
            VmAgentAdminSupport,
            WorkflowConfigAdminService,
        )

        candidate_runtime = Path(runtime_dir)
        explicit_legacy = (
            Path(legacy_config_path or config_path) if (legacy_config_path or config_path) else None
        )
        if explicit_legacy is None and candidate_runtime.suffix.casefold() in {".yaml", ".yml"}:
            explicit_legacy = candidate_runtime
            candidate_runtime = resolve_runtime_dir(legacy_config_path=explicit_legacy)
        self.runtime_dir = candidate_runtime
        self.legacy_config_path = explicit_legacy
        self.project_dir = _resolve_project_dir_for_admin_service(
            runtime_dir=self.runtime_dir,
            legacy_config_path=self.legacy_config_path,
        )
        bootstrap_config = load_host_bootstrap_config(
            runtime_dir=self.runtime_dir,
            legacy_config_path=self.legacy_config_path,
        )
        directory_store = DirectoryStore(bootstrap_config.directories_store_path)
        directory_store.ensure_root()
        DirectoryConfigStore(
            directory_store, runtime_dir=bootstrap_config.runtime_dir
        ).ensure_root()
        self.config = load_runtime_gateway_config(
            runtime_dir=bootstrap_config.runtime_dir,
            legacy_config_path=self.legacy_config_path,
        )
        self.runtime = build_runtime(self.config)
        self.skill_library = SkillLibraryRepository(self.config.runtime_dir)
        self.agent_control_store = AgentControlStore(self.config.host_agent_control_state_path)
        self.local_issue_store = JsonStore(
            self.config.runtime_dir / "local-issues.json", LocalIssueRecord
        )
        self.local_issue_counter_path = self.config.runtime_dir / "local-issue-counter.txt"
        self.local_issue_create_lock_path = self.config.runtime_dir / "local-issue-create.lock"
        self.local_issue_yaml_dir = self.config.runtime_dir / "issues"
        self.local_issue_yaml_dir.mkdir(parents=True, exist_ok=True)
        self.use_privileged_helper = False
        self._privileged_helper: PrivilegedHelperSession | None = None
        self.directory_admin = DirectoryAdminService.from_store(
            directory_store=self.runtime.directories,
            gateway_config=self.config,
        )
        self.directory_ownership = DirectoryOwnershipQueries(
            directories=self.directory_admin._directories,
            sessions=self.runtime.session_repository,
            pr_map_path=self.config.pr_map_path,
            ado_processed_store_path=self.config.runtime_dir / "ado-processed-work-items.json",
        )
        self.issue_service = IssueService(
            ProviderConfigResolver(
                self.config,
                directory_store=self.runtime.directories,
                session_store=self.runtime.session_repository,
            )
        )
        self.session_admin = SessionAdminFacade(
            SessionAdminDependencies(
                runtime=self.runtime,
                config=self.config,
                normalize_directory_path=lambda directory_path: (
                    self.issue_git_support.normalize_directory_path(directory_path)
                ),
                direct_owned_entity_ids=lambda directory_path, **kwargs: (
                    self.issue_git_support.direct_owned_entity_ids(directory_path, **kwargs)
                ),
            )
        )
        self.teams_admin = TeamsAdminFacade(
            TeamsAdminDependencies(
                runtime=self.runtime,
                normalize_directory_reference=self._normalize_directory_reference,
            )
        )
        self.worker_admin = WorkerAdminFacade(
            WorkerAdminDependencies(
                runtime=self.runtime,
                config=self.config,
                agent_control_store=self.agent_control_store,
                normalize_directory_path=lambda directory_path: (
                    self.issue_git_support.normalize_directory_path(directory_path)
                ),
                direct_owned_entity_ids=lambda directory_path, **kwargs: (
                    self.issue_git_support.direct_owned_entity_ids(directory_path, **kwargs)
                ),
                read_worker_global_config=self._read_worker_global_config,
                write_worker_global_config=self._write_worker_global_config,
                read_workflow_editor_document=self._read_workflow_editor_document,
                read_workflow_editor_catalog=self._read_workflow_editor_catalog,
                validate_workflow_editor_document=self._validate_workflow_editor_document,
                save_workflow_editor_document=self._save_workflow_editor_document,
                read_workflow_run_snapshot=self._read_workflow_run_snapshot,
                clear_workflow_run=self._clear_workflow_run,
                resume_workflow_run=self._resume_workflow_run,
                workflow_event_journal=self._workflow_event_journal,
                upsert_trigger_config=self._upsert_trigger_config,
                delete_trigger_config=self._delete_trigger_config,
                upsert_effect_config=self._upsert_effect_config,
                delete_effect_config=self._delete_effect_config,
            )
        )
        self.workflow_config_admin = WorkflowConfigAdminService(self.worker_admin)
        self.issue_git_support = IssueGitAdminSupport(
            project_dir=self.project_dir,
            runtime=self.runtime,
            config=self.config,
            local_issue_store=self.local_issue_store,
            local_issue_counter_path=self.local_issue_counter_path,
            local_issue_create_lock_path=self.local_issue_create_lock_path,
            local_issue_yaml_dir=self.local_issue_yaml_dir,
            issue_service=self.issue_service,
            directory_admin=self.directory_admin,
            directory_ownership=self.directory_ownership,
        )
        self.vm_agent_support = VmAgentAdminSupport(
            config=self.config,
            runtime=self.runtime,
            project_dir=self.project_dir,
            local_issue_store=self.local_issue_store,
            worker_admin=self.worker_admin,
            workflow_config_admin=self.workflow_config_admin,
            directory_admin=self.directory_admin,
            get_privileged_helper=lambda: self._privileged_helper,
            get_use_privileged_helper=lambda: self.use_privileged_helper,
        )
        self.issue_git_admin = IssueGitAdminService(
            IssueGitAdminDependencies(
                project_dir=self.issue_git_support.project_dir,
                runtime=self.issue_git_support.runtime,
                config=self.issue_git_support.config,
                local_issue_store=self.issue_git_support.local_issue_store,
                local_issue_yaml_dir=self.issue_git_support.local_issue_yaml_dir,
                issue_service=self.issue_git_support.issue_service,
                normalize_directory_path=self.issue_git_support.normalize_directory_path,
                local_issue_creation_lock=self.issue_git_support.local_issue_creation_lock,
                next_local_issue_number=self.issue_git_support.next_local_issue_number,
                canonical_local_issue_id=self.issue_git_support.canonical_local_issue_id,
                resolve_local_issue_record=self.issue_git_support.resolve_local_issue_record,
                resolve_local_issue_key=self.issue_git_support.resolve_local_issue_key,
                proxy_target_for_issue=self.issue_git_support.proxy_target_for_issue,
                refresh_proxy_cache_from_remote=self.issue_git_support.refresh_proxy_cache_from_remote,
                write_local_issue_yaml=self.issue_git_support.write_local_issue_yaml,
                direct_owned_entity_ids=self.issue_git_support.direct_owned_entity_ids,
            )
        )
        self.vm_agent_admin = VmAgentAdminService(
            VmAgentAdminDependencies(
                config=self.vm_agent_support.config,
                runtime=self.vm_agent_support.runtime,
                project_dir=self.vm_agent_support.project_dir,
                local_issue_store=self.vm_agent_support.local_issue_store,
                worker_admin=self.vm_agent_support.worker_admin,
                get_privileged_helper=self.vm_agent_support.get_privileged_helper,
                get_use_privileged_helper=self.vm_agent_support.get_use_privileged_helper,
                normalize_directory_path=self.vm_agent_support.normalize_directory_path,
                read_worker_global_config=self.vm_agent_support.read_worker_global_config,
                resolve_agent_model_for_session=self.vm_agent_support.resolve_agent_model_for_session,
                resolve_vm_defaults=self.vm_agent_support.resolve_vm_defaults,
                resolve_issue_setup=self.vm_agent_support.resolve_issue_setup,
                resolve_local_issue_record=self.vm_agent_support.resolve_local_issue_record,
                resolve_local_issue_key=self.vm_agent_support.resolve_local_issue_key,
                resolve_repo_source=self.vm_agent_support.resolve_repo_source,
                sync_repo_into_bare_remote=self.vm_agent_support.sync_repo_into_bare_remote,
                clone_repo_into_vm=self.vm_agent_support.clone_repo_into_vm,
            )
        )
        self.operations_admin = OperationsAdminService(
            project_dir=self.project_dir,
            get_privileged_helper=lambda: self._privileged_helper,
            get_use_privileged_helper=lambda: self.use_privileged_helper,
            set_privileged_helper=lambda helper: setattr(self, "_privileged_helper", helper),
            teams_admin=self.teams_admin,
            session_admin=self.session_admin,
            worker_admin=self.worker_admin,
            directory_admin=self.directory_admin,
            privileged_helper_factory=lambda **kwargs: PrivilegedHelperSession(**kwargs),
        )
        self.trigger_repository: TriggerRepository = WorkerConfigTriggerRepository(
            read_config=self._read_trigger_document,
            write_config=self._write_trigger_document,
        )
        self.trigger_event_repository: TriggerEventRepository = JsonTriggerEventRepository()
        self._execution_backend_instance = None

    @property
    def directory_scoped_config_enabled(self) -> bool:
        return self.config.enable_directory_scoped_config

    def effective_execution_backend(self, directory_path: str | None = None) -> str:
        resolved_config = self.runtime.provider_config.resolve_for_directory(directory_path)
        return str(resolved_config.execution_backend or "libvirt_vm")

    def read_deployment_status(self) -> DeploymentStatusPayload:
        payload_path = self.config.runtime_dir / "deployment-status.json"
        raw = _read_json_object(payload_path)
        if not raw:
            return DeploymentStatusPayload()
        try:
            status = DeploymentStatusPayload.model_validate(raw)
        except ValidationError:
            return DeploymentStatusPayload()
        if not status.active:
            return status
        started_at = _coerce_optional_datetime(status.started_at)
        updated_at = _coerce_optional_datetime(status.updated_at)
        status = status.model_copy(update={"started_at": started_at, "updated_at": updated_at})
        checked_at = updated_at or started_at
        is_stale = False
        if checked_at is not None:
            is_stale = (_utc_now() - checked_at).total_seconds() > status.ttl_seconds
        if is_stale:
            return status.model_copy(update={"active": False, "stale": True})
        if not status.message:
            return status.model_copy(update={"message": "Control plane update in progress."})
        return status

    def _execution_backend(self):
        backend = getattr(self, "_execution_backend_instance", None)
        if backend is None:
            from agent_sandbox.execution import build_execution_backend
            from agent_sandbox.execution.libvirt_backend import LibvirtVmExecutionBackend

            if not hasattr(self, "config"):
                backend = LibvirtVmExecutionBackend(vm_admin_service=self.vm_agent_admin)
                self._execution_backend_instance = backend
                return backend

            backend = build_execution_backend(
                self.config,
                admin_service=self,
                project_dir=getattr(self, "project_dir", None),
            )
            self._execution_backend_instance = backend
        return backend

    def _normalize_directory_reference(self, reference: str | None) -> str:
        directory_service = DirectoryService(self.runtime.directories)
        return directory_service.show(reference or "root").directory.path

    def _teams_auth_provider_for_directory(
        self,
        directory_path: str | None,
    ) -> tuple[str, GatewayConfig, str, TeamsDeviceCodeTokenProvider]:
        normalized_directory = self._normalize_directory_reference(directory_path)
        resolved_config = self.runtime.provider_config.resolve_for_directory(normalized_directory)
        if resolved_config.teams_backend != "delegated_graph_api":
            raise ValueError(
                f"Directory '{normalized_directory}' is not configured for delegated Teams auth "
                f"(teams_backend={resolved_config.teams_backend!r})."
            )
        identity_id = str(resolved_config.teams_delegated_identity_id or "").strip()
        if not identity_id:
            raise ValueError(
                f"Directory '{normalized_directory}' is missing teams_delegated_identity_id."
            )
        client_id = str(resolved_config.teams_delegated_client_id or "").strip()
        if not client_id:
            raise ValueError(
                f"Directory '{normalized_directory}' is missing teams_delegated_client_id."
            )
        provider = TeamsDeviceCodeTokenProvider(
            MicrosoftDeviceCodeAuthConfig(
                client_id=client_id,
                tenant_id=resolved_config.teams_delegated_tenant_id,
                scopes=resolved_config.teams_delegated_scopes,
                authority_base_url=resolved_config.teams_delegated_authority_base_url,
                timeout_seconds=resolved_config.teams_timeout_seconds,
            ),
            JsonTeamsIdentityCredentialStore(
                resolved_config.teams_delegated_credential_store_path,
                identity_id,
            ),
        )
        return normalized_directory, resolved_config, identity_id, provider

    def _teams_auth_payload(
        self,
        *,
        directory_path: str,
        config: GatewayConfig,
        identity_id: str,
        credentials: TeamsAuthCredentials | None,
    ) -> dict[str, object]:
        return {
            "directory_path": directory_path,
            "identity_id": identity_id,
            "backend": config.teams_backend,
            "authenticated": credentials is not None,
            "status": "authenticated" if credentials is not None else "reauth-required",
            "account_identifier": identity_id,
            "tenant_id": config.teams_delegated_tenant_id,
            "scopes": list(config.teams_delegated_scopes),
            "expires_at": credentials.expires_at if credentials is not None else None,
            "issued_at": credentials.issued_at if credentials is not None else None,
            "store_path": str(config.teams_delegated_credential_store_path),
        }

    def teams_auth_login(self, *, directory: str = "root") -> DeviceCodeLoginSession:
        return self.operations_admin.teams_auth_login(directory=directory)

    def teams_auth_complete_login(
        self,
        login_session: DeviceCodeLoginSession,
        *,
        directory: str = "root",
        timeout_seconds: float | None = None,
    ) -> dict[str, object]:
        return self.operations_admin.teams_auth_complete_login(
            login_session,
            directory=directory,
            timeout_seconds=timeout_seconds,
        )

    def teams_auth_status(self, *, directory: str = "root") -> dict[str, object]:
        return self.operations_admin.teams_auth_status(directory=directory)

    def teams_auth_logout(self, *, directory: str = "root") -> dict[str, object]:
        return self.operations_admin.teams_auth_logout(directory=directory)

    def teams_auth_list(self) -> list[dict[str, object]]:
        return self.operations_admin.teams_auth_list()

    def teams_auth_list_channels(
        self,
        *,
        directory: str = "root",
        team_id: str,
        channel_name: str | None = None,
        include_all: bool = False,
    ) -> dict[str, object]:
        return self.operations_admin.teams_auth_list_channels(
            directory=directory,
            team_id=team_id,
            channel_name=channel_name,
            include_all=include_all,
        )

    def _teams_graph_list_channels(
        self,
        config: GatewayConfig,
        provider: TeamsDeviceCodeTokenProvider,
        *,
        team_id: str,
    ) -> list[dict[str, Any]]:
        encoded_team_id = quote(team_id, safe="")
        next_url = f"{config.teams_graph_api_base_url}/teams/{encoded_team_id}/channels"
        channels: list[dict[str, Any]] = []
        with httpx.Client(timeout=config.teams_timeout_seconds) as client:
            while next_url:
                response = client.get(
                    next_url,
                    headers={
                        "Authorization": f"Bearer {provider.get_bearer_token()}",
                        "Content-Type": "application/json",
                    },
                )
                response.raise_for_status()
                payload = response.json()
                for item in payload.get("value", []):
                    if isinstance(item, dict):
                        channels.append(item)
                raw_next = payload.get("@odata.nextLink")
                next_url = str(raw_next).strip() if raw_next else ""
        return channels

    def unlock_privileged_operations(self) -> str:
        return self.operations_admin.unlock_privileged_operations()

    def lock_privileged_operations(self) -> None:
        return self.operations_admin.lock_privileged_operations()

    def privileged_operations_unlocked(self) -> bool:
        return self.operations_admin.privileged_operations_unlocked()

    def privileged_helper_required(self) -> bool:
        return self.operations_admin.privileged_helper_required()

    def _session_ids_by_vm(self) -> dict[str, list[str]]:
        mapping: dict[str, list[str]] = {}
        for session_id, session in self.runtime.session_repository.read_all().items():
            if session.vm_id:
                mapping.setdefault(session.vm_id, []).append(session_id)
        for session_ids in mapping.values():
            session_ids.sort()
        return mapping

    def _directory_paths_by_vm(self) -> dict[str, list[str]]:
        mapping: dict[str, set[str]] = {}
        for session in self.runtime.session_repository.list_sessions():
            if session.vm_id:
                mapping.setdefault(session.vm_id, set()).add(session.directory_path)
        return {vm_id: sorted(paths) for vm_id, paths in mapping.items()}

    def _labels_by_vm(self) -> dict[str, dict[str, str]]:
        mapping: dict[str, dict[str, str] | None] = {}
        sessions = self.runtime.session_repository.read_all()
        for session_id in sorted(sessions):
            session = sessions[session_id]
            if not session.vm_id:
                continue
            labels = _normalize_session_labels(session.metadata.get("labels"))
            current = mapping.get(session.vm_id)
            if current is None:
                mapping[session.vm_id] = dict(labels)
                continue
            mapping[session.vm_id] = {
                key: value for key, value in current.items() if labels.get(key) == value
            }
        return {vm_id: labels for vm_id, labels in mapping.items() if labels}

    def _normalize_directory_path(self, directory_path: str | None) -> str:
        if not directory_path:
            return "root"
        result = self.directory_admin.get(directory_path)
        if not result.ok or result.data is None:
            return "root"
        return result.data.details.directory.path

    def _resolve_vm_defaults(self, directory_path: str) -> EffectiveVmDefaultsConfig:
        resolver = VmDefaultsConfigResolver(
            self.config,
            directory_store=self.runtime.directories,
            session_store=self.runtime.session_repository,
        )
        return resolver.resolve_for_directory(directory_path)

    def _resolve_issue_setup(
        self, directory_path: str, *, root_dir: Path
    ) -> EffectiveIssueSetupConfig:
        return resolve_effective_issue_setup_config(
            config=self.config,
            runtime=self.runtime,
            directory_path=directory_path,
            root_dir=root_dir,
        )

    def _resolve_agent_model_for_session(self, session_or_id: SessionManifest | str) -> str:
        if isinstance(session_or_id, SessionManifest):
            session = session_or_id
            session_id = session.session_id
        else:
            session_id = str(session_or_id).strip()
            if not session_id:
                raise ValueError(
                    "Cannot resolve AGENT_MODEL: missing session id. Provide a valid session id or SessionManifest."
                )
            session = self.runtime.session_repository.get_session(session_id)
            if session is None:
                raise ValueError(
                    f"Cannot resolve AGENT_MODEL for session '{session_id}': session not found. "
                    "Remediation: create the session first or pass a SessionManifest with session.model set."
                )
        issue_setup = self._resolve_issue_setup(session.directory_path, root_dir=self.project_dir)
        return resolve_agent_model_for_manifest(session, issue_setup=issue_setup)

    def resolve_agent_model_for_target(self, target: str) -> str:
        session = self.runtime.session_repository.resolve_session_target(target)
        if session is None:
            raise ValueError(f"Unknown session target: {target}")
        return self._resolve_agent_model_for_session(session.session_id)

    def _resolve_repo_source(self, repo_source: str) -> tuple[str, str]:
        resolve_script = self.project_dir / "scripts" / "resolve_repo_source.sh"
        path_result = _run_subprocess(
            [str(resolve_script), "--path-only", repo_source], capture_output=True, text=True
        )
        if path_result.returncode != 0:
            raise RuntimeError(
                (path_result.stderr or path_result.stdout or "").strip()
                or f"Failed to resolve repo source: {repo_source}"
            )
        name_result = _run_subprocess(
            [str(resolve_script), "--name-only", repo_source], capture_output=True, text=True
        )
        if name_result.returncode != 0:
            raise RuntimeError(
                (name_result.stderr or name_result.stdout or "").strip()
                or f"Failed to determine repo name: {repo_source}"
            )
        return (path_result.stdout or "").strip(), (name_result.stdout or "").strip()

    def _sync_repo_into_bare_remote(
        self,
        repo_name: str,
        resolved_repo_path: str,
        *,
        issue_setup: EffectiveIssueSetupConfig,
        repo_source: str | None = None,
    ) -> Path:
        create_remote_script = self.project_dir / "scripts" / "create_git_remote.sh"
        completed = _run_subprocess(
            [str(create_remote_script), repo_name, "agents"],
            capture_output=True,
            text=True,
            env=os.environ
            | {
                "AGENT_SANDBOX_GIT_ROOT": issue_setup.git_root,
                "AGENT_SANDBOX_GIT_HOST": issue_setup.git_host,
                "AGENT_SANDBOX_GIT_USER": issue_setup.git_user,
                "AGENT_SANDBOX_GIT_SSH_ALIAS": issue_setup.git_ssh_alias,
            },
        )
        if completed.returncode != 0:
            raise RuntimeError(
                (completed.stderr or completed.stdout or "").strip()
                or f"Failed to create git remote for {repo_name}"
            )
        bare_repo_path = Path(issue_setup.git_root) / f"{repo_name}.git"
        fetch_result = _run_subprocess(
            [
                "git",
                f"--git-dir={bare_repo_path}",
                "fetch",
                "--prune",
                resolved_repo_path,
                "+refs/heads/*:refs/heads/*",
                "+refs/tags/*:refs/tags/*",
            ],
            capture_output=True,
            text=True,
        )
        if fetch_result.returncode != 0:
            raise RuntimeError(
                (fetch_result.stderr or fetch_result.stdout or "").strip()
                or f"Failed to mirror repo into {bare_repo_path}"
            )
        trusted_repo_result = _run_subprocess(
            [
                "git",
                f"--git-dir={bare_repo_path}",
                "config",
                "agent-sandbox.trustedRepoPath",
                resolved_repo_path,
            ],
            capture_output=True,
            text=True,
        )
        if trusted_repo_result.returncode != 0:
            raise RuntimeError(
                (trusted_repo_result.stderr or trusted_repo_result.stdout or "").strip()
                or f"Failed to record trusted repo path for {bare_repo_path}"
            )
        mirror_source = (repo_source or resolved_repo_path).strip()
        mirror_source_result = _run_subprocess(
            [
                "git",
                f"--git-dir={bare_repo_path}",
                "config",
                "agent-sandbox.mirrorSource",
                mirror_source,
            ],
            capture_output=True,
            text=True,
        )
        if mirror_source_result.returncode != 0:
            raise RuntimeError(
                (mirror_source_result.stderr or mirror_source_result.stdout or "").strip()
                or f"Failed to record mirror source for {bare_repo_path}"
            )
        mirror_remote_name = _mirror_remote_name_for_repo(resolved_repo_path)
        mirror_remote_result = _run_subprocess(
            [
                "git",
                f"--git-dir={bare_repo_path}",
                "config",
                "agent-sandbox.mirrorRemoteName",
                mirror_remote_name,
            ],
            capture_output=True,
            text=True,
        )
        if mirror_remote_result.returncode != 0:
            raise RuntimeError(
                (mirror_remote_result.stderr or mirror_remote_result.stdout or "").strip()
                or f"Failed to record mirror remote name for {bare_repo_path}"
            )
        mirror_sync_result = _run_subprocess(
            ["git", f"--git-dir={bare_repo_path}", "config", "agent-sandbox.mirrorSyncRemote", "1"],
            capture_output=True,
            text=True,
        )
        if mirror_sync_result.returncode != 0:
            raise RuntimeError(
                (mirror_sync_result.stderr or mirror_sync_result.stdout or "").strip()
                or f"Failed to record mirror sync policy for {bare_repo_path}"
            )
        mirror_force_local_result = _run_subprocess(
            ["git", f"--git-dir={bare_repo_path}", "config", "agent-sandbox.mirrorForceLocal", "0"],
            capture_output=True,
            text=True,
        )
        if mirror_force_local_result.returncode != 0:
            raise RuntimeError(
                (mirror_force_local_result.stderr or mirror_force_local_result.stdout or "").strip()
                or f"Failed to record mirror force-local policy for {bare_repo_path}"
            )
        canonical_origin_url = _resolve_canonical_origin_url_for_repo(
            resolved_repo_path, mirror_remote_name
        )
        if canonical_origin_url:
            canonical_config_result = _run_subprocess(
                [
                    "git",
                    f"--git-dir={bare_repo_path}",
                    "config",
                    "agent-sandbox.canonicalOriginUrl",
                    canonical_origin_url,
                ],
                capture_output=True,
                text=True,
            )
            if canonical_config_result.returncode != 0:
                raise RuntimeError(
                    (canonical_config_result.stderr or canonical_config_result.stdout or "").strip()
                    or f"Failed to record canonical origin URL for {bare_repo_path}"
                )
        default_base_branch = _default_base_branch_for_repo_remote(
            resolved_repo_path, mirror_remote_name
        )
        if default_base_branch:
            base_branch_config_result = _run_subprocess(
                [
                    "git",
                    f"--git-dir={bare_repo_path}",
                    "config",
                    "agent-sandbox.baseBranch",
                    default_base_branch,
                ],
                capture_output=True,
                text=True,
            )
            if base_branch_config_result.returncode != 0:
                raise RuntimeError(
                    (
                        base_branch_config_result.stderr or base_branch_config_result.stdout or ""
                    ).strip()
                    or f"Failed to record base branch for {bare_repo_path}"
                )
            symbolic_ref_result = _run_subprocess(
                [
                    "git",
                    f"--git-dir={bare_repo_path}",
                    "symbolic-ref",
                    "HEAD",
                    f"refs/heads/{default_base_branch}",
                ],
                capture_output=True,
                text=True,
            )
            if symbolic_ref_result.returncode != 0:
                raise RuntimeError(
                    (symbolic_ref_result.stderr or symbolic_ref_result.stdout or "").strip()
                    or f"Failed to set HEAD for {bare_repo_path}"
                )
        return bare_repo_path

    def _clone_repo_into_vm(
        self,
        *,
        vm_name: str,
        vm_user: str,
        session_id: str,
        repo_name: str,
        issue_setup: EffectiveIssueSetupConfig,
        repo_source: str | None = None,
        clone_branch: str | None = None,
        issue_number: str | None = None,
        vm_ip: str | None = None,
    ) -> str:
        bootstrap_git_script = self.project_dir / "scripts" / "bootstrap_git_vm.sh"
        completed = _run_subprocess(
            [str(bootstrap_git_script), vm_name, vm_user],
            capture_output=True,
            text=True,
            env=os.environ
            | {
                "AGENT_SANDBOX_GIT_ROOT": issue_setup.git_root,
                "AGENT_SANDBOX_GIT_HOST": issue_setup.git_host,
                "AGENT_SANDBOX_GIT_USER": issue_setup.git_user,
                "AGENT_SANDBOX_GIT_SSH_ALIAS": issue_setup.git_ssh_alias,
            },
        )
        if completed.returncode != 0:
            raise RuntimeError(
                (completed.stderr or completed.stdout or "").strip()
                or f"Failed to bootstrap guest git for {vm_name}"
            )

        vm_ip = vm_ip or _resolve_vm_ip(vm_name)
        ssh_private_key_path = _resolve_private_key_path()
        if not vm_ip or ssh_private_key_path is None:
            raise RuntimeError(f"Unable to resolve SSH access for {vm_name}")

        remote_url = f"ssh://{issue_setup.git_ssh_alias}{issue_setup.git_root}/{repo_name}.git"
        pr_notes_ref = agent_pr_notes_ref_for_session(session_id)
        canonical_remote_url = (
            repo_source.strip() if repo_source and _is_remote_git_source(repo_source) else ""
        )
        guest_repo_root = f"/home/{vm_user}/{repo_name}"
        agent_pr_note_helper_b64 = _guest_agent_pr_note_helper_b64(self.project_dir)
        agent_push_helper_b64 = _guest_push_agent_helper_b64(self.project_dir)
        sync_base_helper_b64 = _guest_sync_base_helper_b64(self.project_dir)
        clone_branch_line = ""
        clone_command = 'git clone "${REMOTE_URL}" "${TARGET_DIR}"'
        if clone_branch:
            clone_command = 'git clone --branch "${CLONE_BRANCH}" "${REMOTE_URL}" "${TARGET_DIR}"'
            clone_branch_line = f'CLONE_BRANCH="{clone_branch}"\n'
        issue_config_line = ""
        if issue_number:
            issue_config_line = (
                f'git -C "${{TARGET_DIR}}" config agent-sandbox.issueNumber "{issue_number}"\n'
                f'git -C "${{TARGET_DIR}}" config agent-sandbox.destinationBranch "feature/issue-{issue_number}"\n'
            )
        branch_suffix_line = (
            'git -C "${TARGET_DIR}" config agent-sandbox.branchSuffix "${VM_NAME}"\n'
        )
        remote_script = f"""
set -euo pipefail
. ~/.profile
TARGET_DIR="{guest_repo_root}"
REMOTE_URL="{remote_url}"
CANONICAL_REMOTE_URL="{canonical_remote_url}"
SESSION_ID="{session_id}"
VM_NAME="{vm_name}"
PR_NOTES_REF="{pr_notes_ref}"
AGENT_PR_NOTE_HELPER_B64="{agent_pr_note_helper_b64}"
AGENT_PUSH_HELPER_B64="{agent_push_helper_b64}"
SYNC_BASE_HELPER_B64="{sync_base_helper_b64}"
WORK_BRANCH="work"
{clone_branch_line}\
if [[ -e "${{TARGET_DIR}}" ]]; then
  echo "Guest repo path already exists: ${{TARGET_DIR}}" >&2
  exit 1
fi
{clone_command}
if [[ -n "${{CANONICAL_REMOTE_URL}}" ]]; then
  git -C "${{TARGET_DIR}}" remote set-url origin "${{CANONICAL_REMOTE_URL}}"
  git -C "${{TARGET_DIR}}" config "url.${{REMOTE_URL}}.insteadOf" "${{CANONICAL_REMOTE_URL}}"
  git -C "${{TARGET_DIR}}" remote add agent "${{REMOTE_URL}}"
else
  git -C "${{TARGET_DIR}}" remote add agent "${{REMOTE_URL}}"
fi
git -C "${{TARGET_DIR}}" config user.name "Truman Bot"
git -C "${{TARGET_DIR}}" config user.email "truman@quinth.se"
BASE_BRANCH="${{CLONE_BRANCH:-}}"
if [[ -z "${{BASE_BRANCH}}" ]]; then
  BASE_BRANCH="$(git -C "${{TARGET_DIR}}" branch --show-current || true)"
fi
if [[ -n "${{BASE_BRANCH}}" ]]; then
  git -C "${{TARGET_DIR}}" config agent-sandbox.baseBranch "${{BASE_BRANCH}}"
fi
git -C "${{TARGET_DIR}}" config agent-sandbox.workBranch "${{WORK_BRANCH}}"
CURRENT_BRANCH="$(git -C "${{TARGET_DIR}}" branch --show-current || true)"
if [[ "${{CURRENT_BRANCH}}" != "${{WORK_BRANCH}}" ]]; then
  if git -C "${{TARGET_DIR}}" show-ref --verify --quiet "refs/heads/${{WORK_BRANCH}}"; then
    git -C "${{TARGET_DIR}}" switch "${{WORK_BRANCH}}"
  else
    git -C "${{TARGET_DIR}}" switch -c "${{WORK_BRANCH}}"
  fi
fi
git -C "${{TARGET_DIR}}" config agent-sandbox.sessionId "${{SESSION_ID}}"
git -C "${{TARGET_DIR}}" config agent-sandbox.prNotesRef "${{PR_NOTES_REF}}"
{issue_config_line}{branch_suffix_line}install -d -m 0755 "${{TARGET_DIR}}/.agent-sandbox/bin"
python3 - "${{TARGET_DIR}}/.agent-sandbox/bin/agent-pr-note" "${{AGENT_PR_NOTE_HELPER_B64}}" <<'PY'
import base64
import os
import sys
from pathlib import Path

target_path = Path(sys.argv[1])
target_path.write_bytes(base64.b64decode(sys.argv[2]))
os.chmod(target_path, 0o755)
PY
python3 - "${{TARGET_DIR}}/.agent-sandbox/bin/push-agent" "${{AGENT_PUSH_HELPER_B64}}" <<'PY'
import base64
import os
import sys
from pathlib import Path

target_path = Path(sys.argv[1])
target_path.write_bytes(base64.b64decode(sys.argv[2]))
os.chmod(target_path, 0o755)
PY
python3 - "${{TARGET_DIR}}/.agent-sandbox/bin/sync-base" "${{SYNC_BASE_HELPER_B64}}" <<'PY'
import base64
import os
import sys
from pathlib import Path

target_path = Path(sys.argv[1])
target_path.write_bytes(base64.b64decode(sys.argv[2]))
os.chmod(target_path, 0o755)
PY
git -C "${{TARGET_DIR}}" config alias.agent-pr-note '!f(){{ repo_root=$(git rev-parse --show-toplevel); "${{repo_root}}/.agent-sandbox/bin/agent-pr-note" "$@"; }}; f'
git -C "${{TARGET_DIR}}" config alias.push-agent '!f(){{ repo_root=$(git rev-parse --show-toplevel); "${{repo_root}}/.agent-sandbox/bin/push-agent" "$@"; }}; f'
git -C "${{TARGET_DIR}}" config alias.sync-base '!f(){{ repo_root=$(git rev-parse --show-toplevel); "${{repo_root}}/.agent-sandbox/bin/sync-base" "$@"; }}; f'
"""
        ssh_result = _run_subprocess(
            [
                "ssh",
                *_ssh_options(vm_name, vm_ip, ssh_private_key_path),
                f"{vm_user}@{vm_ip}",
                "bash",
                "-s",
            ],
            capture_output=True,
            text=True,
            timeout=60.0,
            input=remote_script,
        )
        if ssh_result.returncode != 0:
            raise RuntimeError(
                (ssh_result.stderr or ssh_result.stdout or "").strip()
                or f"Failed to clone repo into {vm_name}"
            )
        return guest_repo_root

    def _direct_owned_entity_ids(self, directory_path: str, *, kind: str) -> set[str]:
        snapshot = self.directory_ownership.list_entities(directory_path, include_descendants=False)
        return {entity.entity_id for entity in snapshot.entities if entity.kind == kind}

    def list_sessions(
        self,
        *,
        include_expired: bool = False,
        expired_only: bool = False,
        directory_path: str | None = None,
        direct_only: bool = False,
    ) -> list[SessionSummary]:
        return self.operations_admin.list_sessions(
            include_expired=include_expired,
            expired_only=expired_only,
            directory_path=directory_path,
            direct_only=direct_only,
        )

    def list_wake_schedules(
        self,
        *,
        session_id: str | None = None,
        include_disabled: bool = True,
    ) -> list[dict[str, Any]]:
        return self.operations_admin.list_wake_schedules(
            session_id=session_id,
            include_disabled=include_disabled,
        )

    def _active_wake_requests(
        self, *, exclude_request_id: str | None = None
    ) -> list[WakeScheduledRequest]:
        requests = self.runtime.wake_store.list_requests(include_disabled=False)
        if not exclude_request_id:
            return requests
        return [request for request in requests if request.request_id != exclude_request_id]

    def _enforce_wake_schedule_guardrails(
        self,
        *,
        session_id: str,
        schedule: WakeScheduleDefinition,
        enabled: bool,
        exclude_request_id: str | None = None,
    ) -> None:
        if not enabled:
            return
        active_requests = self._active_wake_requests(exclude_request_id=exclude_request_id)
        max_active_per_session = self.config.wake_schedule_max_active_per_session
        if max_active_per_session is not None:
            active_for_session = sum(
                1 for request in active_requests if request.session_id == session_id
            )
            if active_for_session >= max_active_per_session:
                raise ValueError(
                    "Wake schedule limit reached for session "
                    f"{session_id}: max_active_per_session={max_active_per_session}"
                )
        max_active_total = self.config.wake_schedule_max_active_total
        if max_active_total is not None and len(active_requests) >= max_active_total:
            raise ValueError(f"Wake schedule limit reached: max_active_total={max_active_total}")
        min_interval_seconds = self.config.wake_schedule_min_interval_seconds
        cron = str(schedule.cron or "").strip()
        if min_interval_seconds is not None and cron:
            actual_interval_seconds = _cron_min_interval_seconds(cron)
            if actual_interval_seconds is None:
                raise ValueError(f"Unable to determine cron interval for schedule: {cron}")
            if actual_interval_seconds < min_interval_seconds:
                raise ValueError(
                    "Recurring wake schedule interval is too short: "
                    f"minimum={min_interval_seconds:g}s actual={actual_interval_seconds:g}s"
                )

    def create_wake_schedule(
        self,
        *,
        session_id: str,
        prompt: str,
        resume_at: datetime | None = None,
        cron: str | None = None,
        resume_mode: str = "same_context",
        enabled: bool = True,
        request_id: str | None = None,
        source: str | None = "admin_cli",
        metadata: dict[str, Any] | None = None,
    ) -> WakeScheduledRequest:
        return self.operations_admin.create_wake_schedule(
            session_id=session_id,
            prompt=prompt,
            resume_at=resume_at,
            cron=cron,
            resume_mode=resume_mode,
            enabled=enabled,
            request_id=request_id,
            source=source,
            metadata=metadata,
        )

    def edit_wake_schedule(
        self,
        request_id: str,
        *,
        resume_at: datetime | None = None,
        cron: str | None = None,
        prompt: str | None = None,
        resume_mode: str | None = None,
        enabled: bool | None = None,
    ) -> WakeScheduledRequest:
        return self.operations_admin.edit_wake_schedule(
            request_id,
            resume_at=resume_at,
            cron=cron,
            prompt=prompt,
            resume_mode=resume_mode,
            enabled=enabled,
        )

    def delete_wake_schedule(self, request_id: str) -> bool:
        return self.operations_admin.delete_wake_schedule(request_id)

    def renew_session(self, target: str, *, extension: str = "1d") -> SessionActionResult:
        return self.operations_admin.renew_session(target, extension=extension)

    def revoke_session(self, target: str) -> SessionActionResult:
        return self.operations_admin.revoke_session(target)

    def delete_session(self, target: str) -> SessionActionResult:
        sessions = {
            session.session_id: session
            for session in self.runtime.session_repository.list_sessions()
        }
        session_id, manifest = _resolve_session_target(target, sessions)
        associated_agent = self._session_associated_agent_description(session_id, manifest)
        if associated_agent:
            raise ValueError(
                f"Session {session_id!r} is associated with {associated_agent}. "
                "Delete the agent instead so backend cleanup can run."
            )

        worker_session_deleted, removed_mappings = self._delete_detached_worker_session_state(
            session_id
        )
        result = self.operations_admin.delete_session(session_id)
        removed_approvals = _prune_approvals_for_sessions(self.runtime, session_ids={session_id})
        cleanup_parts: list[str] = []
        if worker_session_deleted:
            cleanup_parts.append("worker_session_deleted=1")
        if removed_mappings:
            cleanup_parts.append(f"removed_mappings={removed_mappings}")
        if removed_approvals:
            cleanup_parts.append(f"removed_approvals={removed_approvals}")
        if cleanup_parts:
            result.message = f"{result.message}; " + " ".join(cleanup_parts)
        return result

    def set_session_allowed_tools(
        self, target: str, allowed_tools: list[str | ToolName]
    ) -> SessionActionResult:
        return self.operations_admin.set_session_allowed_tools(target, allowed_tools)

    def set_session_allowed_script_names(
        self, target: str, allowed_script_names: list[str]
    ) -> SessionActionResult:
        return self.operations_admin.set_session_allowed_script_names(target, allowed_script_names)

    def update_session_policy(
        self, target: str, payload: dict[str, object]
    ) -> SessionActionResult:
        return self.operations_admin.update_session_policy(target, payload)

    def set_session_model(self, target: str, model: str) -> SessionActionResult:
        return self.operations_admin.set_session_model(target, model)

    def list_vms(
        self,
        *,
        include_inactive: bool = True,
        omit_state: bool = False,
        vm_user: str = "agent",
        directory_path: str | None = None,
        direct_only: bool = False,
    ) -> list[VmSummary]:
        return self.vm_agent_admin.list_vms(
            include_inactive=include_inactive,
            omit_state=omit_state,
            vm_user=vm_user,
            directory_path=directory_path,
            direct_only=direct_only,
        )

    def _container_worker_execution_repository(self):
        from agent_sandbox.execution.container_worker_repository import (
            ContainerWorkerExecutionRepository,
        )

        return ContainerWorkerExecutionRepository(
            self.config.runtime_dir / "container-worker-executions.json"
        )

    def _container_worker_backend(
        self,
        *,
        directory_path: str | None = None,
        worker_url: str | None = None,
    ):
        from agent_sandbox.execution.container_worker_backend import (
            ContainerWorkerVmExecutionBackend,
        )

        resolved_config = self.runtime.provider_config.resolve_for_directory(
            self._normalize_directory_path(directory_path)
        )
        update: dict[str, Any] = {"execution_backend": "container_worker_vm"}
        normalized_worker_url = str(worker_url or "").strip()
        if normalized_worker_url:
            update["container_worker_base_url"] = normalized_worker_url
        config = resolved_config.model_copy(update=update)
        return ContainerWorkerVmExecutionBackend(config=config)

    @staticmethod
    def _container_worker_backend_url(backend: Any) -> str | None:
        client = getattr(backend, "client", None)
        client_url = str(getattr(client, "base_url", "") or "").strip()
        if client_url:
            return client_url
        config = getattr(backend, "config", None)
        config_url = str(getattr(config, "container_worker_base_url", "") or "").strip()
        return config_url or None

    @staticmethod
    def _container_worker_url_from_metadata(metadata: Any) -> str | None:
        if not isinstance(metadata, Mapping):
            return None
        for key in ("worker_url", "container_worker_base_url"):
            value = str(metadata.get(key) or "").strip()
            if value:
                return value
        return None

    def _container_worker_url_for_target(
        self,
        *,
        session: SessionManifest | None = None,
        record: Any | None = None,
        records: list[Any] | None = None,
    ) -> str | None:
        candidates: list[Any] = []
        if record is not None:
            candidates.append(record)
        if records:
            candidates.extend(
                sorted(
                    records,
                    key=self._container_execution_sort_key,
                    reverse=True,
                )
            )
        for candidate in candidates:
            worker_url = str(getattr(candidate, "worker_url", "") or "").strip()
            if worker_url:
                return worker_url
            metadata_url = self._container_worker_url_from_metadata(
                getattr(candidate, "metadata", None)
            )
            if metadata_url:
                return metadata_url
        if session is not None:
            return self._container_worker_url_from_metadata(session.metadata)
        return None

    @staticmethod
    def _agent_record_directory_path(directory_path: str | None) -> str:
        try:
            return DirectoryPath(segments=str(directory_path or "root")).as_posix()
        except ValueError:
            return "root"

    @classmethod
    def _agent_record_matches_directory(
        cls,
        record_directory_path: str | None,
        selected_directory_path: str,
        *,
        direct_only: bool,
    ) -> bool:
        del direct_only
        record_path = cls._agent_record_directory_path(record_directory_path)
        return record_path == selected_directory_path

    @staticmethod
    def _container_agent_status(last_status: str | None) -> str:
        normalized = str(last_status or "").strip().casefold()
        if normalized in {"created", "starting"}:
            return "starting"
        if normalized == "running":
            return "running"
        if normalized in {"stopped", "exited"}:
            return "stopped"
        if normalized == "failed":
            return "failed"
        if normalized == "removed":
            return "destroyed"
        return "unknown"

    @staticmethod
    def _agent_status_is_active(status: str | None) -> bool:
        return str(status or "").strip().casefold() in {
            "preparing",
            "starting",
            "running",
        }

    @staticmethod
    def _container_execution_sort_key(record: Any) -> tuple[datetime, str]:
        timestamp = (
            getattr(record, "started_at", None)
            or getattr(record, "created_at", None)
            or getattr(record, "updated_at", None)
            or datetime.min.replace(tzinfo=UTC)
        )
        return timestamp, str(getattr(record, "execution_id", "") or "")

    @classmethod
    def _container_session_status(cls, records: list[Any]) -> str:
        if not records:
            return "prepared"
        statuses = [cls._container_agent_status(record.last_status) for record in records]
        if "running" in statuses:
            return "running"
        if "starting" in statuses:
            return "starting"
        latest = max(records, key=cls._container_execution_sort_key)
        latest_status = cls._container_agent_status(latest.last_status)
        if latest_status == "failed":
            return "failed"
        return "idle"

    @staticmethod
    def _normalize_agent_backend(backend: str | None) -> str:
        selected_backend = str(backend or "libvirt_vm").strip()
        if selected_backend in {"libvirt", "libvirt_vm"}:
            return "libvirt_vm"
        if selected_backend == "container_worker_vm":
            return selected_backend
        raise ValueError(f"Unknown agent backend: {selected_backend}")

    @classmethod
    def _normalize_agent_backend_filter(cls, backend: str | None) -> str:
        selected_backend = str(backend or "all").strip()
        if selected_backend in {"", "all"}:
            return "all"
        return cls._normalize_agent_backend(selected_backend)

    @staticmethod
    def _agent_optional_string(payload: Mapping[str, Any], key: str) -> str | None:
        value = payload.get(key)
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _agent_string_list(payload: Mapping[str, Any], key: str) -> list[str] | None:
        if key not in payload or payload.get(key) is None:
            return None
        value = payload.get(key)
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)):
            raise ValueError(f"{key} must be a list of strings")
        normalized = [str(item).strip() for item in value if str(item).strip()]
        return normalized

    @staticmethod
    def _agent_int_or_none(payload: Mapping[str, Any], key: str) -> int | None:
        if key not in payload:
            return None
        value = payload.get(key)
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} must be an integer") from exc

    @staticmethod
    def _container_session_target_name(session: SessionManifest) -> str:
        metadata = session.metadata if isinstance(session.metadata, dict) else {}
        return (
            str(metadata.get("target_name") or session.vm_provider_id or session.session_id).strip()
            or session.session_id
        )

    @staticmethod
    def _is_container_session(session: SessionManifest) -> bool:
        metadata = session.metadata if isinstance(session.metadata, dict) else {}
        return (
            str(session.vm_provider or "").strip() == "container_worker_vm"
            or str(metadata.get("execution_backend") or "").strip() == "container_worker_vm"
        )

    @staticmethod
    def _agent_session_policy_payload(session: SessionManifest | None) -> dict[str, Any] | None:
        if session is None:
            return None
        labels = _normalize_session_labels(session.metadata.get("labels"))
        return {
            "session_id": session.session_id,
            "model": session.model,
            "expires_at": session.expires_at,
            "allowed_tools": [
                tool.value if isinstance(tool, ToolName) else str(tool)
                for tool in session.allowed_tools
            ],
            "allowed_script_names": list(session.allowed_script_names),
            "discord_channels": list(session.discord_channel_allowlist),
            "discord_channel_allowlist": list(session.discord_channel_allowlist),
            "teams_channel_allowlist": list(session.teams_channel_allowlist),
            "teams_chat_allowlist": list(session.teams_chat_allowlist),
            "teams_user_allowlist": list(session.teams_user_allowlist),
            "email_mailboxes": list(session.email_mailbox_allowlist),
            "email_folders": list(session.email_folder_allowlist),
            "communication_file_receive_enabled": session.communication_file_receive_enabled,
            "communication_file_send_enabled": session.communication_file_send_enabled,
            "communication_file_max_bytes": session.communication_file_max_bytes,
            "communication_file_allowed_mime_types": list(
                session.communication_file_allowed_mime_types
            ),
            "communication_file_allowed_extensions": list(
                session.communication_file_allowed_extensions
            ),
            "communication_file_blocked_extensions": list(
                session.communication_file_blocked_extensions
            ),
            "communication_file_require_approval_for_send": (
                session.communication_file_require_approval_for_send
            ),
            "metadata": dict(session.metadata),
            "labels": labels,
        }

    def _agent_record_from_vm(self, summary: VmSummary) -> AgentRecord:
        power_state = str(summary.power_state or summary.libvirt_state or "unknown").strip()
        status = "running" if power_state == "running" else "stopped"
        directory_path = summary.directory_paths[0] if len(summary.directory_paths) == 1 else "root"
        session_policy = None
        if len(summary.session_ids) == 1:
            session_policy = self._agent_session_policy_payload(
                self.runtime.session_repository.get_session(summary.session_ids[0])
            )
        return AgentRecord(
            agent_id=summary.vm_name,
            backend="libvirt_vm",
            target_name=summary.vm_name,
            session_id=summary.session_ids[0] if len(summary.session_ids) == 1 else None,
            directory_path=directory_path,
            status=status,
            updated_at=summary.last_state_update_at,
            host={
                "kind": "vm",
                "vm_name": summary.vm_name,
                "provider": summary.provider,
                "issue_number": summary.issue_number,
                "session_ids": list(summary.session_ids),
                "labels": dict(summary.labels),
                "directory_paths": list(summary.directory_paths),
                "power_state": summary.power_state,
                "libvirt_state": summary.libvirt_state,
                "provider_state": summary.provider_state,
                "provider_details": dict(summary.provider_details),
                "ip_address": summary.ip_address,
                "agent": {
                    "state": summary.agent_state,
                    "pid": summary.agent_pid,
                    "dispatch_id": summary.dispatch_id,
                    "repo_names": list(summary.agent_repo_names),
                    "preferred_repo_name": summary.agent_repo_names[0]
                    if len(summary.agent_repo_names) == 1
                    else None,
                },
                "session_policy": session_policy,
            },
            capabilities={
                "start": True,
                "shutdown": True,
                "resume": True,
                "enable_disable": True,
                "kill": True,
                "logs": True,
                "stop": False,
                "destroy": True,
            },
        )

    def _reconcile_container_execution_records(self, records: list[Any]) -> list[Any]:
        reconciled: list[Any] = []
        for record in records:
            status = self._container_agent_status(record.last_status)
            if status not in {"starting", "running", "unknown"}:
                reconciled.append(record)
                continue
            try:
                self._container_worker_backend(
                    directory_path=record.directory_path,
                    worker_url=self._container_worker_url_for_target(record=record),
                ).inspect(record.execution_id)
                record = (
                    self._container_worker_execution_repository().get(record.execution_id) or record
                )
            except Exception:
                pass
            reconciled.append(record)
        return reconciled

    def _container_execution_payload(self, record: Any) -> dict[str, Any]:
        status = self._container_agent_status(record.last_status)
        metadata = dict(record.metadata) if isinstance(record.metadata, dict) else {}
        image = metadata.get("image")
        return {
            "execution_id": record.execution_id,
            "session_id": record.session_id,
            "worker_job_id": record.worker_job_id,
            "worker_url": record.worker_url,
            "target_name": record.target_name,
            "status": status,
            "last_status": record.last_status,
            "created_at": record.created_at,
            "started_at": record.started_at,
            "finished_at": record.finished_at,
            "updated_at": record.updated_at,
            "image": image if isinstance(image, str) and image.strip() else None,
            "metadata": metadata,
        }

    def _agent_record_from_container_execution(
        self,
        record: Any,
        *,
        reconcile: bool,
    ) -> AgentRecord:
        status = self._container_agent_status(record.last_status)
        message: str | None = None
        if reconcile:
            try:
                self._container_worker_backend(
                    directory_path=record.directory_path,
                    worker_url=self._container_worker_url_for_target(record=record),
                ).inspect(record.execution_id)
            except Exception as exc:
                message = str(exc)
            else:
                record = self._container_worker_execution_repository().resolve(record.execution_id)
                status = self._container_agent_status(record.last_status)
        host = {
            "kind": "container_worker",
            "worker_job_id": record.worker_job_id,
            "worker_url": record.worker_url,
            "last_status": record.last_status,
            "metadata": dict(record.metadata),
            "session_policy": self._agent_session_policy_payload(
                self.runtime.session_repository.get_session(record.session_id)
            ),
        }
        if message:
            host["reconcile_error"] = message
        image = record.metadata.get("image") if isinstance(record.metadata, dict) else None
        if image:
            host["image"] = image
        return AgentRecord(
            agent_id=record.execution_id,
            backend="container_worker_vm",
            target_name=record.target_name,
            session_id=record.session_id,
            directory_path=self._agent_record_directory_path(record.directory_path),
            status=status,
            started_at=record.started_at,
            finished_at=record.finished_at,
            updated_at=record.updated_at,
            host=host,
            capabilities={
                "start": False,
                "shutdown": False,
                "resume": False,
                "enable_disable": False,
                "kill": False,
                "logs": True,
                "stop": True,
                "destroy": True,
            },
        )

    def _agent_record_from_container_session(
        self,
        session: SessionManifest,
        *,
        executions: list[Any] | None = None,
        reconcile: bool = False,
    ) -> AgentRecord:
        metadata = dict(session.metadata) if isinstance(session.metadata, dict) else {}
        target_name = self._container_session_target_name(session)
        labels = _normalize_session_labels(metadata.get("labels"))
        execution_records = sorted(
            list(executions or []),
            key=self._container_execution_sort_key,
            reverse=True,
        )
        if reconcile and execution_records:
            execution_records = sorted(
                self._reconcile_container_execution_records(execution_records),
                key=self._container_execution_sort_key,
                reverse=True,
            )
        latest_execution = execution_records[0] if execution_records else None
        execution_payloads = [
            self._container_execution_payload(record) for record in execution_records
        ]
        status = self._container_session_status(execution_records)
        active_count = sum(
            1
            for record in execution_records
            if self._agent_status_is_active(self._container_agent_status(record.last_status))
        )
        host: dict[str, Any] = {
            "kind": "container_worker_session",
            "metadata": metadata,
            "session_policy": self._agent_session_policy_payload(session),
            "executions": execution_payloads,
            "execution_count": len(execution_records),
            "active_execution_count": active_count,
        }
        if latest_execution is not None:
            latest_metadata = (
                dict(latest_execution.metadata)
                if isinstance(latest_execution.metadata, dict)
                else {}
            )
            host.update(
                {
                    "latest_execution_id": latest_execution.execution_id,
                    "worker_job_id": latest_execution.worker_job_id,
                    "worker_url": latest_execution.worker_url,
                    "last_status": latest_execution.last_status,
                }
            )
            image = latest_metadata.get("image")
            if isinstance(image, str) and image.strip():
                host["image"] = image
        if labels:
            host["labels"] = labels
        return AgentRecord(
            agent_id=session.session_id,
            backend="container_worker_vm",
            target_name=target_name,
            session_id=session.session_id,
            directory_path=self._agent_record_directory_path(session.directory_path),
            status=status,
            started_at=None,
            finished_at=None,
            updated_at=(
                latest_execution.updated_at if latest_execution is not None else session.created_at
            ),
            host=host,
            capabilities={
                "start": False,
                "shutdown": False,
                "resume": False,
                "enable_disable": False,
                "kill": False,
                "logs": bool(execution_records),
                "stop": active_count > 0,
                "destroy": True,
            },
        )

    def list_agents(
        self,
        *,
        directory_path: str | None = None,
        direct_only: bool = False,
        include_inactive: bool = True,
        reconcile_containers: bool = True,
        backend: str | None = None,
        all_directories: bool = False,
    ) -> list[AgentRecord]:
        normalized_directory = self._normalize_directory_path(directory_path)
        selected_backend = self._normalize_agent_backend_filter(backend)
        records: list[AgentRecord] = []
        if selected_backend in {"all", "libvirt_vm"}:
            try:
                vm_summaries = self.list_vms(
                    include_inactive=include_inactive,
                    directory_path=None if all_directories else normalized_directory,
                    direct_only=False if all_directories else direct_only,
                )
            except Exception:
                if selected_backend == "libvirt_vm":
                    raise
                vm_summaries = []
            for summary in vm_summaries:
                record = self._agent_record_from_vm(summary)
                if include_inactive or self._agent_status_is_active(record.status):
                    records.append(record)

        if selected_backend == "libvirt_vm":
            return records

        container_records_by_session: dict[str, list[Any]] = {}
        for container_record in self._container_worker_execution_repository().read_all().values():
            session_id = str(container_record.session_id or "").strip()
            if not session_id:
                continue
            if not all_directories and not self._agent_record_matches_directory(
                container_record.directory_path,
                normalized_directory,
                direct_only=direct_only,
            ):
                continue
            container_records_by_session.setdefault(session_id, []).append(container_record)
        for session in sorted(
            self.runtime.session_repository.list_sessions(),
            key=lambda item: (item.created_at, item.session_id),
            reverse=True,
        ):
            if not self._is_container_session(session):
                continue
            if not all_directories and not self._agent_record_matches_directory(
                session.directory_path,
                normalized_directory,
                direct_only=direct_only,
            ):
                continue
            record = self._agent_record_from_container_session(
                session,
                executions=container_records_by_session.get(session.session_id, []),
                reconcile=reconcile_containers,
            )
            if include_inactive or self._agent_status_is_active(record.status):
                records.append(record)
        return records

    def create_admin_agent(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        selected_backend = self._normalize_agent_backend(
            self._agent_optional_string(payload, "backend")
            or self._agent_optional_string(payload, "execution_backend")
        )
        if selected_backend == "libvirt_vm":
            vm_name = self._agent_optional_string(payload, "vm_name")
            if not vm_name:
                raise ValueError("vm_name is required for libvirt_vm agents")
            codex_skills = self.validate_managed_codex_skills(
                self._agent_string_list(payload, "codex_skills")
            )
            result = self.create_vm(
                provider=self._agent_optional_string(payload, "provider"),
                vm_name=vm_name,
                session_id=self._agent_optional_string(payload, "session_id"),
                labels=_normalize_session_labels(payload.get("labels")),
                model=self._agent_optional_string(payload, "model"),
                vm_user=self._agent_optional_string(payload, "vm_user") or "agent",
                ttl_hours=self._agent_int_or_none(payload, "ttl_hours"),
                allowed_tools=self._agent_string_list(payload, "allowed_tools"),
                allowed_script_names=self._agent_string_list(payload, "allowed_script_names"),
                discord_channels=self._agent_string_list(payload, "discord_channels"),
                teams_channel_allowlist=self._agent_string_list(
                    payload, "teams_channel_allowlist"
                ),
                **(
                    {
                        "teams_chat_allowlist": self._agent_string_list(
                            payload, "teams_chat_allowlist"
                        )
                    }
                    if "teams_chat_allowlist" in payload
                    else {}
                ),
                teams_user_allowlist=self._agent_string_list(payload, "teams_user_allowlist"),
                email_mailboxes=self._agent_string_list(payload, "email_mailboxes"),
                email_folders=self._agent_string_list(payload, "email_folders"),
                communication_file_receive_enabled=(
                    _normalize_session_policy_bool(
                        payload.get("communication_file_receive_enabled"),
                        field_name="communication_file_receive_enabled",
                    )
                    if "communication_file_receive_enabled" in payload
                    else None
                ),
                communication_file_send_enabled=(
                    _normalize_session_policy_bool(
                        payload.get("communication_file_send_enabled"),
                        field_name="communication_file_send_enabled",
                    )
                    if "communication_file_send_enabled" in payload
                    else None
                ),
                communication_file_max_bytes=(
                    _normalize_session_policy_non_negative_int_or_none(
                        payload.get("communication_file_max_bytes"),
                        field_name="communication_file_max_bytes",
                    )
                    if "communication_file_max_bytes" in payload
                    else None
                ),
                communication_file_require_approval_for_send=(
                    _normalize_session_policy_bool(
                        payload.get("communication_file_require_approval_for_send"),
                        field_name="communication_file_require_approval_for_send",
                    )
                    if "communication_file_require_approval_for_send" in payload
                    else None
                ),
                dotnet=bool(payload.get("dotnet", False)),
                nuget=bool(payload.get("nuget", False)),
                mssql=bool(payload.get("mssql", False)),
                mssql_init_root=self._agent_optional_string(payload, "mssql_init_root"),
                playwright=bool(payload.get("playwright", False)),
                codex_skills=codex_skills,
                directory_path=self._agent_optional_string(payload, "directory_path") or "root",
            )
            session_id = self._agent_optional_string(payload, "session_id")
            return {
                "success": result.success,
                "agent_id": result.vm_name or vm_name,
                "backend": "libvirt_vm",
                "target_name": result.vm_name or vm_name,
                "action": result.action,
                "message": result.message,
                "status": "created" if result.success else "failed",
                "session_id": session_id,
                "provider": result.provider,
                "provider_operation_id": result.provider_operation_id,
            }

        session_id = self._agent_optional_string(payload, "session_id")
        if not session_id:
            raise ValueError("session_id is required for container_worker_vm agents")
        if bool(payload.get("mssql", False)) or self._agent_optional_string(
            payload, "mssql_init_root"
        ):
            raise ValueError(
                "create_admin_agent with container_worker_vm does not support MSSQL yet"
            )
        codex_skill_files = self.resolve_container_codex_skill_files(
            self._agent_string_list(payload, "codex_skills")
        )
        target_name = self._agent_optional_string(payload, "target_name") or session_id
        directory_path = self._normalize_directory_path(
            self._agent_optional_string(payload, "directory_path")
        )
        session_repository = self.runtime.session_repository
        if session_repository.get_session(session_id) is not None:
            raise ValueError(f"Session already exists: {session_id}")

        policy = self.runtime.policy_config.resolve_for_directory(directory_path)
        labels = _normalize_session_labels(payload.get("labels"))
        metadata: dict[str, Any] = {
            "execution_backend": "container_worker_vm",
            "target_name": target_name,
        }
        if labels:
            metadata["labels"] = labels
        model = self._agent_optional_string(payload, "model")
        backend = self._container_worker_backend(directory_path=directory_path)
        worker_url = self._container_worker_backend_url(backend)
        if worker_url:
            metadata["worker_url"] = worker_url
        manifest = _build_issue_session_manifest(
            policy=policy,
            session_id=session_id,
            vm_id=target_name,
            issue_number=None,
            directory_path=directory_path,
            ttl_hours=self._agent_int_or_none(payload, "ttl_hours"),
            extra_allowed_tools=self._agent_string_list(payload, "allowed_tools"),
            allowed_script_names=self._agent_string_list(payload, "allowed_script_names"),
            discord_channels=self._agent_string_list(payload, "discord_channels") or [],
            teams_channel_allowlist=self._agent_string_list(payload, "teams_channel_allowlist")
            or [],
            teams_chat_allowlist=self._agent_string_list(payload, "teams_chat_allowlist") or [],
            teams_user_allowlist=self._agent_string_list(payload, "teams_user_allowlist") or [],
            email_mailboxes=self._agent_string_list(payload, "email_mailboxes") or [],
            email_folders=self._agent_string_list(payload, "email_folders") or [],
            communication_file_receive_enabled=(
                _normalize_session_policy_bool(
                    payload.get("communication_file_receive_enabled"),
                    field_name="communication_file_receive_enabled",
                )
                if "communication_file_receive_enabled" in payload
                else None
            ),
            communication_file_send_enabled=(
                _normalize_session_policy_bool(
                    payload.get("communication_file_send_enabled"),
                    field_name="communication_file_send_enabled",
                )
                if "communication_file_send_enabled" in payload
                else None
            ),
            communication_file_max_bytes=(
                _normalize_session_policy_non_negative_int_or_none(
                    payload.get("communication_file_max_bytes"),
                    field_name="communication_file_max_bytes",
                )
                if "communication_file_max_bytes" in payload
                else None
            ),
            communication_file_require_approval_for_send=(
                _normalize_session_policy_bool(
                    payload.get("communication_file_require_approval_for_send"),
                    field_name="communication_file_require_approval_for_send",
                )
                if "communication_file_require_approval_for_send" in payload
                else None
            ),
        ).model_copy(
            update={
                "vm_id": None,
                "vm_provider": "container_worker_vm",
                "vm_provider_id": target_name,
                "model": model,
                "metadata": metadata,
            }
        )
        session_record = backend.prepare_session(
            session_id=session_id,
            metadata={
                "execution_backend": "container_worker_vm",
                "directory_path": directory_path,
                "target_name": target_name,
                "labels": labels,
                **({"worker_url": worker_url} if worker_url else {}),
            },
        )
        from agent_sandbox.effects.providers.agent_session import (
            build_container_session_files,
        )

        backend.write_files(
            session_id=session_id,
            files=[
                *build_container_session_files(
                    config=backend.config,
                    session_id=session_id,
                    model=model,
                    dotnet=bool(payload.get("dotnet", False)),
                    nuget=bool(payload.get("nuget", False)),
                    playwright=bool(payload.get("playwright", False)),
                ),
                *codex_skill_files,
            ],
        )
        session_repository.upsert_session(manifest)
        return {
            "success": True,
            "agent_id": session_id,
            "backend": "container_worker_vm",
            "target_name": target_name,
            "action": "create",
            "message": f"Prepared container worker session {session_id}",
            "status": "prepared",
            "session_id": session_id,
            "agent_home": session_record.agent_home_container_path,
            "repo_root": session_record.repo_root_container_path,
        }

    def _resolve_container_agent_session(
        self,
        agent_id: str,
        *,
        require_session: bool,
    ) -> tuple[SessionManifest | None, Any | None]:
        direct_session = self.runtime.session_repository.get_session(agent_id)
        if direct_session is not None and self._is_container_session(direct_session):
            return direct_session, None

        repository = self._container_worker_execution_repository()
        try:
            record = repository.resolve(agent_id)
        except ValueError as exc:
            if str(exc).startswith("Ambiguous container worker session target"):
                raise
            record = None
        if record is not None:
            session = self.runtime.session_repository.get_session(record.session_id)
            if session is None and require_session:
                raise ValueError(f"Unknown session: {record.session_id}")
            return session, record

        matches = [
            session
            for session in self.runtime.session_repository.list_sessions()
            if self._is_container_session(session)
            and self._container_session_target_name(session) == agent_id
        ]
        if len(matches) == 1:
            return matches[0], None
        if len(matches) > 1:
            session_ids = ", ".join(sorted(session.session_id for session in matches))
            raise ValueError(
                f"Container worker target {agent_id!r} is associated with multiple sessions: {session_ids}"
            )
        if require_session:
            raise ValueError(f"Unknown container worker agent target: {agent_id}")
        return None, None

    def _container_execution_records_for_session(self, session_id: str) -> list[Any]:
        return sorted(
            [
                record
                for record in self._container_worker_execution_repository().read_all().values()
                if record.session_id == session_id
            ],
            key=self._container_execution_sort_key,
        )

    def _session_associated_agent_description(
        self,
        session_id: str,
        session: SessionManifest,
    ) -> str | None:
        if self._is_container_session(session):
            target_name = self._container_session_target_name(session)
            return f"container worker agent {target_name!r}"
        vm_name = str(session.vm_id or "").strip()
        if vm_name:
            return f"VM agent {vm_name!r}"
        return None

    def _delete_detached_worker_session_state(self, session_id: str) -> tuple[bool, int]:
        execution_records = self._container_execution_records_for_session(session_id)
        if not execution_records:
            return False, 0
        latest_record = sorted(
            execution_records,
            key=self._container_execution_sort_key,
            reverse=True,
        )[0]
        worker_backend = self._container_worker_backend(
            directory_path=getattr(latest_record, "directory_path", None),
            worker_url=self._container_worker_url_for_target(
                record=latest_record,
                records=execution_records,
            ),
        )
        worker_session_deleted = self._worker_delete_session_removed(
            worker_backend.delete_session(session_id)
        )
        container_repository = self._container_worker_execution_repository()
        removed_mappings = 0
        for execution_id, candidate in list(container_repository.read_all().items()):
            if candidate.session_id == session_id:
                container_repository.delete(execution_id)
                removed_mappings += 1
        return worker_session_deleted, removed_mappings

    @staticmethod
    def _worker_delete_session_removed(result: Any) -> bool:
        if isinstance(result, Mapping) and "deleted" in result:
            return bool(result["deleted"])
        return bool(result)

    def _resolve_vm_agent_session(self, agent_id: str) -> SessionManifest:
        direct_session = self.runtime.session_repository.get_session(agent_id)
        if direct_session is not None:
            return direct_session
        matches = self.runtime.session_repository.list_sessions_for_vm(agent_id)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            session_ids = ", ".join(sorted(session.session_id for session in matches))
            raise ValueError(f"VM {agent_id!r} is associated with multiple sessions: {session_ids}")
        raise ValueError(f"No session is linked to VM agent target: {agent_id}")

    def _record_installed_codex_skills(self, session_id: str, skill_names: list[str]) -> None:
        session = self.runtime.session_repository.get_session(session_id)
        if session is None:
            return
        metadata = dict(session.metadata)
        existing = metadata.get("codex_skills")
        merged: list[str] = []
        if isinstance(existing, list):
            for item in existing:
                if isinstance(item, str) and item.strip():
                    merged.append(item)
        merged.extend(skill_names)
        metadata["codex_skills"] = normalize_skill_names(merged)
        metadata["codex_skills_updated_at"] = datetime.now(UTC).isoformat()
        self.runtime.session_repository.upsert_session(
            session.model_copy(update={"metadata": metadata})
        )

    @staticmethod
    def _skill_install_files_for_codex_home(
        entries: list[SkillLibraryEntry], *, codex_home: str
    ) -> list[dict[str, Any]]:
        normalized_home = str(codex_home or "").strip().rstrip("/")
        if (
            not normalized_home
            or not normalized_home.startswith("/")
            or "\n" in normalized_home
            or "\x00" in normalized_home
        ):
            raise RuntimeError(
                f"Guest CODEX_HOME resolved to an invalid path: {codex_home!r}"
            )
        return [
            {
                "destination_path": f"{normalized_home}/skills/{entry.name}/SKILL.md",
                "mode": "0644",
                "content": entry.content,
                "overwrite": True,
            }
            for entry in entries
        ]

    def _install_codex_skills_into_vm_agent(
        self,
        agent_id: str,
        *,
        skill_names: list[str],
        vm_user: str,
    ) -> dict[str, Any]:
        session = self._resolve_vm_agent_session(agent_id)
        target = session.session_id
        target_name = str(session.vm_id or agent_id).strip()
        if not target_name:
            raise ValueError(f"Session {session.session_id!r} is not linked to a VM")
        entries = [self.skill_library.get_skill(name) for name in skill_names]
        codex_home_result = self.vm_exec(
            target,
            command='printf "%s" "${CODEX_HOME:-$HOME/.config/agent-sandbox/codex}"',
            vm_user=vm_user,
            timeout_seconds=30,
            check=True,
        )
        if not bool(codex_home_result.get("success")):
            message = str(
                codex_home_result.get("message") or "Failed to resolve guest CODEX_HOME"
            )
            raise RuntimeError(message)
        codex_home = str(codex_home_result.get("stdout") or "").strip()
        files = self._skill_install_files_for_codex_home(entries, codex_home=codex_home)
        result = self.vm_copy_files(target, files=files, vm_user=vm_user)
        if not bool(result.get("success")):
            message = str(result.get("message") or "Failed to copy Codex skills")
            raise RuntimeError(message)
        copied_paths = list(result.get("copied_paths") or [])
        self._record_installed_codex_skills(session.session_id, skill_names)
        return {
            "success": True,
            "agent_id": agent_id,
            "backend": "libvirt_vm",
            "target_name": target_name,
            "action": "install-skills",
            "message": (
                f"Installed {len(skill_names)} Codex skill"
                f"{'' if len(skill_names) == 1 else 's'} into session "
                f"{session.session_id}: "
                f"{', '.join(skill_names)}"
            ),
            "status": "updated",
            "session_id": session.session_id,
            "copied_count": len(copied_paths),
            "copied_paths": copied_paths,
            "codex_skills": list(skill_names),
        }

    def _install_codex_skills_into_container_agent(
        self,
        agent_id: str,
        *,
        skill_names: list[str],
    ) -> dict[str, Any]:
        session, record = self._resolve_container_agent_session(
            agent_id, require_session=True
        )
        if session is None:
            raise ValueError(f"Unknown container worker agent target: {agent_id}")
        execution_records = self._container_execution_records_for_session(session.session_id)
        worker_backend = self._container_worker_backend(
            directory_path=session.directory_path,
            worker_url=self._container_worker_url_for_target(
                session=session,
                record=record,
                records=execution_records,
            ),
        )
        files = self.resolve_container_codex_skill_files(skill_names)
        result = worker_backend.write_files(session_id=session.session_id, files=files)
        copied_paths = list(getattr(result, "copied_paths", []) or [])
        self._record_installed_codex_skills(session.session_id, skill_names)
        return {
            "success": True,
            "agent_id": agent_id,
            "backend": "container_worker_vm",
            "target_name": self._container_session_target_name(session),
            "action": "install-skills",
            "message": (
                f"Installed {len(skill_names)} Codex skill"
                f"{'' if len(skill_names) == 1 else 's'} into session "
                f"{session.session_id}: "
                f"{', '.join(skill_names)}"
            ),
            "status": "updated",
            "session_id": session.session_id,
            "copied_count": int(
                getattr(result, "copied_count", len(copied_paths)) or 0
            ),
            "copied_paths": copied_paths,
            "codex_skills": list(skill_names),
        }

    def install_admin_agent_skills(
        self,
        agent_id: str,
        *,
        backend: str | None = None,
        skill_names: list[str] | tuple[str, ...] | None = None,
        vm_user: str = "agent",
    ) -> dict[str, Any]:
        names = self.validate_managed_codex_skills(skill_names)
        if not names:
            raise ValueError("codex_skills must contain at least one skill")
        selected_backend = self._normalize_agent_backend(backend)
        if selected_backend == "libvirt_vm":
            return self._install_codex_skills_into_vm_agent(
                agent_id,
                skill_names=names,
                vm_user=vm_user,
            )
        return self._install_codex_skills_into_container_agent(
            agent_id, skill_names=names
        )

    def update_admin_agent(
        self,
        agent_id: str,
        *,
        backend: str | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        selected_backend = self._normalize_agent_backend(backend)
        body = dict(payload or {})
        allowed_fields = {
            "model",
            "ttl_hours",
            "allowed_tools",
            "allowed_script_names",
            "discord_channels",
            "discord_channel_allowlist",
            "teams_channels",
            "teams_channel_allowlist",
            "teams_chat_allowlist",
            "teams_user_allowlist",
            "metadata",
            "email_mailboxes",
            "email_folders",
            "labels",
            *COMMUNICATION_FILE_SESSION_POLICY_FIELDS,
        }
        unsupported = sorted(set(body) - allowed_fields)
        if unsupported:
            raise ValueError("Unsupported agent policy fields: " + ", ".join(unsupported))
        session: SessionManifest | None
        if selected_backend == "container_worker_vm":
            session, record = self._resolve_container_agent_session(agent_id, require_session=True)
            del record
            target_name = self._container_session_target_name(session)
        else:
            session = self._resolve_vm_agent_session(agent_id)
            target_name = str(session.vm_id or agent_id)

        update: dict[str, Any] = {}
        if "model" in body:
            update["model"] = self._agent_optional_string(body, "model")
        if "ttl_hours" in body:
            ttl_hours = self._agent_int_or_none(body, "ttl_hours")
            if ttl_hours is None:
                raise ValueError("ttl_hours must be an integer when supplied")
            update["expires_at"] = _utc_now() + timedelta(hours=ttl_hours)
        if "allowed_tools" in body:
            update["allowed_tools"] = _normalize_allowed_tools(
                self._agent_string_list(body, "allowed_tools") or []
            )
        session_policy_fields = {
            key: body[key]
            for key in (
                "allowed_script_names",
                "discord_channels",
                "discord_channel_allowlist",
                "teams_channels",
                "teams_channel_allowlist",
                "teams_chat_allowlist",
                "teams_user_allowlist",
                "metadata",
                *COMMUNICATION_FILE_SESSION_POLICY_FIELDS,
            )
            if key in body
        }
        if session_policy_fields:
            update.update(_session_policy_update_from_payload(session, session_policy_fields))
        if "email_mailboxes" in body:
            update["email_mailbox_allowlist"] = (
                self._agent_string_list(body, "email_mailboxes") or []
            )
        if "email_folders" in body:
            update["email_folder_allowlist"] = self._agent_string_list(body, "email_folders") or []
        if "labels" in body:
            metadata = dict(update["metadata"] if "metadata" in update else session.metadata)
            labels = _normalize_session_labels(body.get("labels"))
            if labels:
                metadata["labels"] = labels
            else:
                metadata.pop("labels", None)
            update["metadata"] = metadata

        updated = session.model_copy(update=update)
        self.runtime.session_repository.upsert_session(updated)
        return {
            "success": True,
            "agent_id": agent_id,
            "backend": selected_backend,
            "target_name": target_name,
            "action": "edit",
            "message": f"Updated policy for session {updated.session_id}",
            "status": "updated",
            "session_id": updated.session_id,
        }

    def delete_admin_agent(
        self,
        agent_id: str,
        *,
        backend: str | None = None,
    ) -> dict[str, Any]:
        selected_backend = self._normalize_agent_backend(backend)
        if selected_backend == "libvirt_vm":
            direct_session = self.runtime.session_repository.get_session(agent_id)
            if direct_session is not None:
                vm_name = str(direct_session.vm_id or "").strip()
                if not vm_name:
                    raise ValueError(f"Session {agent_id!r} is not linked to a VM")
            else:
                vm_name = agent_id
                matches = self.runtime.session_repository.list_sessions_for_vm(vm_name)
                if len(matches) > 1:
                    session_ids = ", ".join(sorted(session.session_id for session in matches))
                    raise ValueError(
                        f"Refusing to delete VM {vm_name!r}: multiple linked sessions "
                        f"({session_ids}). Delete is only allowed when the VM-to-session link is unambiguous."
                    )
            matches = self.runtime.session_repository.list_sessions_for_vm(vm_name)
            if len(matches) > 1:
                session_ids = ", ".join(sorted(session.session_id for session in matches))
                raise ValueError(
                    f"Refusing to delete VM {vm_name!r}: multiple linked sessions "
                    f"({session_ids}). Delete is only allowed when the VM-to-session link is unambiguous."
                )
            result = self.destroy_vm(vm_name)
            return {
                "success": result.success,
                "agent_id": vm_name,
                "backend": "libvirt_vm",
                "target_name": vm_name,
                "action": "delete",
                "message": result.message,
                "status": "deleted" if result.success else "failed",
                "session_id": matches[0].session_id if len(matches) == 1 else None,
            }

        session, record = self._resolve_container_agent_session(agent_id, require_session=False)
        session_id = (
            str(getattr(session, "session_id", "") or "").strip()
            if session is not None
            else str(getattr(record, "session_id", "") or "").strip()
        )
        if not session_id:
            raise ValueError(f"Unknown container worker agent target: {agent_id}")
        target_name = (
            self._container_session_target_name(session)
            if session is not None
            else str(getattr(record, "target_name", "") or agent_id)
        )
        execution_records = self._container_execution_records_for_session(session_id)
        worker_backend = self._container_worker_backend(
            directory_path=(
                session.directory_path
                if session is not None
                else getattr(record, "directory_path", None)
            ),
            worker_url=self._container_worker_url_for_target(
                session=session,
                record=record,
                records=execution_records,
            ),
        )
        if record is not None:
            worker_backend.destroy(record.execution_id)
        worker_session_deleted = worker_backend.delete_session(session_id)
        worker_session_removed = self._worker_delete_session_removed(worker_session_deleted)
        container_repository = self._container_worker_execution_repository()
        removed_mappings = 0
        for execution_id, candidate in list(container_repository.read_all().items()):
            if candidate.session_id == session_id:
                container_repository.delete(execution_id)
                removed_mappings += 1
        removed_session = self.runtime.session_repository.delete_session(session_id)
        removed_approvals = _prune_approvals_for_sessions(self.runtime, session_ids={session_id})
        removed_pr_map_entries, removed_streams = _prune_pr_map(
            self.config.pr_map_path,
            session_ids={session_id},
        )
        removed_cursors, removed_feedback = _prune_pr_feedback_store(
            self.config.runtime_dir / "pr-feedback-store.json",
            streams=removed_streams,
        )
        return {
            "success": True,
            "agent_id": agent_id,
            "backend": "container_worker_vm",
            "target_name": target_name,
            "action": "delete",
            "message": (
                f"Deleted container worker session {session_id}; "
                f"removed_mappings={removed_mappings} "
                f"removed_session={int(removed_session)} "
                f"removed_approvals={removed_approvals} "
                f"removed_pr_map_entries={removed_pr_map_entries} "
                f"removed_feedback_cursors={removed_cursors} "
                f"removed_feedback_items={removed_feedback} "
                f"worker_session_deleted={int(worker_session_removed)}"
            ),
            "status": "deleted",
            "session_id": session_id,
        }

    def get_admin_agent_log_snapshot(
        self,
        agent_id: str,
        *,
        backend: str | None = None,
        lines: int = 50,
        repo_name: str | None = None,
        vm_user: str = "agent",
    ) -> str:
        selected_backend = str(backend or "libvirt_vm").strip()
        if selected_backend in {"libvirt", "libvirt_vm"}:
            return self.get_agent_log_snapshot(
                agent_id,
                lines=lines,
                repo_name=repo_name,
                vm_user=vm_user,
            )
        if selected_backend == "container_worker_vm":
            session, record = self._resolve_container_agent_session(agent_id, require_session=False)
            session_id = (
                str(session.session_id).strip()
                if session is not None
                else str(getattr(record, "session_id", "") or "").strip()
            )
            execution_records = (
                self._container_execution_records_for_session(session_id) if session_id else []
            )
            worker_backend = self._container_worker_backend(
                directory_path=(
                    session.directory_path
                    if session is not None
                    else getattr(record, "directory_path", None)
                ),
                worker_url=self._container_worker_url_for_target(
                    session=session,
                    record=record,
                    records=execution_records,
                ),
            )
            if session is None or record is not None:
                try:
                    worker_backend.inspect(agent_id)
                except Exception:
                    pass
                return worker_backend.get_log_snapshot(agent_id, lines=lines)
            if not execution_records:
                return f"No container executions have run for session {session_id}.\n"
            per_execution_lines = lines
            if lines > 0:
                per_execution_lines = max(1, lines // max(1, len(execution_records)))
            chunks: list[str] = []
            for execution in reversed(execution_records):
                try:
                    transcript = worker_backend.get_log_snapshot(
                        execution.execution_id,
                        lines=lines,
                        mode="session",
                    ).rstrip()
                except Exception:
                    transcript = ""
                if transcript:
                    chunks.append(
                        f"=== session={session_id} log=codex_transcript ===\n{transcript}"
                    )
                    break
            for execution in execution_records:
                header_parts = [
                    f"execution={execution.execution_id}",
                    f"status={self._container_agent_status(execution.last_status)}",
                    f"worker_job={execution.worker_job_id}",
                    "log=raw",
                ]
                if execution.started_at is not None:
                    header_parts.append(f"started={execution.started_at.isoformat()}")
                if execution.finished_at is not None:
                    header_parts.append(f"finished={execution.finished_at.isoformat()}")
                try:
                    text = worker_backend.get_log_snapshot(
                        execution.execution_id,
                        lines=per_execution_lines,
                        mode="raw",
                    ).rstrip()
                except Exception as exc:
                    text = f"[log unavailable: {exc}]"
                chunks.append("=== " + " ".join(header_parts) + " ===\n" + text)
            return "\n\n".join(chunks).rstrip() + "\n"
        raise ValueError(f"Unknown agent backend: {selected_backend}")

    def get_admin_agent_log_page(
        self,
        agent_id: str,
        *,
        backend: str | None = None,
        limit: int = 200,
        offset: int = 0,
        query: str | None = None,
        repo_name: str | None = None,
        vm_user: str = "agent",
    ) -> dict[str, object]:
        normalized_limit = max(1, min(int(limit), 5000))
        normalized_offset = max(0, int(offset))
        normalized_query = str(query or "").strip()
        selected_backend = str(backend or "libvirt_vm").strip()
        if selected_backend in {"libvirt", "libvirt_vm"}:
            window_lines = normalized_limit + normalized_offset
            if normalized_query:
                window_lines = max(window_lines, 5000)
            text = self.get_agent_log_snapshot(
                agent_id,
                lines=window_lines,
                repo_name=repo_name,
                vm_user=vm_user,
            )
            page = _log_page_from_end(
                text,
                limit=normalized_limit,
                offset=normalized_offset,
                query=normalized_query or None,
            )
            if not normalized_query and page["returned_lines"] >= normalized_limit:
                page["has_more_before"] = True
                page["total_lines"] = None
                page["start_line"] = None
                page["end_line"] = None
            return page
        if selected_backend == "container_worker_vm":
            session, record = self._resolve_container_agent_session(agent_id, require_session=False)
            session_id = (
                str(session.session_id).strip()
                if session is not None
                else str(getattr(record, "session_id", "") or "").strip()
            )
            execution_records = (
                self._container_execution_records_for_session(session_id) if session_id else []
            )
            worker_backend = self._container_worker_backend(
                directory_path=(
                    session.directory_path
                    if session is not None
                    else getattr(record, "directory_path", None)
                ),
                worker_url=self._container_worker_url_for_target(
                    session=session,
                    record=record,
                    records=execution_records,
                ),
            )
            target = agent_id
            if session is not None and record is None:
                if not execution_records:
                    return _log_page_from_end(
                        f"No container executions have run for session {session_id}.\n",
                        limit=normalized_limit,
                        offset=normalized_offset,
                        query=normalized_query or None,
                    )
                target = execution_records[-1].execution_id
            page_reader = getattr(worker_backend, "get_log_page", None)
            if callable(page_reader):
                return page_reader(
                    target,
                    limit=normalized_limit,
                    offset=normalized_offset,
                    query=normalized_query or None,
                )
            text = worker_backend.get_log_snapshot(target, lines=normalized_limit + normalized_offset)
            return _log_page_from_end(
                text,
                limit=normalized_limit,
                offset=normalized_offset,
                query=normalized_query or None,
            )
        raise ValueError(f"Unknown agent backend: {selected_backend}")

    def run_admin_agent_action(
        self,
        agent_id: str,
        action: str,
        *,
        backend: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        selected_backend = self._normalize_agent_backend(backend)
        body = payload or {}
        if selected_backend == "libvirt_vm":
            if action == "start":
                result = self.start_vm(agent_id)
            elif action == "shutdown":
                result = self.shutdown_vm(agent_id)
            elif action == "enable-agent":
                result = self.enable_agent(agent_id)
            elif action == "disable-agent":
                result = self.disable_agent(agent_id)
            elif action == "kill-agent":
                pid = body.get("pid")
                if pid in {None, ""}:
                    raise ValueError("kill-agent requires a running agent pid")
                result = self.kill_agent(agent_id, int(pid))
            elif action == "resume-agent":
                prompt = str(body.get("prompt") or "").strip()
                if not prompt:
                    raise ValueError("resume-agent requires a non-empty prompt")
                result = self.resume_agent(
                    agent_id,
                    prompt,
                    model=str(body.get("model") or "").strip() or None,
                    resume_mode=str(body.get("resume_mode") or "same_context").strip()
                    or "same_context",
                )
            elif action == "install-skills":
                return self.install_admin_agent_skills(
                    agent_id,
                    backend="libvirt_vm",
                    skill_names=self._agent_string_list(body, "codex_skills"),
                    vm_user=self._agent_optional_string(body, "vm_user") or "agent",
                )
            elif action == "destroy":
                result = self.destroy_vm(agent_id)
            else:
                raise LookupError(f"Unknown VM agent action: {action}")
            return {
                "success": result.success,
                "agent_id": result.vm_name,
                "backend": "libvirt_vm",
                "target_name": result.vm_name,
                "action": result.action,
                "message": result.message,
                "provider": result.provider,
                "provider_operation_id": result.provider_operation_id,
                "thread_id": result.thread_id,
                "target_dir": result.target_dir,
                "guest_repo_name": result.guest_repo_name,
                "remote_url": result.remote_url,
            }
        if selected_backend == "container_worker_vm":
            if action == "install-skills":
                return self.install_admin_agent_skills(
                    agent_id,
                    backend="container_worker_vm",
                    skill_names=self._agent_string_list(body, "codex_skills"),
                    vm_user=self._agent_optional_string(body, "vm_user") or "agent",
                )
            session, record = self._resolve_container_agent_session(agent_id, require_session=False)
            is_session_target = session is not None and record is None
            session_id = (
                str(session.session_id).strip()
                if session is not None
                else str(getattr(record, "session_id", "") or "").strip()
            )
            execution_records = (
                self._container_execution_records_for_session(session_id) if session_id else []
            )
            worker_backend = self._container_worker_backend(
                directory_path=(
                    session.directory_path
                    if session is not None
                    else getattr(record, "directory_path", None)
                ),
                worker_url=self._container_worker_url_for_target(
                    session=session,
                    record=record,
                    records=execution_records,
                ),
            )
            if action == "stop":
                if session is not None and record is None:
                    active_records = [
                        candidate
                        for candidate in execution_records
                        if self._agent_status_is_active(
                            self._container_agent_status(candidate.last_status)
                        )
                    ]
                    if not active_records:
                        raise ValueError(
                            f"No active container executions for session {session.session_id}"
                        )
                    statuses = [
                        worker_backend.stop(candidate.execution_id) for candidate in active_records
                    ]
                    status = statuses[-1]
                    message = (
                        f"Stopped {len(statuses)} active container execution"
                        f"{'' if len(statuses) == 1 else 's'} for session {session.session_id}"
                    )
                else:
                    status = worker_backend.stop(agent_id)
                    message = status.message or "stop completed"
            elif action == "destroy":
                if session is not None and record is None:
                    if not execution_records:
                        raise ValueError(
                            f"No container executions for session {session.session_id}"
                        )
                    statuses = [
                        worker_backend.destroy(candidate.execution_id)
                        for candidate in execution_records
                    ]
                    status = statuses[-1]
                    message = (
                        f"Destroyed {len(statuses)} container execution"
                        f"{'' if len(statuses) == 1 else 's'} for session {session.session_id}"
                    )
                else:
                    status = worker_backend.destroy(agent_id)
                    message = status.message or "destroy completed"
            else:
                raise LookupError(f"Unknown container worker agent action: {action}")
            return {
                "success": True,
                "agent_id": session.session_id if is_session_target else status.execution_id,
                "backend": "container_worker_vm",
                "target_name": (
                    self._container_session_target_name(session)
                    if is_session_target
                    else status.target_name
                ),
                "action": action,
                "message": message,
                "status": status.status.value,
                "session_id": session.session_id if session is not None else status.session_id,
            }
        raise ValueError(f"Unknown agent backend: {selected_backend}")

    def list_background_workers(self) -> list[WorkerSummary]:
        return self.operations_admin.list_background_workers()

    def get_background_worker_log_snapshot(self, worker_id: str, *, lines: int = 200) -> str:
        return self.operations_admin.get_background_worker_log_snapshot(worker_id, lines=lines)

    def start_background_worker(self, worker_id: str) -> WorkerActionResult:
        return self.operations_admin.start_background_worker(worker_id)

    def stop_background_worker(self, worker_id: str) -> WorkerActionResult:
        return self.operations_admin.stop_background_worker(worker_id)

    def get_agent_log_snapshot(
        self,
        target: str,
        *,
        lines: int = 50,
        repo_name: str | None = None,
        vm_user: str = "agent",
    ) -> str:
        return self._execution_backend().get_log_snapshot(
            target,
            lines=lines,
            repo_name=repo_name,
            vm_user=vm_user,
        )

    def start_vm(self, target: str) -> VmActionResult:
        return self._execution_backend().start(target)

    def shutdown_vm(self, target: str, *, timeout_seconds: int = 60) -> VmActionResult:
        return self._execution_backend().stop(target, timeout_seconds=timeout_seconds)

    def _run_script(
        self, script_name: str, args: list[str], *, env: dict[str, str] | None = None
    ) -> VmActionResult:
        script_path = Path(__file__).resolve().parents[3] / "scripts" / script_name
        completed = _run_subprocess(
            [str(script_path), *args], capture_output=True, text=True, env=env
        )
        vm_name = ""
        if "--vm-name" in args:
            try:
                vm_name = args[args.index("--vm-name") + 1]
            except IndexError:
                vm_name = ""
        elif len(args) > 1:
            issue_target = args[1]
            issue_number = _extract_issue_number(issue_target) or issue_target
            vm_name = _resolve_vm_name(issue_number) if issue_number else ""
        message = (completed.stdout or completed.stderr or "").strip()
        return VmActionResult(
            success=completed.returncode == 0,
            vm_name=vm_name,
            action=script_name.removesuffix(".sh"),
            message=message or f"{script_name} exited with code {completed.returncode}",
        )

    def enable_agent(self, target: str, *, vm_user: str = "agent") -> VmActionResult:
        return self.vm_agent_admin.enable_agent(target, vm_user=vm_user)

    def disable_agent(self, target: str, *, vm_user: str = "agent") -> VmActionResult:
        return self.vm_agent_admin.disable_agent(target, vm_user=vm_user)

    def kill_agent(self, target: str, pid: int, *, vm_user: str = "agent") -> VmActionResult:
        return self.vm_agent_admin.kill_agent(target, pid, vm_user=vm_user)

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
    ) -> VmActionResult:
        return self.vm_agent_admin.resume_agent(
            target,
            prompt,
            model=model,
            vm_user=vm_user,
            dispatch_id=dispatch_id,
            resume_thread_id=resume_thread_id,
            resume_mode=resume_mode,
        )

    def _spawn_agent_completion_watcher(
        self,
        *,
        vm_name: str,
        dispatch_id: str,
        vm_user: str,
        session_id: str | None,
        directory_path: str | None,
    ) -> None:
        watcher_cmd = [
            sys.executable,
            "-m",
            "agent_sandbox.daemons.agent_completion_watcher",
            "--runtime-dir",
            str(self.config.runtime_dir),
            "--vm-name",
            vm_name,
            "--dispatch-id",
            dispatch_id,
            "--vm-user",
            vm_user,
            "--poll-interval-seconds",
            "1.0",
            "--timeout-seconds",
            "172800",
        ]
        trigger_worker_config = self._read_worker_global_config("trigger-orchestrator").config
        workflow_runtime_dir = _resolve_path_setting(
            base_dir=self.project_dir,
            value=trigger_worker_config.get("runtime_dir"),
            fallback=self.config.runtime_dir,
        )
        event_queue_store_path = _resolve_path_setting(
            base_dir=self.project_dir,
            value=trigger_worker_config.get("event_queue_store_path"),
            fallback=workflow_runtime_dir / "trigger-orchestrator-events.json",
        )
        workflow_event_journal = _resolve_path_setting(
            base_dir=self.project_dir,
            value=trigger_worker_config.get("workflow_event_journal_path"),
            fallback=workflow_event_journal_path(workflow_runtime_dir),
        )
        orchestration_backend = (
            str(trigger_worker_config.get("orchestration_store_backend") or "sqlite")
            .strip()
            .lower()
        )
        if orchestration_backend not in {"sqlite", "json"}:
            orchestration_backend = "sqlite"
        orchestration_store_path = _resolve_path_setting(
            base_dir=self.project_dir,
            value=trigger_worker_config.get("orchestration_store_path"),
            fallback=workflow_runtime_dir / "orchestration.db",
        )
        orchestration_retention_days = trigger_worker_config.get("orchestration_retention_days")
        watcher_cmd.extend(["--event-queue-store-path", str(event_queue_store_path)])
        watcher_cmd.extend(["--workflow-event-journal-path", str(workflow_event_journal)])
        watcher_cmd.extend(["--orchestration-store-backend", orchestration_backend])
        watcher_cmd.extend(["--orchestration-store-path", str(orchestration_store_path)])
        if orchestration_retention_days is not None:
            watcher_cmd.extend(
                ["--orchestration-retention-days", str(orchestration_retention_days)]
            )
        if session_id:
            watcher_cmd.extend(["--session-id", session_id])
        if directory_path:
            watcher_cmd.extend(["--directory-path", directory_path])
        subprocess.Popen(
            watcher_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            cwd=str(self.project_dir),
            env=os.environ.copy(),
            text=True,
        )

    def vm_exec(
        self,
        target: str,
        *,
        command: str | list[str],
        vm_user: str = "agent",
        working_directory: str | None = None,
        environment: dict[str, str] | None = None,
        stdin: str | None = None,
        timeout_seconds: float | None = None,
        check: bool = True,
    ) -> dict[str, Any]:
        return self.vm_agent_admin.vm_exec(
            target,
            command=command,
            vm_user=vm_user,
            working_directory=working_directory,
            environment=environment,
            stdin=stdin,
            timeout_seconds=timeout_seconds,
            check=check,
        )

    def vm_copy_files(
        self,
        target: str,
        *,
        files: list[dict[str, Any]],
        vm_user: str = "agent",
    ) -> dict[str, Any]:
        return self.vm_agent_admin.vm_copy_files(target, files=files, vm_user=vm_user)

    def vm_delete_files(
        self,
        target: str,
        *,
        paths: list[str],
        vm_user: str = "agent",
        recursive: bool = False,
        missing_ok: bool = True,
    ) -> dict[str, Any]:
        return self.vm_agent_admin.vm_delete_files(
            target,
            paths=paths,
            vm_user=vm_user,
            recursive=recursive,
            missing_ok=missing_ok,
        )

    def pr_create(
        self,
        *,
        host_repo_path: str,
        local_branch: str | None = None,
        origin_branch: str | None = None,
        canonical_origin_url: str | None = None,
        provider: str | None = None,
        project: str | None = None,
        base_branch: str | None = None,
        session_id: str | None = None,
        work_item_id: str | None = None,
        title: str | None = None,
        body: str | None = None,
        body_file: str | None = None,
        body_artifact_ids: list[str] | None = None,
        draft: bool | None = None,
        fill: bool = True,
    ) -> dict[str, Any]:
        return self.issue_git_admin.pr_create(
            host_repo_path=host_repo_path,
            local_branch=local_branch,
            origin_branch=origin_branch,
            canonical_origin_url=canonical_origin_url,
            provider=provider,
            project=project,
            base_branch=base_branch,
            session_id=session_id,
            work_item_id=work_item_id,
            title=title,
            body=body,
            body_file=body_file,
            body_artifact_ids=body_artifact_ids,
            draft=draft,
            fill=fill,
        )

    def promote_agent_branch(
        self,
        *,
        host_repo_path: str,
        bare_repo_path: str | None = None,
        bare_repo_name: str | None = None,
        agent_branch: str,
        review_branch: str | None = None,
        remote_name: str | None = None,
    ) -> dict[str, Any]:
        return self.issue_git_admin.promote_agent_branch(
            host_repo_path=host_repo_path,
            bare_repo_path=bare_repo_path,
            bare_repo_name=bare_repo_name,
            agent_branch=agent_branch,
            review_branch=review_branch,
            remote_name=remote_name,
        )

    @staticmethod
    def _derive_review_branch(agent_branch: str) -> str:
        normalized = str(agent_branch).strip()
        if normalized.startswith("agents/") and normalized.count("/") >= 2:
            return f"review/{normalized.removeprefix('agents/')}"
        return f"review/{normalized.removeprefix('refs/heads/')}"

    def git_pr_note_query(
        self,
        *,
        host_repo_path: str,
        local_branch: str,
        remote_name: str | None = None,
        notes_ref: str | None = None,
    ) -> dict[str, Any]:
        return self.issue_git_admin.git_pr_note_query(
            host_repo_path=host_repo_path,
            local_branch=local_branch,
            remote_name=remote_name,
            notes_ref=notes_ref,
        )

    def session_update_metadata(
        self, *, target: str, metadata: Mapping[str, Any]
    ) -> dict[str, Any]:
        return self.issue_git_admin.session_update_metadata(target=target, metadata=metadata)

    def session_context_query(self, *, target: str) -> dict[str, Any]:
        return self.issue_git_admin.session_context_query(target=target)

    def read_session_artifact_text(
        self,
        *,
        session_id: str,
        path: str,
        vm_user: str = "agent",
    ) -> dict[str, Any]:
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            raise ValueError("session_id is required")
        relative_path = normalize_artifact_relative_path(path)
        session = self.runtime.session_repository.get_session(normalized_session_id)
        if session is None:
            raise ValueError(f"Unknown session: {normalized_session_id}")

        metadata = session.metadata if isinstance(session.metadata, Mapping) else {}
        provider = str(
            metadata.get("execution_backend")
            or session.vm_provider
            or metadata.get("vm_provider")
            or ""
        ).strip()
        is_container_session = provider == "container_worker_vm"
        if is_container_session:
            worker_url = self._container_worker_url_for_target(session=session)
            backend = self._container_worker_backend(
                directory_path=session.directory_path,
                worker_url=worker_url,
            )
            content = backend.read_session_artifact_text(
                session_id=normalized_session_id,
                relative_path=relative_path,
            )
            return {
                "session_id": normalized_session_id,
                "path": relative_path,
                "content": content,
                "byte_count": len(content.encode("utf-8")),
                "artifacts_root": DEFAULT_AGENT_ARTIFACTS_DIR,
                "execution_backend": "container_worker_vm",
            }

        result = self.vm_exec(
            normalized_session_id,
            command=_VM_ARTIFACT_TEXT_READ_COMMAND,
            vm_user=(str(vm_user or "").strip() or "agent"),
            environment={"AGENT_SANDBOX_ARTIFACT_RELATIVE_PATH": relative_path},
            timeout_seconds=30.0,
            check=True,
        )
        if not bool(result.get("success")):
            raise RuntimeError(
                str(result.get("stderr") or result.get("stdout") or result.get("message") or "")
                .strip()
                or "VM artifact read failed"
            )
        content = str(result.get("stdout") or "")
        return {
            "session_id": normalized_session_id,
            "path": relative_path,
            "content": content,
            "byte_count": len(content.encode("utf-8")),
            "artifacts_root": DEFAULT_AGENT_ARTIFACTS_DIR,
            "execution_backend": "libvirt_vm",
            "vm_name": result.get("vm_name") or session.vm_id,
            "vm_provider": session.vm_provider,
        }

    @staticmethod
    def _derive_local_pr_notes_ref(remote_url: str) -> str:

        from agent_sandbox.admin.services.issue_git_service import IssueGitAdminService

        return IssueGitAdminService._derive_local_pr_notes_ref(remote_url)

    def list_local_issues(
        self,
        *,
        directory_path: str | None = None,
        direct_only: bool = False,
        states: set[str] | None = None,
        exclude_states: set[str] | None = None,
    ) -> list[LocalIssueRecord]:
        return self.issue_git_admin.list_local_issues(
            directory_path=directory_path,
            direct_only=direct_only,
            states=states,
            exclude_states=exclude_states,
        )

    def _extract_local_issue_id_candidate(self, value: str) -> str | None:
        normalized = value.strip()
        if not normalized:
            return None
        if normalized.isdigit():
            return normalized
        if normalized.startswith("vm-") and normalized[3:].isdigit():
            return normalized[3:]
        prefixed_match = re.match(
            r"^(?:local|issue)[:/\-]([0-9]+)$", normalized, flags=re.IGNORECASE
        )
        if prefixed_match:
            return prefixed_match.group(1)
        session_match = re.match(r"^issue-([0-9]+)(?:-|$)", normalized)
        if session_match:
            return session_match.group(1)
        return None

    def _find_local_issue_by_remote_identifier(
        self, issue_identifier: str
    ) -> LocalIssueRecord | None:
        normalized = issue_identifier.strip()
        if not normalized:
            return None
        provider = ""
        remote_issue_id = ""
        prefixed_match = re.match(
            r"^(?:(?:proxy:)?(azure_devops|ado)[:/])([0-9]+)$",
            normalized,
            flags=re.IGNORECASE,
        )
        if prefixed_match:
            provider = "azure_devops"
            remote_issue_id = prefixed_match.group(2)
        if not remote_issue_id:
            return None
        matches: list[LocalIssueRecord] = []
        for issue in self.local_issue_store.read_all().values():
            remote = issue.remote_reference
            if remote is not None:
                issue_provider = str(remote.provider or "").strip()
                issue_remote_id = str(remote.work_item_id or "").strip()
            else:
                legacy_remote = (
                    issue.metadata.get("remote")
                    if isinstance(issue.metadata.get("remote"), dict)
                    else {}
                )
                issue_provider = str(legacy_remote.get("provider") or "").strip()
                issue_remote_id = str(legacy_remote.get("issue_id") or "").strip()
            if issue_provider != provider:
                continue
            if issue_remote_id == remote_issue_id:
                matches.append(issue)
        if not matches:
            return None
        if len(matches) == 1:
            return matches[0]
        sorted_matches = sorted(issue.issue_id for issue in matches)
        raise ValueError(
            "Ambiguous proxy issue identifier: "
            f"{normalized}. Matches: {', '.join(sorted_matches)}. "
            "Use a canonical proxy issue identifier."
        )
        return None

    def _resolve_local_issue_record(self, issue_identifier: str) -> LocalIssueRecord:
        candidate = Path(issue_identifier)
        if candidate.is_file():
            candidate_path = candidate.resolve()
            for issue in self.local_issue_store.read_all().values():
                if issue.yaml_path and Path(issue.yaml_path).resolve() == candidate_path:
                    return issue
            issue_number = _read_issue_number_from_yaml(candidate)
            raise ValueError(f"Unknown local issue: {issue_number}")

        issue_key = self._resolve_local_issue_key(issue_identifier)
        if issue_key is not None:
            issue = self.local_issue_store.get(issue_key)
            if issue is not None:
                return issue

        by_remote_identifier = self._find_local_issue_by_remote_identifier(issue_identifier)
        if by_remote_identifier is not None:
            return by_remote_identifier

        raise ValueError(f"Unknown local issue identifier: {issue_identifier}")

    def list_issues(
        self,
        *,
        directory_path: str | None = None,
        direct_only: bool = False,
        states: set[str] | None = None,
        exclude_states: set[str] | None = None,
    ) -> list[LocalIssueRecord]:
        return self.issue_git_admin.list_issues(
            directory_path=directory_path,
            direct_only=direct_only,
            states=states,
            exclude_states=exclude_states,
        )

    def _refresh_approval_request(self, request: ApprovalRequest) -> ApprovalRequest:
        session = self.runtime.session_repository.get_session(request.session_id)
        mutated = False
        if not request.directory_path and session is not None:
            request.directory_path = session.directory_path
            mutated = True
        if request.vm_id is None and session is not None and session.vm_id:
            request.vm_id = session.vm_id
            mutated = True
        if not request.tool_name:
            request.tool_name = request.action_type
            mutated = True
        if (
            request.status in {ApprovalStatus.PENDING, ApprovalStatus.APPROVED}
            and request.expires_at <= _utc_now()
        ):
            request.status = ApprovalStatus.EXPIRED
            mutated = True
        if mutated:
            self.runtime.approvals.put(request.request_id, request)
        return request

    def _approval_matches_filters(
        self,
        request: ApprovalRequest,
        *,
        statuses: set[ApprovalStatus] | None = None,
        directory_path: str | None = None,
        direct_only: bool = False,
        session_id: str | None = None,
        vm_id: str | None = None,
        tool_name: str | None = None,
        min_age_seconds: int | None = None,
    ) -> bool:
        if statuses is not None and request.status not in statuses:
            return False
        if directory_path is not None:
            normalized_directory_path = self._normalize_directory_path(directory_path)
            request_directory_path = self._normalize_directory_path(request.directory_path)
            if direct_only or request_directory_path != normalized_directory_path:
                if request_directory_path != normalized_directory_path:
                    return False
        if session_id is not None and request.session_id != session_id.strip():
            return False
        if vm_id is not None and (request.vm_id or "") != vm_id.strip():
            return False
        if (
            tool_name is not None
            and (request.tool_name or request.action_type) != tool_name.strip()
        ):
            return False
        if min_age_seconds is not None:
            age_seconds = int((_utc_now() - request.created_at).total_seconds())
            if age_seconds < min_age_seconds:
                return False
        return True

    def list_approval_requests(
        self,
        *,
        statuses: set[ApprovalStatus] | None = None,
        directory_path: str | None = None,
        direct_only: bool = False,
        session_id: str | None = None,
        vm_id: str | None = None,
        tool_name: str | None = None,
        min_age_seconds: int | None = None,
    ) -> list[ApprovalRequest]:
        return self.issue_git_admin.list_approval_requests(
            statuses=statuses,
            directory_path=directory_path,
            direct_only=direct_only,
            session_id=session_id,
            vm_id=vm_id,
            tool_name=tool_name,
            min_age_seconds=min_age_seconds,
        )

    def approval_queue_health(
        self,
        *,
        statuses: set[ApprovalStatus] | None = None,
        directory_path: str | None = None,
        direct_only: bool = False,
        session_id: str | None = None,
        vm_id: str | None = None,
        tool_name: str | None = None,
        min_age_seconds: int | None = None,
    ) -> ApprovalQueueHealth:
        return self.issue_git_admin.approval_queue_health(
            statuses=statuses,
            directory_path=directory_path,
            direct_only=direct_only,
            session_id=session_id,
            vm_id=vm_id,
            tool_name=tool_name,
            min_age_seconds=min_age_seconds,
        )

    def get_approval_request(self, request_id: str) -> ApprovalRequest:
        return self.issue_git_admin.get_approval_request(request_id)

    def _approval_audit_context(
        self, request: ApprovalRequest, *, limit: int = 8
    ) -> list[dict[str, Any]]:
        if not self.config.audit_log_path.exists():
            return []
        try:
            lines = self.config.audit_log_path.read_text(encoding="utf-8").splitlines()
        except Exception:
            return []
        matches: list[dict[str, Any]] = []
        for raw_line in reversed(lines):
            try:
                payload = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            args_payload = payload.get("args") if isinstance(payload.get("args"), dict) else {}
            result_payload = (
                payload.get("result") if isinstance(payload.get("result"), dict) else {}
            )
            if (
                payload.get("session_id") == request.session_id
                or args_payload.get("request_id") == request.request_id
                or result_payload.get("request_id") == request.request_id
            ):
                matches.append(payload)
            if len(matches) >= limit:
                break
        matches.reverse()
        return matches

    def get_approval_review(self, request_id: str) -> dict[str, Any]:
        return self.issue_git_admin.get_approval_review(request_id)

    def decide_approval_request(
        self,
        request_id: str,
        *,
        approver: str,
        approve: bool,
        reason: str,
    ) -> ApprovalRequest:
        return self.issue_git_admin.decide_approval_request(
            request_id,
            approver=approver,
            approve=approve,
            reason=reason,
        )

    def list_actionable_local_issues(
        self,
        *,
        directory_path: str,
        states: set[str] | None = None,
    ) -> list[LocalIssueRecord]:
        return self.issue_git_admin.list_actionable_local_issues(
            directory_path=directory_path, states=states
        )

    def _resolve_local_issue_key(self, identifier: str) -> str | None:
        normalized = identifier.strip()
        if not normalized:
            return None
        issues = self.local_issue_store.read_all()
        if normalized in issues:
            return normalized
        matches = [
            issue_id for issue_id, issue in issues.items() if issue.issue_number == normalized
        ]
        if not matches:
            return None
        if len(matches) == 1:
            return matches[0]
        sorted_matches = sorted(matches)
        raise ValueError(
            "Ambiguous local issue identifier: "
            f"{normalized}. Matches: {', '.join(sorted_matches)}. "
            "Use a scoped issue identifier."
        )

    def list_direct_owned_entity_ids(self, directory_path: str, *, kind: str) -> list[str]:
        return self.issue_git_admin.list_direct_owned_entity_ids(directory_path, kind=kind)

    def list_directory_tree(self):
        return self.operations_admin.list_directory_tree()

    def get_directory(self, reference: str):
        return self.operations_admin.get_directory(reference)

    def create_directory_child(self, parent: str, name: str):
        return self.operations_admin.create_directory_child(parent, name)

    def read_directory_domain_config(self, directory_path: str, domain: str):
        return self.operations_admin.read_directory_domain_config(directory_path, domain)

    def write_directory_domain_config(
        self, directory_path: str, domain: str, payload: dict[str, object]
    ):
        return self.operations_admin.write_directory_domain_config(directory_path, domain, payload)

    def compute_directory_effective_config(self, directory_path: str, domain: str):
        return self.operations_admin.compute_directory_effective_config(directory_path, domain)

    def read_worker_global_config(self, worker_id: str):
        return self.workflow_config_admin.read_worker_global_config(worker_id)

    def write_worker_global_config(self, worker_id: str, payload: dict[str, object]):
        return self.workflow_config_admin.write_worker_global_config(worker_id, payload)

    def read_workflow_editor_document(self) -> WorkflowEditorDocumentPayload:
        return self.workflow_config_admin.read_workflow_editor_document()

    def read_workflow_editor_catalog(self) -> WorkflowEditorCatalogPayload:
        return self.workflow_config_admin.read_workflow_editor_catalog()

    def list_skills(self) -> list[SkillLibraryMetadata]:
        return self.skill_library.list_skills()

    def create_skill(self, payload: Mapping[str, Any]) -> SkillLibraryMetadata:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("Skill name is required.")
        if "content" not in payload:
            raise ValueError("Skill content is required.")
        return self.skill_library.create_skill(
            name=name,
            content=payload.get("content"),
            replace=bool(payload.get("replace", False)),
        )

    def get_skill(self, name: str) -> SkillLibraryEntry:
        return self.skill_library.get_skill(name)

    def delete_skill(self, name: str) -> SkillLibraryMetadata:
        return self.skill_library.delete_skill(name)

    def validate_managed_codex_skills(
        self, skill_names: list[str] | tuple[str, ...] | None
    ) -> list[str]:
        names = normalize_skill_names(skill_names)
        for name in names:
            if not self.skill_library.has_skill(name):
                raise ValueError(f"Unknown Codex skill: {name}")
        return names

    def resolve_container_codex_skill_files(
        self, skill_names: list[str] | tuple[str, ...] | None
    ) -> list[dict[str, Any]]:
        names = self.validate_managed_codex_skills(skill_names)
        return self.skill_library.container_file_specs(names)

    def validate_workflow_editor_document(
        self,
        *,
        config: dict[str, object],
        layout: dict[str, object] | None = None,
        graphs: list[dict[str, object]] | None = None,
    ) -> WorkflowEditorValidationPayload:
        return self.workflow_config_admin.validate_workflow_editor_document(
            config=config, layout=layout, graphs=graphs
        )

    def save_workflow_editor_document(
        self,
        *,
        config: dict[str, object],
        layout: dict[str, object] | None = None,
        graphs: list[dict[str, object]] | None = None,
    ) -> WorkflowEditorSavePayload:
        return self.workflow_config_admin.save_workflow_editor_document(
            config=config, layout=layout, graphs=graphs
        )

    def read_workflow_run_snapshot(self) -> WorkflowRunSnapshotPayload:
        return self.workflow_config_admin.read_workflow_run_snapshot()

    def read_workflow_graph_activity_report(
        self,
        *,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
        limit: int | None = None,
    ) -> WorkflowGraphActivityReportPayload:
        return self._read_workflow_graph_activity_report(
            period_start=period_start,
            period_end=period_end,
            limit=limit,
        )

    def read_conformance_reports(self) -> ConformanceReportsPayload:
        return self._read_conformance_reports()

    def clear_workflow_run(self, event_id: str) -> WorkflowRunClearPayload:
        return self.workflow_config_admin.clear_workflow_run(event_id)

    def resume_workflow_run(self, event_id: str) -> WorkflowRunResumePayload:
        return self.workflow_config_admin.resume_workflow_run(event_id)

    def workflow_event_journal(self) -> WorkflowEventJournal:
        return self.workflow_config_admin.workflow_event_journal()

    def upsert_trigger_config(
        self, trigger_id: str, payload: dict[str, object]
    ) -> TriggerConfigMutationPayload:
        return self.workflow_config_admin.upsert_trigger_config(trigger_id, payload)

    def delete_trigger_config(self, trigger_id: str) -> TriggerConfigMutationPayload:
        return self.workflow_config_admin.delete_trigger_config(trigger_id)

    def upsert_effect_config(
        self, effect_id: str, payload: dict[str, object]
    ) -> EffectConfigMutationPayload:
        return self.workflow_config_admin.upsert_effect_config(effect_id, payload)

    def delete_effect_config(self, effect_id: str) -> EffectConfigMutationPayload:
        return self.workflow_config_admin.delete_effect_config(effect_id)

    def _worker_global_config_path(self, worker_id: str) -> Path | None:
        relative = _WORKER_GLOBAL_CONFIG_PATHS.get(worker_id)
        if relative is None:
            return None
        return self.project_dir / relative

    def _read_worker_global_config(self, worker_id: str) -> WorkerGlobalConfigPayload:
        path = self._worker_global_config_path(worker_id)
        if path is None:
            raise ValueError(f"Unknown worker: {worker_id}")
        exists = path.exists()
        if exists:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError(f"worker config file {path} must contain a JSON object")
        else:
            model = _WORKER_GLOBAL_CONFIG_MODELS[worker_id]
            raw = model.model_validate(self.config.model_dump(mode="json")).model_dump(
                mode="json", exclude_none=True
            )
        model = _WORKER_GLOBAL_CONFIG_MODELS[worker_id]
        config = self._validate_worker_global_config_payload(worker_id, raw)
        return WorkerGlobalConfigPayload(
            worker_id=worker_id, path=str(path), exists=exists, config=config
        )

    def _write_worker_global_config(
        self, worker_id: str, payload: dict[str, object]
    ) -> WorkerGlobalConfigWritePayload:
        path = self._worker_global_config_path(worker_id)
        if path is None:
            raise ValueError(f"Unknown worker: {worker_id}")
        config = self._validate_worker_global_config_payload(worker_id, payload)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config, indent=2, sort_keys=True), encoding="utf-8")
        return WorkerGlobalConfigWritePayload(worker_id=worker_id, path=str(path), config=config)

    def _read_trigger_document(self) -> TriggerConfigDocument:
        payload = self._read_worker_global_config("trigger-orchestrator")
        return TriggerConfigDocument(path=payload.path, config=payload.config)

    def _write_trigger_document(self, config: dict[str, object]) -> TriggerConfigDocument:
        payload = self._write_worker_global_config("trigger-orchestrator", config)
        return TriggerConfigDocument(path=payload.path, config=payload.config)

    def _validate_worker_global_config_payload(
        self, worker_id: str, payload: dict[str, object]
    ) -> dict[str, object]:
        model = _WORKER_GLOBAL_CONFIG_MODELS[worker_id]
        try:
            normalized = model.model_validate(payload).model_dump(mode="json", exclude_none=True)
            return _strip_default_edge_fields(normalized)
        except ValidationError as exc:
            raise _safe_validation_error(exc, context=f"Invalid {worker_id} worker config") from exc

    def assistant_settings_path(self) -> Path:
        return self.config.runtime_dir / "assistant-settings.json"

    def read_assistant_settings(self) -> dict[str, object]:
        path = self.assistant_settings_path()
        if not path.exists():
            return self.config.assistant_settings.model_dump(mode="json", exclude_none=True)
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"assistant settings file {path} must contain a JSON object")
        return AssistantSettingsConfig.model_validate(payload).model_dump(
            mode="json", exclude_none=True
        )

    def write_assistant_settings(self, payload: dict[str, object]) -> dict[str, object]:
        config = AssistantSettingsConfig.model_validate(payload).model_dump(
            mode="json", exclude_none=True
        )
        path = self.assistant_settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config, indent=2, sort_keys=True), encoding="utf-8")
        return config

    def ensure_chat_assistant_workflow(self) -> TriggerConfigDocument:
        current = self.trigger_repository.read_document()
        config, changed = self._with_system_chat_assistant_workflow(current.config)
        if not changed:
            return current
        return self.trigger_repository.write_document(config)

    def publish_chat_assistant_message(
        self,
        *,
        user_id: str,
        thread_id: str,
        user_message_id: str,
        content: str,
        backend: str | None = None,
        assistant_capability_mode: str | None = None,
    ) -> dict[str, object]:
        document = self.ensure_chat_assistant_workflow()
        worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(document.config)
        if worker_config.orchestration_store_backend == "sqlite":
            queue = SqliteTriggerEventQueue(
                worker_config.orchestration_store_path,
                retention_days=worker_config.orchestration_retention_days,
            )
        else:
            queue = TriggerEventQueue(
                worker_config.event_queue_store_path,
                journal_path=worker_config.workflow_event_journal_path,
            )
        normalized_backend = str(backend or self.config.execution_backend).strip() or "libvirt_vm"
        normalized_capability_mode = str(assistant_capability_mode or "").strip() or "read-only"
        if normalized_capability_mode != "full-access":
            normalized_capability_mode = "read-only"
        event_id = f"chat:{user_message_id}"
        event = TriggerEvent(
            event_id=event_id,
            trigger_id=_SYSTEM_CHAT_ASSISTANT_TRIGGER_ID,
            trigger_type="trigger:system:chat-assistant",
            occurred_at=_utc_now(),
            payload={
                "user_id": user_id,
                "thread_id": thread_id,
                "user_message_id": user_message_id,
                "content": content,
                "backend": normalized_backend,
                "assistant_capability_mode": normalized_capability_mode,
            },
        )
        record = queue.publish(event, idempotency_key=event_id)
        return record.model_dump(mode="json")

    def _workflow_editor_default_layout_path(self) -> Path:
        return self.project_dir / "config" / "trigger-orchestrator" / "layout.json"

    def _workflow_editor_pack_layout_path(self, pack_reference: object) -> Path:
        return self._workflow_editor_pack_path(pack_reference).with_suffix(".layout.json")

    def _workflow_editor_layout_path(self, raw_config: dict[str, object]) -> Path:
        workflow_packs = raw_config.get("workflow_packs")
        inline_ir = raw_config.get("workflow_ir")
        if (
            isinstance(workflow_packs, list)
            and len(workflow_packs) == 1
            and not isinstance(inline_ir, dict)
        ):
            return self._workflow_editor_pack_layout_path(workflow_packs[0])
        return self._workflow_editor_default_layout_path()

    def _workflow_editor_revision(self, config: dict[str, object]) -> str:
        encoded = json.dumps(config, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _workflow_editor_pack_path(self, pack_reference: object) -> Path:
        text = str(pack_reference).strip()
        if not text:
            raise ValueError("workflow pack reference must be a non-empty string")
        normalized = text if text.endswith(".json") else f"{text}.json"
        relative = Path(normalized)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("workflow pack reference must stay under config/workflow-packs")
        return self.project_dir / "config" / "workflow-packs" / relative

    def _workflow_editor_stored_graphs(
        self, raw_config: dict[str, object]
    ) -> list[dict[str, object]]:
        graphs: list[dict[str, object]] = []
        workflow_packs = raw_config.get("workflow_packs")
        if isinstance(workflow_packs, list):
            for pack_reference in workflow_packs:
                pack_path = self._workflow_editor_pack_path(pack_reference)
                if not pack_path.exists():
                    continue
                pack_payload = json.loads(pack_path.read_text(encoding="utf-8"))
                if not isinstance(pack_payload, dict):
                    continue
                pack_graphs = pack_payload.get("graphs")
                if isinstance(pack_graphs, list):
                    graphs.extend(item for item in pack_graphs if isinstance(item, dict))
        inline_graphs = raw_config.get("workflow_graphs")
        if isinstance(inline_graphs, list):
            graphs.extend(item for item in inline_graphs if isinstance(item, dict))
        return graphs

    def _workflow_editor_nodes_by_id(
        self, config: dict[str, object]
    ) -> dict[str, dict[str, object]]:
        nodes_by_id: dict[str, dict[str, object]] = {}
        for collection_name in ("triggers", "gates", "effects", "queries"):
            entries = config.get(collection_name)
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                node_id = str(entry.get("id") or "").strip()
                if node_id:
                    nodes_by_id[node_id] = entry
        return nodes_by_id

    def _workflow_editor_connected_graph_node_ids(
        self, config: dict[str, object]
    ) -> list[list[str]]:
        nodes_by_id = self._workflow_editor_nodes_by_id(config)
        ordered_node_ids = list(nodes_by_id)
        node_ids = set(ordered_node_ids)
        neighbors: dict[str, set[str]] = {node_id: set() for node_id in ordered_node_ids}
        edges = config.get("edges")
        if isinstance(edges, list):
            for edge in edges:
                if not isinstance(edge, dict):
                    continue
                source_id = str(edge.get("source_id") or "").strip()
                target_id = str(edge.get("target_id") or "").strip()
                if source_id not in node_ids or target_id not in node_ids:
                    continue
                neighbors[source_id].add(target_id)
                neighbors[target_id].add(source_id)

        components: list[list[str]] = []
        visited: set[str] = set()
        order = {node_id: index for index, node_id in enumerate(ordered_node_ids)}
        for node_id in ordered_node_ids:
            if node_id in visited:
                continue
            stack = [node_id]
            component: set[str] = set()
            while stack:
                current = stack.pop()
                if current in visited:
                    continue
                visited.add(current)
                component.add(current)
                stack.extend(sorted(neighbors[current] - visited, key=order.get, reverse=True))
            components.append(sorted(component, key=order.get))
        return components

    def _workflow_editor_default_graph_name(
        self,
        *,
        component: list[str],
        nodes_by_id: dict[str, dict[str, object]],
        index: int,
    ) -> str:
        trigger_node = next(
            (
                nodes_by_id[node_id]
                for node_id in component
                if str(nodes_by_id[node_id].get("type") or "").startswith("trigger:")
            ),
            None,
        )
        fallback_node = nodes_by_id.get(component[0]) if component else None
        source_node = trigger_node or fallback_node
        display_name = str(source_node.get("display_name") or "").strip() if source_node else ""
        return display_name or f"Graph {index + 1}"

    def _workflow_editor_graph_id_base(
        self,
        *,
        component: list[str],
        nodes_by_id: dict[str, dict[str, object]],
        index: int,
    ) -> str:
        trigger_id = next(
            (
                node_id
                for node_id in component
                if str(nodes_by_id[node_id].get("type") or "").startswith("trigger:")
            ),
            "",
        )
        source = trigger_id or (component[0] if component else f"graph-{index + 1}")
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", source).strip("-").lower()
        return f"graph-{slug or index + 1}"

    def _coerce_workflow_editor_graphs(
        self,
        graphs: object,
        *,
        strict: bool,
    ) -> list[WorkflowEditorGraphMetadata]:
        if graphs is None:
            return []
        if not isinstance(graphs, list):
            if strict:
                raise ValueError("Workflow graphs must be a JSON array.")
            return []
        normalized: list[WorkflowEditorGraphMetadata] = []
        for entry in graphs:
            if not isinstance(entry, dict):
                if strict:
                    raise ValueError("Workflow graph entries must be JSON objects.")
                continue
            try:
                graph = WorkflowEditorGraphMetadata.model_validate(entry)
            except ValidationError as exc:
                if strict:
                    raise _safe_validation_error(exc, context="Invalid workflow graph") from exc
                continue
            graph_id = graph.id.strip()
            graph_name = graph.name.strip()
            node_ids: list[str] = []
            seen_node_ids: set[str] = set()
            for raw_node_id in graph.node_ids:
                node_id = str(raw_node_id or "").strip()
                if not node_id or node_id in seen_node_ids:
                    continue
                seen_node_ids.add(node_id)
                node_ids.append(node_id)
            if not graph_id or not graph_name:
                if strict:
                    raise ValueError("Workflow graph id and name are required.")
                continue
            description = graph.description.strip() if graph.description else None
            directory_paths: list[str] = []
            seen_directory_paths: set[str] = set()
            for raw_directory_path in graph.directory_paths:
                directory_path = str(raw_directory_path or "").strip()
                if not directory_path:
                    continue
                try:
                    normalized_directory_path = DirectoryPath(segments=directory_path).as_posix()
                except ValueError as exc:
                    if strict:
                        raise ValueError(
                            f"Workflow graph {graph_id} has invalid directory path: {directory_path}"
                        ) from exc
                    continue
                if normalized_directory_path in seen_directory_paths:
                    continue
                seen_directory_paths.add(normalized_directory_path)
                directory_paths.append(normalized_directory_path)
            if not directory_paths:
                directory_paths = ["root"]
            user_ids: list[str] = []
            seen_user_ids: set[str] = set()
            for raw_user_id in graph.user_ids:
                user_id = str(raw_user_id or "").strip()
                if not user_id or user_id in seen_user_ids:
                    continue
                seen_user_ids.add(user_id)
                user_ids.append(user_id)
            normalized.append(
                WorkflowEditorGraphMetadata(
                    id=graph_id,
                    name=graph_name,
                    description=description or None,
                    directory_paths=directory_paths,
                    user_ids=user_ids,
                    node_ids=node_ids,
                )
            )
        return normalized

    def _workflow_editor_graphs_for_config(
        self,
        *,
        config: dict[str, object],
        graphs: object,
        strict: bool = False,
    ) -> list[WorkflowEditorGraphMetadata]:
        components = self._workflow_editor_connected_graph_node_ids(config)
        component_keys = {frozenset(component) for component in components}
        node_ids = set(self._workflow_editor_nodes_by_id(config))
        stored_graphs = self._coerce_workflow_editor_graphs(graphs, strict=strict)
        seen_graph_ids: set[str] = set()
        seen_graph_keys: set[frozenset[str]] = set()
        valid_stored_graphs: list[WorkflowEditorGraphMetadata] = []
        for graph in stored_graphs:
            graph_key = frozenset(graph.node_ids)
            if graph.id in seen_graph_ids:
                if strict:
                    raise ValueError(f"Duplicate workflow graph id: {graph.id}")
                continue
            seen_graph_ids.add(graph.id)
            unknown_node_ids = sorted(
                node_id for node_id in graph.node_ids if node_id not in node_ids
            )
            if unknown_node_ids:
                if strict:
                    unknown_list = ", ".join(unknown_node_ids)
                    raise ValueError(
                        f"Workflow graph {graph.id} references unknown node IDs: {unknown_list}"
                    )
                continue
            if graph_key in seen_graph_keys:
                if strict:
                    raise ValueError(
                        f"Multiple workflow graphs describe the same connected graph: {graph.id}"
                    )
                continue
            seen_graph_keys.add(graph_key)
            if strict and graph_key not in component_keys:
                raise ValueError(
                    f"Workflow graph {graph.id} node_ids must match one connected graph."
                )
            valid_stored_graphs.append(graph)
        stored_graphs = valid_stored_graphs

        by_exact_key = {frozenset(graph.node_ids): graph for graph in stored_graphs}
        unmatched_graphs = [
            graph for graph in stored_graphs if frozenset(graph.node_ids) not in component_keys
        ]
        used_graph_ids: set[str] = set()
        used_result_ids: set[str] = set()
        result: list[WorkflowEditorGraphMetadata] = []
        nodes_by_id = self._workflow_editor_nodes_by_id(config)
        for index, component in enumerate(components):
            component_key = frozenset(component)
            matched = by_exact_key.get(component_key)
            if matched is None:
                matched = max(
                    (
                        graph
                        for graph in unmatched_graphs
                        if graph.id not in used_graph_ids
                        and component_key.intersection(graph.node_ids)
                    ),
                    key=lambda graph: len(component_key.intersection(graph.node_ids)),
                    default=None,
                )
            if matched is not None:
                used_graph_ids.add(matched.id)
                graph_id = matched.id
                graph_name = matched.name
                graph_description = matched.description
                graph_directory_paths = matched.directory_paths or ["root"]
                graph_user_ids = matched.user_ids
            else:
                graph_id = self._workflow_editor_graph_id_base(
                    component=component, nodes_by_id=nodes_by_id, index=index
                )
                graph_name = self._workflow_editor_default_graph_name(
                    component=component, nodes_by_id=nodes_by_id, index=index
                )
                graph_description = None
                graph_directory_paths = ["root"]
                graph_user_ids = []
            unique_id = graph_id
            suffix = 2
            while unique_id in used_result_ids:
                unique_id = f"{graph_id}-{suffix}"
                suffix += 1
            used_result_ids.add(unique_id)
            result.append(
                WorkflowEditorGraphMetadata(
                    id=unique_id,
                    name=graph_name,
                    description=graph_description,
                    directory_paths=graph_directory_paths,
                    user_ids=graph_user_ids,
                    node_ids=component,
                )
            )
        return result

    def _workflow_editor_graph_payloads_to_persist(
        self,
        *,
        raw_graphs: list[dict[str, object]],
        normalized_graphs: list[WorkflowEditorGraphMetadata],
    ) -> list[dict[str, object]]:
        raw_graphs_by_id: dict[str, dict[str, object]] = {}
        normalized_raw_graphs_by_id: dict[str, dict[str, object]] = {}
        for raw_graph in raw_graphs:
            graph_id = str(raw_graph.get("id") or "").strip()
            if not graph_id or graph_id in raw_graphs_by_id:
                continue
            raw_graphs_by_id[graph_id] = raw_graph
            normalized_raw_graph = self._coerce_workflow_editor_graphs(
                [raw_graph],
                strict=False,
            )
            if normalized_raw_graph:
                normalized_raw_graphs_by_id[graph_id] = normalized_raw_graph[0].model_dump(
                    mode="json"
                )

        graph_payloads: list[dict[str, object]] = []
        for graph in normalized_graphs:
            graph_payload = graph.model_dump(mode="json")
            raw_graph = raw_graphs_by_id.get(graph.id)
            normalized_raw_graph = normalized_raw_graphs_by_id.get(graph.id)
            if raw_graph is not None and graph_payload == normalized_raw_graph:
                graph_payloads.append(json.loads(json.dumps(raw_graph)))
            else:
                graph_payloads.append(graph_payload)
        return graph_payloads

    def _compose_workflow_editor_config(self, payload: dict[str, object]) -> dict[str, object]:
        candidate = json.loads(json.dumps(payload))
        if not isinstance(candidate, dict):
            return payload
        return _compose_configured_workflow_ir(project_dir=self.project_dir, payload=candidate)

    def _workflow_editor_pack_backed_save_paths(
        self,
        raw_config: dict[str, object],
    ) -> tuple[Path, list[object]] | None:
        workflow_packs = raw_config.get("workflow_packs")
        inline_ir = raw_config.get("workflow_ir")
        if not isinstance(workflow_packs, list) or not workflow_packs:
            return None
        if isinstance(inline_ir, dict):
            raise ValueError(
                "Workflow editor cannot save configs that combine workflow_packs with inline workflow_ir; "
                "edit the pack files directly."
            )
        if len(workflow_packs) != 1:
            raise ValueError(
                "Workflow editor can only save a config backed by one workflow pack; edit multi-pack configs directly."
            )
        return self._workflow_editor_pack_path(workflow_packs[0]), workflow_packs

    def _write_workflow_editor_pack_config(
        self,
        *,
        config_path: Path,
        pack_path: Path,
        workflow_packs: list[object],
        validated_config: dict[str, object],
        graph_payloads: list[dict[str, object]] | None,
    ) -> None:
        if pack_path.exists():
            pack_payload = json.loads(pack_path.read_text(encoding="utf-8"))
            if not isinstance(pack_payload, dict):
                raise ValueError(f"workflow pack file {pack_path} must contain a JSON object")
        else:
            raise ValueError(f"workflow pack file {pack_path} does not exist")

        existing_ir = pack_payload.get("ir")
        ir_version = "v1"
        if isinstance(existing_ir, dict):
            ir_version = str(existing_ir.get("version") or "v1")
        nodes: list[object] = []
        for key in ("triggers", "gates", "effects", "queries"):
            values = validated_config.get(key, [])
            if isinstance(values, list):
                nodes.extend(values)
        pack_payload["ir"] = {
            "version": ir_version,
            "nodes": nodes,
            "edges": validated_config.get("edges", []),
        }
        if graph_payloads is not None:
            pack_payload["graphs"] = graph_payloads

        graph_keys = {"triggers", "gates", "effects", "queries", "edges"}
        wrapper_config = {
            key: value for key, value in validated_config.items() if key not in graph_keys
        }
        wrapper_config["workflow_packs"] = workflow_packs
        wrapper_config.pop("workflow_ir", None)
        wrapper_config.pop("workflow_graphs", None)

        self._write_json_file_atomic(config_path, wrapper_config)
        self._write_json_file_atomic(pack_path, pack_payload)

    def _write_json_file_atomic(self, path: Path, payload: dict[str, object]) -> None:
        encoded = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(encoded)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        try:
            os.replace(temp_path, path)
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def _read_workflow_editor_document(self) -> WorkflowEditorDocumentPayload:
        config_path = self._worker_global_config_path("trigger-orchestrator")
        if config_path is None:
            raise ValueError("Unknown worker: trigger-orchestrator")
        config_exists = config_path.exists()
        if config_exists:
            raw_config = json.loads(config_path.read_text(encoding="utf-8"))
            if not isinstance(raw_config, dict):
                raise ValueError(f"worker config file {config_path} must contain a JSON object")
        else:
            model = _WORKER_GLOBAL_CONFIG_MODELS["trigger-orchestrator"]
            raw_config = model.model_validate(self.config.model_dump(mode="json")).model_dump(
                mode="json", exclude_none=True
            )
        composed_config = self._compose_workflow_editor_config(raw_config)
        composed_config = self._strip_system_managed_workflows(composed_config)
        normalized_candidate = self._normalize_workflow_editor_config(composed_config)
        normalized_config = self._validate_worker_global_config_payload(
            "trigger-orchestrator", normalized_candidate
        )
        layout_path = self._workflow_editor_layout_path(raw_config)
        layout_exists = layout_path.exists()
        raw_layout: dict[str, object]
        if layout_exists:
            loaded_layout = json.loads(layout_path.read_text(encoding="utf-8"))
            if not isinstance(loaded_layout, dict):
                raise ValueError(f"workflow layout file {layout_path} must contain a JSON object")
            raw_layout = loaded_layout
        else:
            raw_layout = {}
        layout = self._validate_workflow_editor_layout(
            raw_layout,
            node_ids=self._collect_workflow_node_ids(normalized_config),
            allow_unknown_nodes=True,
        )
        graphs = self._workflow_editor_graphs_for_config(
            config=normalized_config,
            graphs=self._workflow_editor_stored_graphs(raw_config),
        )
        saved_at = (
            datetime.fromtimestamp(config_path.stat().st_mtime, tz=UTC) if config_exists else None
        )
        revision = self._workflow_editor_revision(normalized_config) if config_exists else None
        return WorkflowEditorDocumentPayload(
            config_path=str(config_path),
            layout_path=str(layout_path),
            config_exists=config_exists,
            layout_exists=layout_exists,
            config=normalized_config,
            layout=layout,
            graphs=graphs,
            output_schemas=derive_workflow_output_schemas(normalized_config),
            saved_at=saved_at,
            revision=revision,
        )

    def _read_workflow_editor_catalog(self) -> WorkflowEditorCatalogPayload:
        skill_options = [_field_option(skill.name) for skill in self.skill_library.list_skills()]
        tool_options = _tool_field_options()
        nodes: list[WorkflowEditorNodeCatalogEntry] = []
        for entry in _WORKFLOW_EDITOR_NODE_CATALOG:
            node = entry.model_copy(
                deep=True,
                update={
                    "description": (
                        entry.description or _WORKFLOW_EDITOR_NODE_DESCRIPTIONS.get(entry.type)
                    )
                },
            )
            if node.type in {"effect:vm:create", "effect:agent:create"}:
                node.fields = [
                    field.model_copy(update={"options": skill_options})
                    if field.key == "codex_skills"
                    else field.model_copy(update={"options": tool_options})
                    if field.key == "allowed_tools"
                    else field
                    for field in node.fields
                ]
            nodes.append(node)
        return WorkflowEditorCatalogPayload(nodes=nodes)

    def _validate_workflow_editor_document(
        self,
        *,
        config: dict[str, object],
        layout: dict[str, object] | None = None,
        graphs: list[dict[str, object]] | None = None,
        base_config: dict[str, object] | None = None,
    ) -> WorkflowEditorValidationPayload:
        merged_config = self._merge_system_managed_workflows(
            editable_config=config,
            base_config=base_config,
        )
        normalized_candidate = self._normalize_workflow_editor_config(merged_config)
        normalized_config = self._validate_worker_global_config_payload(
            "trigger-orchestrator", normalized_candidate
        )
        compiled_config, diagnostics = compile_worker_global_config_with_diagnostics(
            TriggerOrchestratorWorkerGlobalConfig.model_validate(normalized_config)
        )
        normalized_config = _strip_default_edge_fields(
            compiled_config.model_dump(mode="json", exclude_none=True)
        )
        editable_normalized_config = self._strip_system_managed_workflows(normalized_config)
        normalized_layout = self._validate_workflow_editor_layout(
            layout or {},
            node_ids=self._collect_workflow_node_ids(editable_normalized_config),
        )
        graph_source: object = graphs
        if graph_source is None and isinstance(base_config, dict):
            graph_source = self._workflow_editor_stored_graphs(base_config)
        normalized_graphs = self._workflow_editor_graphs_for_config(
            config=editable_normalized_config,
            graphs=graph_source,
            strict=graphs is not None,
        )
        output_schemas = derive_workflow_output_schemas(editable_normalized_config)
        binding_diagnostics = _workflow_binding_diagnostics_for_graphs(
            editable_normalized_config,
            normalized_graphs,
        )
        return WorkflowEditorValidationPayload(
            config=editable_normalized_config,
            layout=normalized_layout,
            graphs=normalized_graphs,
            output_schemas=output_schemas,
            validation_diagnostics=[
                {
                    **diagnostic.model_dump(mode="json", exclude_none=True),
                    "code": "domain_validation",
                }
                for diagnostic in diagnostics
            ]
            + [
                diagnostic.model_dump(mode="json", exclude_none=True)
                for diagnostic in binding_diagnostics
            ],
        )

    def _save_workflow_editor_document(
        self,
        *,
        config: dict[str, object],
        layout: dict[str, object] | None = None,
        graphs: list[dict[str, object]] | None = None,
    ) -> WorkflowEditorSavePayload:
        config_path = self._worker_global_config_path("trigger-orchestrator")
        if config_path is None:
            raise ValueError("Unknown worker: trigger-orchestrator")
        raw_config: dict[str, object] = {}
        if config_path.exists():
            loaded_config = json.loads(config_path.read_text(encoding="utf-8"))
            if not isinstance(loaded_config, dict):
                raise ValueError(f"worker config file {config_path} must contain a JSON object")
            raw_config = loaded_config

        validation = self._validate_workflow_editor_document(
            config=config, layout=layout, graphs=graphs, base_config=raw_config
        )
        merged_config = self._merge_system_managed_workflows(
            editable_config=validation.config,
            base_config=raw_config,
        )
        existing_editable_config = self._strip_system_managed_workflows(
            self._compose_workflow_editor_config(raw_config)
        )
        self._seed_new_ticket_created_trigger_cursors(
            previous_config=existing_editable_config,
            next_config=validation.config,
        )
        layout_path = self._workflow_editor_layout_path(raw_config)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        layout_path.parent.mkdir(parents=True, exist_ok=True)
        stored_graphs = self._workflow_editor_stored_graphs(raw_config)
        graphs_to_persist = validation.graphs if graphs is not None or stored_graphs else None
        graph_payloads_to_persist = (
            self._workflow_editor_graph_payloads_to_persist(
                raw_graphs=stored_graphs,
                normalized_graphs=graphs_to_persist,
            )
            if graphs_to_persist is not None
            else None
        )
        pack_save = self._workflow_editor_pack_backed_save_paths(raw_config)
        if pack_save is None:
            if graph_payloads_to_persist is not None:
                merged_config["workflow_graphs"] = graph_payloads_to_persist
            else:
                merged_config.pop("workflow_graphs", None)
            self._write_json_file_atomic(config_path, merged_config)
        else:
            pack_path, workflow_packs = pack_save
            pack_path.parent.mkdir(parents=True, exist_ok=True)
            self._write_workflow_editor_pack_config(
                config_path=config_path,
                pack_path=pack_path,
                workflow_packs=workflow_packs,
                validated_config=merged_config,
                graph_payloads=graph_payloads_to_persist,
            )
        self._write_json_file_atomic(layout_path, validation.layout)
        saved_at = datetime.fromtimestamp(config_path.stat().st_mtime, tz=UTC)
        revision = self._workflow_editor_revision(validation.config)
        return WorkflowEditorSavePayload(
            config_path=str(config_path),
            layout_path=str(layout_path),
            config=validation.config,
            layout=validation.layout,
            graphs=validation.graphs,
            output_schemas=validation.output_schemas,
            validation_diagnostics=validation.validation_diagnostics,
            saved_at=saved_at,
            revision=revision,
        )

    def _seed_new_ticket_created_trigger_cursors(
        self, *, previous_config: dict[str, object], next_config: dict[str, object]
    ) -> None:
        previous_trigger_ids = _ticket_created_trigger_ids(previous_config)
        new_triggers = [
            trigger
            for trigger in _ticket_created_trigger_definitions(next_config)
            if trigger["id"] not in previous_trigger_ids
        ]
        if not new_triggers:
            return

        runtime_dir = _resolve_path_setting(
            base_dir=self.project_dir,
            value=next_config.get("runtime_dir"),
            fallback=self.config.runtime_dir,
        )
        issues = JsonStore(runtime_dir / "local-issues.json", LocalIssueRecord).read_all()
        if not issues:
            return

        cursor_path = runtime_dir / "trigger-cursors-ticket-created.json"
        cursor_payload = _read_json_object(cursor_path)
        changed = False
        for trigger in new_triggers:
            trigger_id = trigger["id"]
            if isinstance(cursor_payload.get(trigger_id), dict):
                continue
            max_created_at = _max_ticket_created_at_for_directories(
                issues.values(), trigger["directories"]
            )
            if max_created_at is None:
                continue
            cursor_payload[trigger_id] = {
                "last_created_at": max_created_at.isoformat().replace("+00:00", "Z")
            }
            changed = True
        if changed:
            cursor_path.parent.mkdir(parents=True, exist_ok=True)
            self._write_json_file_atomic(cursor_path, cursor_payload)

    def _read_workflow_run_snapshot(self) -> WorkflowRunSnapshotPayload:
        stores = self._workflow_run_store_paths()
        runtime_dir = stores["runtime_dir"]
        event_queue_store_path = stores["event_queue_store_path"]
        effect_execution_store_path = stores["effect_execution_store_path"]
        gate_execution_store_path = stores["gate_execution_store_path"]
        runs_by_event: dict[str, dict[str, object]] = {}
        node_activity: dict[str, dict[str, int]] = {}

        def ensure_run(event_id: str) -> dict[str, object]:
            existing = runs_by_event.get(event_id)
            if existing is not None:
                return existing
            created: dict[str, object] = {
                "event_id": event_id,
                "active_nodes": [],
            }
            runs_by_event[event_id] = created
            return created

        def register_node_activity(
            *,
            event_id: str,
            node_id: str,
            status: str,
            source: str,
            trace_id: str | None = None,
            source_node_id: str | None = None,
            source_trigger_type: str | None = None,
            delivery_state: str | None = None,
            occurred_at: datetime | None = None,
            started_at: datetime | None = None,
            finished_at: datetime | None = None,
            dispatch_id: str | None = None,
            error: str | None = None,
            resumable: bool = False,
        ) -> None:
            normalized_node_id = str(node_id or "").strip()
            if not normalized_node_id:
                return
            run = ensure_run(event_id)
            if trace_id and not run.get("trace_id"):
                run["trace_id"] = trace_id
            if source_node_id and not run.get("source_node_id"):
                run["source_node_id"] = source_node_id
            if source_trigger_type and not run.get("source_trigger_type"):
                run["source_trigger_type"] = source_trigger_type
            if delivery_state and not run.get("delivery_state"):
                run["delivery_state"] = delivery_state
            if occurred_at is not None and run.get("occurred_at") is None:
                run["occurred_at"] = occurred_at
            latest_activity_at = max(
                (value for value in (finished_at, started_at, occurred_at) if value is not None),
                default=None,
            )
            previous_latest = run.get("latest_activity_at")
            if latest_activity_at is not None and (
                not isinstance(previous_latest, datetime) or latest_activity_at > previous_latest
            ):
                run["latest_activity_at"] = latest_activity_at
            if status == "failed":
                run["run_state"] = "errored"
                previous_failed_at = run.get("failed_at")
                if latest_activity_at is not None and (
                    not isinstance(previous_failed_at, datetime)
                    or latest_activity_at >= previous_failed_at
                ):
                    run["failed_node_id"] = normalized_node_id
                    run["failed_node_status"] = status
                    run["failed_at"] = latest_activity_at
                    run["error"] = error
                    run["resumable"] = resumable
            elif run.get("run_state") != "errored":
                run["run_state"] = "active"
            active_nodes = run.setdefault("active_nodes", [])
            assert isinstance(active_nodes, list)
            active_nodes.append(
                WorkflowRunNodeActivityPayload(
                    node_id=normalized_node_id,
                    status=status,
                    source=source,
                    started_at=started_at,
                    finished_at=finished_at,
                    dispatch_id=dispatch_id,
                    error=error,
                )
            )
            node_status_counts = node_activity.setdefault(normalized_node_id, {})
            node_status_counts[status] = node_status_counts.get(status, 0) + 1

        if self._workflow_uses_sqlite_store(stores):
            queue_store, effect_store, gate_store = self._workflow_sqlite_store_bundle(stores)
            queue_payload = {
                record.event_id: record.model_dump(mode="json")
                for record in queue_store.list_active_records()
            }
            execution_payload = {
                record.execution_id: record.model_dump(mode="json")
                for record in effect_store.list_active_records()
            }
            gate_payload = {
                record.execution_id: record.model_dump(mode="json")
                for record in gate_store.list_active_records()
            }
        else:
            queue_payload = self.trigger_event_repository.read_events(event_queue_store_path)
            execution_payload = _read_json_object(effect_execution_store_path)
            gate_payload = _read_json_object(gate_execution_store_path)

        for event_id, raw_record in queue_payload.items():
            if not isinstance(raw_record, dict):
                continue
            delivery_state = str(raw_record.get("delivery_state") or "").strip()
            if delivery_state == "delivered":
                continue
            normalized_event_id = str(raw_record.get("event_id") or event_id).strip()
            source_node_id = str(raw_record.get("trigger_id") or "").strip()
            register_node_activity(
                event_id=normalized_event_id,
                node_id=source_node_id,
                status=delivery_state or "pending",
                source="queue",
                trace_id=_coerce_optional_string(raw_record.get("trace_id")),
                source_node_id=source_node_id,
                source_trigger_type=_coerce_optional_string(raw_record.get("trigger_type")),
                delivery_state=delivery_state or None,
                occurred_at=_coerce_optional_datetime(raw_record.get("occurred_at")),
                started_at=_coerce_optional_datetime(raw_record.get("inflight_at")),
                finished_at=_coerce_optional_datetime(raw_record.get("delivered_at")),
            )

        for execution_id, raw_record in execution_payload.items():
            if not isinstance(raw_record, dict):
                continue
            status = str(raw_record.get("status") or "").strip()
            if raw_record.get("dismissed_at"):
                continue
            if status not in {"running", "waiting", "failed"}:
                continue
            event_id = str(raw_record.get("event_id") or execution_id).strip()
            node_id = str(raw_record.get("effect_id") or "").strip()
            register_node_activity(
                event_id=event_id,
                node_id=node_id,
                status=status,
                source="effect_execution",
                trace_id=_coerce_optional_string(raw_record.get("trace_id")),
                source_node_id=_coerce_optional_string(raw_record.get("source_node_id")),
                source_trigger_type=_coerce_optional_string(raw_record.get("source_trigger_type")),
                started_at=_coerce_optional_datetime(raw_record.get("started_at")),
                finished_at=_coerce_optional_datetime(raw_record.get("finished_at")),
                error=_coerce_optional_string(raw_record.get("error")),
                resumable=bool(
                    _coerce_optional_string(raw_record.get("source_node_id"))
                    and isinstance(raw_record.get("input_payload"), dict)
                ),
            )

        for execution_id, raw_record in gate_payload.items():
            if not isinstance(raw_record, dict):
                continue
            status = str(raw_record.get("status") or "").strip()
            if raw_record.get("dismissed_at"):
                continue
            if status not in {"running", "waiting", "failed"}:
                continue
            event_id = str(raw_record.get("event_id") or execution_id).strip()
            node_id = str(raw_record.get("gate_id") or "").strip()
            register_node_activity(
                event_id=event_id,
                node_id=node_id,
                status=status,
                source="gate_execution",
                trace_id=_coerce_optional_string(raw_record.get("trace_id")),
                source_node_id=_coerce_optional_string(raw_record.get("source_node_id")),
                source_trigger_type=_coerce_optional_string(raw_record.get("source_trigger_type")),
                started_at=_coerce_optional_datetime(raw_record.get("started_at")),
                finished_at=_coerce_optional_datetime(raw_record.get("finished_at")),
                error=_coerce_optional_string(raw_record.get("error")),
                resumable=bool(
                    _coerce_optional_string(raw_record.get("source_node_id"))
                    and isinstance(raw_record.get("input_payload"), dict)
                ),
            )

        await_agent_payload = _read_json_object(
            runtime_dir / "providers" / "gates" / "await-agent" / "pending.json"
        )
        if not await_agent_payload:
            await_agent_payload = _read_json_object(runtime_dir / "await-agent-pending.json")
        for record_key, raw_record in await_agent_payload.items():
            if not isinstance(raw_record, dict):
                continue
            gate_id = str(raw_record.get("gate_id") or "").strip()
            dispatch_id = _coerce_optional_string(raw_record.get("dispatch_id"))
            trace_id = _coerce_optional_string(raw_record.get("trace_id"))
            prompt_event_id = (
                _coerce_optional_string(raw_record.get("prompt_event_id")) or record_key
            )
            register_node_activity(
                event_id=prompt_event_id,
                node_id=gate_id,
                status="waiting",
                source="await_agent",
                trace_id=trace_id,
                source_node_id=_coerce_optional_string(raw_record.get("prompt_source_node_id")),
                source_trigger_type="trigger:agent:run:completed",
                started_at=_coerce_optional_datetime(raw_record.get("occurred_at")),
                dispatch_id=dispatch_id,
            )

        runs = [
            WorkflowRunSummaryPayload.model_validate(
                {
                    **payload,
                    "active_nodes": _merge_run_node_activity(
                        payload.get("active_nodes", []),
                    ),
                }
            )
            for payload in runs_by_event.values()
            if payload.get("active_nodes")
        ]
        runs.sort(
            key=lambda run: (
                run.latest_activity_at or datetime.min.replace(tzinfo=UTC),
                run.occurred_at or datetime.min.replace(tzinfo=UTC),
                run.event_id,
            ),
            reverse=True,
        )
        return WorkflowRunSnapshotPayload(
            generated_at=_utc_now(),
            runs=runs,
            node_activity={
                node_id: dict(sorted(counts.items()))
                for node_id, counts in sorted(node_activity.items())
            },
        )

    def _clear_workflow_run(self, event_id: str) -> WorkflowRunClearPayload:
        normalized_event_id = str(event_id or "").strip()
        if not normalized_event_id:
            raise ValueError("Workflow run event_id is required.")

        stores = self._workflow_run_store_paths()
        event_queue_store_path = stores["event_queue_store_path"]
        effect_execution_store_path = stores["effect_execution_store_path"]
        gate_execution_store_path = stores["gate_execution_store_path"]
        await_agent_store_path = stores["await_agent_store_path"]
        legacy_await_agent_store_path = stores["legacy_await_agent_store_path"]

        cleared_at = _utc_now()
        clear_reason = "manual_clear_stale_workflow_runs"
        clear_error = "Manually cleared stale workflow run from editor state."

        if self._workflow_uses_sqlite_store(stores):
            queue_store, effect_store, gate_store = self._workflow_sqlite_store_bundle(stores)
            queue_records_updated = queue_store.mark_event_cleared(
                normalized_event_id,
                cleared_at=cleared_at,
                clear_reason=clear_reason,
                error=clear_error,
            )
            effect_records_updated = effect_store.clear_event_records(
                normalized_event_id,
                cleared_at=cleared_at,
                clear_reason=clear_reason,
                clear_error=clear_error,
            )
            gate_records_updated = gate_store.clear_event_records(
                normalized_event_id,
                cleared_at=cleared_at,
                clear_reason=clear_reason,
                clear_error=clear_error,
            )
        else:
            def _clear_queue_records(records: dict[str, TriggerQueueRecord]) -> int:
                updated = 0
                for record_key, record in records.items():
                    record_event_id = str(record.event_id or record_key).strip()
                    if record_event_id != normalized_event_id:
                        continue
                    if record.delivery_state == "delivered":
                        continue
                    record.delivery_state = "delivered"
                    record.delivered_at = cleared_at
                    record.clear_reason = clear_reason
                    record.error = clear_error
                    records[record_key] = record
                    updated += 1
                return updated

            from agent_sandbox.daemons.effect_orchestrator import (
                JsonExecutionStore,
                JsonGateExecutionStore,
            )

            queue_records_updated = JsonStore(
                event_queue_store_path, TriggerQueueRecord
            ).update_all(_clear_queue_records)

            def _clear_execution_records(records: dict[str, Any]) -> int:
                updated = 0
                for execution_id, record in records.items():
                    record_event_id = str(getattr(record, "event_id", execution_id) or "").strip()
                    if record_event_id != normalized_event_id:
                        continue
                    status = str(getattr(record, "status", "") or "").strip()
                    if status == "failed":
                        record.dismissed_at = cleared_at
                        record.dismiss_reason = "manual_dismiss_errored_workflow_run"
                        records[execution_id] = record
                        updated += 1
                        continue
                    if status not in {"running", "waiting"}:
                        continue
                    record.status = "failed"
                    record.finished_at = cleared_at
                    record.cleared_at = cleared_at
                    record.clear_reason = clear_reason
                    record.error = clear_error
                    record.dismissed_at = cleared_at
                    record.dismiss_reason = "manual_clear_stale_workflow_runs"
                    records[execution_id] = record
                    updated += 1
                return updated

            effect_records_updated = JsonExecutionStore(effect_execution_store_path).update_all(
                _clear_execution_records
            )
            gate_records_updated = JsonGateExecutionStore(gate_execution_store_path).update_all(
                _clear_execution_records
            )

        await_agent_records_removed = 0
        await_agent_payload = _read_json_object(await_agent_store_path)
        matching_await_keys = [
            record_key
            for record_key, raw_record in await_agent_payload.items()
            if isinstance(raw_record, dict)
            and (
                (_coerce_optional_string(raw_record.get("prompt_event_id")) or record_key)
                == normalized_event_id
            )
        ]
        for record_key in matching_await_keys:
            del await_agent_payload[record_key]
            await_agent_records_removed += 1

        legacy_await_agent_payload = _read_json_object(legacy_await_agent_store_path)
        legacy_matching_await_keys = [
            record_key
            for record_key, raw_record in legacy_await_agent_payload.items()
            if isinstance(raw_record, dict)
            and (
                (_coerce_optional_string(raw_record.get("prompt_event_id")) or record_key)
                == normalized_event_id
            )
        ]
        for record_key in legacy_matching_await_keys:
            del legacy_await_agent_payload[record_key]
            await_agent_records_removed += 1

        if (
            queue_records_updated == 0
            and effect_records_updated == 0
            and gate_records_updated == 0
            and await_agent_records_removed == 0
        ):
            raise ValueError(f"Workflow run not found or already inactive: {normalized_event_id}")

        await_agent_store_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_json_file_atomic(await_agent_store_path, await_agent_payload)
        if legacy_await_agent_store_path.exists() or legacy_await_agent_payload:
            legacy_await_agent_store_path.parent.mkdir(parents=True, exist_ok=True)
            self._write_json_file_atomic(legacy_await_agent_store_path, legacy_await_agent_payload)
        self._workflow_event_journal().append(
            "workflow.run.cleared",
            source="workflow_editor",
            subject_id=normalized_event_id,
            payload={
                "queue_records_updated": queue_records_updated,
                "effect_records_updated": effect_records_updated,
                "gate_records_updated": gate_records_updated,
                "await_agent_records_removed": await_agent_records_removed,
            },
        )
        return WorkflowRunClearPayload(
            event_id=normalized_event_id,
            cleared_at=cleared_at,
            queue_records_updated=queue_records_updated,
            effect_records_updated=effect_records_updated,
            gate_records_updated=gate_records_updated,
            await_agent_records_removed=await_agent_records_removed,
        )

    def _resume_workflow_run(self, event_id: str) -> WorkflowRunResumePayload:
        normalized_event_id = str(event_id or "").strip()
        if not normalized_event_id:
            raise ValueError("Workflow run event_id is required.")

        stores = self._workflow_run_store_paths()
        event_queue_store_path = stores["event_queue_store_path"]
        effect_execution_store_path = stores["effect_execution_store_path"]
        gate_execution_store_path = stores["gate_execution_store_path"]

        queue_store = None
        if self._workflow_uses_sqlite_store(stores):
            queue_store, effect_store, gate_store = self._workflow_sqlite_store_bundle(stores)
            failed_record = self._latest_resumable_failed_workflow_record(
                event_id=normalized_event_id,
                effect_payload={
                    record.execution_id: record.model_dump(mode="json")
                    for record in effect_store.list_records_for_event(
                        normalized_event_id, statuses={"failed"}
                    )
                },
                gate_payload={
                    record.execution_id: record.model_dump(mode="json")
                    for record in gate_store.list_records_for_event(
                        normalized_event_id, statuses={"failed"}
                    )
                },
            )
        else:
            failed_record = self._latest_resumable_failed_workflow_record(
                event_id=normalized_event_id,
                effect_payload=_read_json_object(effect_execution_store_path),
                gate_payload=_read_json_object(gate_execution_store_path),
            )
        if failed_record is None:
            raise ValueError(f"Workflow run is not resumable: {normalized_event_id}")

        resumed_at = _utc_now()
        source_node_id = str(failed_record["source_node_id"])
        source_trigger_type = str(failed_record["source_trigger_type"])
        target_node_id = str(failed_record["node_id"])
        input_payload = dict(failed_record["input_payload"])
        input_payload["__resume_target_node_id"] = target_node_id
        input_payload["__resume_source_event_id"] = normalized_event_id
        resume_event_id = f"{normalized_event_id}:resume:{uuid4().hex[:12]}"

        resume_event = TriggerEvent(
            event_id=resume_event_id,
            trigger_id=source_node_id,
            trigger_type=source_trigger_type,
            trace_id=str(failed_record["trace_id"]),
            occurred_at=resumed_at,
            payload=input_payload,
        )
        if queue_store is not None:
            queue_store.publish(resume_event)
        else:
            TriggerEventQueue(event_queue_store_path).publish(resume_event)
        self._workflow_event_journal().append(
            "workflow.run.resumed",
            source="workflow_editor",
            subject_id=normalized_event_id,
            payload={
                "resume_event_id": resume_event_id,
                "source_node_id": source_node_id,
                "target_node_id": target_node_id,
            },
        )
        return WorkflowRunResumePayload(
            event_id=normalized_event_id,
            resumed_at=resumed_at,
            resume_event_id=resume_event_id,
            source_node_id=source_node_id,
            target_node_id=target_node_id,
        )

    def _workflow_run_store_paths(self) -> dict[str, Any]:
        worker_payload = self._read_worker_global_config("trigger-orchestrator")
        worker_config = worker_payload.config
        runtime_dir = _resolve_path_setting(
            base_dir=self.project_dir,
            value=worker_config.get("runtime_dir"),
            fallback=self.config.runtime_dir,
        )
        backend = (
            str(worker_config.get("orchestration_store_backend") or "sqlite").strip().lower()
        )
        if backend not in {"sqlite", "json"}:
            backend = "sqlite"
        backend_explicit = False
        try:
            worker_config_path = Path(worker_payload.path)
            if worker_config_path.exists():
                raw_worker_config = json.loads(worker_config_path.read_text(encoding="utf-8"))
                backend_explicit = (
                    isinstance(raw_worker_config, dict)
                    and "orchestration_store_backend" in raw_worker_config
                )
        except (OSError, json.JSONDecodeError):
            backend_explicit = False
        orchestration_store_path = _resolve_path_setting(
            base_dir=self.project_dir,
            value=worker_config.get("orchestration_store_path"),
            fallback=runtime_dir / "orchestration.db",
        )
        event_queue_store_path = _resolve_path_setting(
            base_dir=self.project_dir,
            value=worker_config.get("event_queue_store_path"),
            fallback=runtime_dir / "trigger-orchestrator-events.json",
        )
        effect_execution_store_path = _resolve_path_setting(
            base_dir=self.project_dir,
            value=worker_config.get("effect_execution_store_path"),
            fallback=runtime_dir / "effect-orchestrator-executions.json",
        )
        gate_execution_store_path = _resolve_path_setting(
            base_dir=self.project_dir,
            value=worker_config.get("gate_execution_store_path"),
            fallback=runtime_dir / "gate-orchestrator-executions.json",
        )
        if (
            backend == "sqlite"
            and not backend_explicit
            and not orchestration_store_path.exists()
            and (
                event_queue_store_path.exists()
                or effect_execution_store_path.exists()
                or gate_execution_store_path.exists()
            )
        ):
            backend = "json"
        return {
            "runtime_dir": runtime_dir,
            "orchestration_store_backend": backend,
            "orchestration_store_path": orchestration_store_path,
            "orchestration_retention_days": int(
                worker_config.get("orchestration_retention_days")
                if worker_config.get("orchestration_retention_days") is not None
                else 30
            ),
            "event_queue_store_path": event_queue_store_path,
            "effect_execution_store_path": effect_execution_store_path,
            "gate_execution_store_path": gate_execution_store_path,
            "await_agent_store_path": runtime_dir
            / "providers"
            / "gates"
            / "await-agent"
            / "pending.json",
            "legacy_await_agent_store_path": runtime_dir / "await-agent-pending.json",
        }

    def _workflow_uses_sqlite_store(self, stores: Mapping[str, Any]) -> bool:
        return str(stores.get("orchestration_store_backend") or "").lower() == "sqlite"

    def _workflow_sqlite_store_bundle(self, stores: Mapping[str, Any]) -> tuple[Any, Any, Any]:
        from agent_sandbox.daemons.effect_orchestrator import (
            SqliteExecutionStore,
            SqliteGateExecutionStore,
        )

        db_path = Path(stores["orchestration_store_path"])
        raw_retention_days = stores.get("orchestration_retention_days")
        retention_days = int(raw_retention_days if raw_retention_days is not None else 30)
        journal = SqliteWorkflowEventJournal(db_path, retention_days=retention_days)
        return (
            SqliteTriggerEventQueue(db_path, journal=journal, retention_days=retention_days),
            SqliteExecutionStore(db_path, journal=journal, retention_days=retention_days),
            SqliteGateExecutionStore(db_path, journal=journal, retention_days=retention_days),
        )

    def _read_workflow_graph_activity_report(
        self,
        *,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
        limit: int | None = None,
    ) -> WorkflowGraphActivityReportPayload:
        now = _utc_now()
        end_at = (
            period_end.astimezone(UTC)
            if period_end is not None and period_end.tzinfo is not None
            else period_end.replace(tzinfo=UTC)
            if period_end is not None
            else now
        )
        start_at = (
            period_start.astimezone(UTC)
            if period_start is not None and period_start.tzinfo is not None
            else period_start.replace(tzinfo=UTC)
            if period_start is not None
            else end_at - timedelta(days=1)
        )
        if start_at > end_at:
            raise ValueError("Workflow graph activity period_start must be before period_end.")

        document = self._read_workflow_editor_document()
        node_metadata: dict[str, dict[str, str | None]] = {}
        for collection_name in ("triggers", "gates", "effects", "queries"):
            nodes = document.config.get(collection_name)
            if not isinstance(nodes, list):
                continue
            for raw_node in nodes:
                if not isinstance(raw_node, dict):
                    continue
                node_id = str(raw_node.get("id") or "").strip()
                if not node_id:
                    continue
                node_metadata[node_id] = {
                    "node_type": _coerce_optional_string(raw_node.get("type")),
                    "display_name": _coerce_optional_string(raw_node.get("display_name")),
                }

        graph_states: dict[str, dict[str, Any]] = {}
        node_to_graph_id: dict[str, str] = {}
        for graph in document.graphs:
            graph_id = graph.id.strip()
            if not graph_id:
                continue
            graph_states[graph_id] = {
                "graph": graph,
                "run_keys": set(),
                "activity_count": 0,
                "trigger_event_count": 0,
                "gate_execution_count": 0,
                "effect_execution_count": 0,
                "status_counts": {},
                "first_activity_at": None,
                "latest_activity_at": None,
                "nodes": {},
            }
            for node_id in graph.node_ids:
                node_to_graph_id.setdefault(node_id, graph_id)

        def in_period(timestamp: datetime | None) -> bool:
            return timestamp is not None and start_at <= timestamp <= end_at

        def update_bounds(state: dict[str, Any], timestamp: datetime) -> None:
            first_activity_at = state.get("first_activity_at")
            if not isinstance(first_activity_at, datetime) or timestamp < first_activity_at:
                state["first_activity_at"] = timestamp
            latest_activity_at = state.get("latest_activity_at")
            if not isinstance(latest_activity_at, datetime) or timestamp > latest_activity_at:
                state["latest_activity_at"] = timestamp

        def increment_status(counts: dict[str, int], status: str) -> None:
            counts[status] = counts.get(status, 0) + 1

        def register_activity(
            *,
            node_id: str,
            timestamp: datetime | None,
            source: str,
            status: str | None,
            run_key: str | None,
        ) -> None:
            normalized_node_id = node_id.strip()
            if not normalized_node_id or not in_period(timestamp):
                return
            graph_id = node_to_graph_id.get(normalized_node_id)
            if graph_id is None:
                return
            assert timestamp is not None
            state = graph_states[graph_id]
            state["activity_count"] += 1
            if source == "trigger":
                state["trigger_event_count"] += 1
            elif source == "gate":
                state["gate_execution_count"] += 1
            elif source == "effect":
                state["effect_execution_count"] += 1
            if run_key:
                state["run_keys"].add(run_key)
            normalized_status = (status or "unknown").strip() or "unknown"
            increment_status(state["status_counts"], normalized_status)
            update_bounds(state, timestamp)

            nodes = state["nodes"]
            node_state = nodes.setdefault(
                normalized_node_id,
                {
                    "activity_count": 0,
                    "status_counts": {},
                    "first_activity_at": None,
                    "latest_activity_at": None,
                },
            )
            node_state["activity_count"] += 1
            increment_status(node_state["status_counts"], normalized_status)
            update_bounds(node_state, timestamp)

        def latest_datetime(*values: object) -> datetime | None:
            parsed_values = [
                parsed for parsed in (_coerce_optional_datetime(value) for value in values) if parsed
            ]
            return max(parsed_values) if parsed_values else None

        stores = self._workflow_run_store_paths()
        if self._workflow_uses_sqlite_store(stores):
            queue_store, effect_store, gate_store = self._workflow_sqlite_store_bundle(stores)
            queue_payload = {
                record.event_id: record.model_dump(mode="json")
                for record in queue_store.list_activity_between(
                    start_at=start_at, end_at=end_at
                )
            }
            effect_payload = {
                record.execution_id: record.model_dump(mode="json")
                for record in effect_store.list_activity_between(
                    start_at=start_at, end_at=end_at
                )
            }
            gate_payload = {
                record.execution_id: record.model_dump(mode="json")
                for record in gate_store.list_activity_between(start_at=start_at, end_at=end_at)
            }
        else:
            queue_payload = _read_json_object(stores["event_queue_store_path"])
            effect_payload = _read_json_object(stores["effect_execution_store_path"])
            gate_payload = _read_json_object(stores["gate_execution_store_path"])

        for record_key, raw_record in queue_payload.items():
            if not isinstance(raw_record, dict):
                continue
            event_id = _coerce_optional_string(raw_record.get("event_id")) or record_key
            trace_id = _coerce_optional_string(raw_record.get("trace_id"))
            register_activity(
                node_id=str(raw_record.get("trigger_id") or ""),
                timestamp=(
                    _coerce_optional_datetime(raw_record.get("occurred_at"))
                    or latest_datetime(raw_record.get("delivered_at"), raw_record.get("inflight_at"))
                ),
                source="trigger",
                status=_coerce_optional_string(raw_record.get("delivery_state")) or "event",
                run_key=trace_id or event_id,
            )

        for execution_id, raw_record in effect_payload.items():
            if not isinstance(raw_record, dict):
                continue
            event_id = _coerce_optional_string(raw_record.get("event_id")) or execution_id
            trace_id = _coerce_optional_string(raw_record.get("trace_id"))
            register_activity(
                node_id=str(raw_record.get("effect_id") or ""),
                timestamp=latest_datetime(raw_record.get("finished_at"), raw_record.get("started_at")),
                source="effect",
                status=_coerce_optional_string(raw_record.get("status")),
                run_key=trace_id or event_id,
            )

        for execution_id, raw_record in gate_payload.items():
            if not isinstance(raw_record, dict):
                continue
            event_id = _coerce_optional_string(raw_record.get("event_id")) or execution_id
            trace_id = _coerce_optional_string(raw_record.get("trace_id"))
            register_activity(
                node_id=str(raw_record.get("gate_id") or ""),
                timestamp=latest_datetime(raw_record.get("finished_at"), raw_record.get("started_at")),
                source="gate",
                status=_coerce_optional_string(raw_record.get("status")),
                run_key=trace_id or event_id,
            )

        graphs: list[WorkflowGraphActivityGraphPayload] = []
        for graph_id, state in graph_states.items():
            graph = state["graph"]
            node_payloads: list[WorkflowGraphActivityNodePayload] = []
            for node_id, node_state in state["nodes"].items():
                metadata = node_metadata.get(node_id, {})
                node_payloads.append(
                    WorkflowGraphActivityNodePayload(
                        node_id=node_id,
                        node_type=metadata.get("node_type"),
                        display_name=metadata.get("display_name"),
                        activity_count=node_state["activity_count"],
                        status_counts=dict(sorted(node_state["status_counts"].items())),
                        first_activity_at=node_state.get("first_activity_at"),
                        latest_activity_at=node_state.get("latest_activity_at"),
                    )
                )
            node_payloads.sort(
                key=lambda node: (
                    node.activity_count,
                    node.latest_activity_at or datetime.min.replace(tzinfo=UTC),
                    node.display_name or node.node_id,
                ),
                reverse=True,
            )
            graphs.append(
                WorkflowGraphActivityGraphPayload(
                    graph_id=graph_id,
                    name=graph.name,
                    description=graph.description,
                    directory_paths=graph.directory_paths,
                    user_ids=graph.user_ids,
                    node_ids=graph.node_ids,
                    node_count=len(graph.node_ids),
                    run_count=len(state["run_keys"]),
                    activity_count=state["activity_count"],
                    trigger_event_count=state["trigger_event_count"],
                    gate_execution_count=state["gate_execution_count"],
                    effect_execution_count=state["effect_execution_count"],
                    status_counts=dict(sorted(state["status_counts"].items())),
                    first_activity_at=state.get("first_activity_at"),
                    latest_activity_at=state.get("latest_activity_at"),
                    nodes=node_payloads,
                )
            )
        graphs.sort(
            key=lambda graph: (
                graph.activity_count,
                graph.run_count,
                graph.latest_activity_at or datetime.min.replace(tzinfo=UTC),
                graph.name,
            ),
            reverse=True,
        )
        if limit is not None and limit > 0:
            graphs = graphs[:limit]

        return WorkflowGraphActivityReportPayload(
            generated_at=now,
            period_start=start_at,
            period_end=end_at,
            graphs=graphs,
        )

    def _read_conformance_reports(self) -> ConformanceReportsPayload:
        now = _utc_now()
        specs = (
            {
                "kind": "trigger",
                "label": "Trigger",
                "artifact_path": "trigger-conformance-report.json",
                "guidance": TRIGGER_CONFORMANCE_GUIDANCE,
            },
            {
                "kind": "effect",
                "label": "Effect",
                "artifact_path": "effect-conformance-report.json",
                "guidance": EFFECT_CONFORMANCE_GUIDANCE,
            },
        )
        return ConformanceReportsPayload(
            generated_at=now,
            stale_after_seconds=int(CONFORMANCE_REPORT_STALE_AFTER.total_seconds()),
            reports=[self._read_conformance_report(now=now, **spec) for spec in specs],
        )

    def _read_conformance_report(
        self,
        *,
        kind: str,
        label: str,
        artifact_path: str,
        guidance: str,
        now: datetime,
    ) -> ConformanceReportPayload:
        path = self.config.runtime_dir / artifact_path

        def report(
            state: str,
            *,
            status: str | None = None,
            generated_at: datetime | None = None,
            requirement_counts: dict[str, int] | None = None,
            requirement_count: int = 0,
            missing_coverage_count: int = 0,
            missing_coverage: dict[str, list[str]] | None = None,
            traceability_task_count: int = 0,
            failing_requirements: list[ConformanceRequirementSnippetPayload] | None = None,
            remediation_snippets: list[ConformanceRequirementSnippetPayload] | None = None,
            error: str | None = None,
        ) -> ConformanceReportPayload:
            return ConformanceReportPayload(
                kind=kind,
                label=label,
                artifact_path=artifact_path,
                state=state,
                status=status,
                generated_at=generated_at,
                requirement_counts=requirement_counts or {},
                requirement_count=requirement_count,
                missing_coverage_count=missing_coverage_count,
                missing_coverage=missing_coverage or {},
                traceability_task_count=traceability_task_count,
                failing_requirements=failing_requirements or [],
                remediation_snippets=remediation_snippets or [],
                error=error,
                guidance=guidance,
            )

        if not path.exists():
            return report("missing", error=f"{artifact_path} has not been generated yet.")

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return report("invalid", error=f"{artifact_path} is not valid JSON: {exc.msg}.")
        except OSError as exc:
            return report("invalid", error=f"{artifact_path} could not be read: {exc}.")

        if not isinstance(payload, dict):
            return report("invalid", error=f"{artifact_path} must contain a JSON object.")

        raw_generated_at = payload.get("generated_at")
        generated_at = _coerce_optional_datetime(raw_generated_at)
        if generated_at is None:
            return report("invalid", error=f"{artifact_path} is missing a valid generated_at.")

        status = (_coerce_optional_string(payload.get("status")) or "").lower()
        if status not in {"pass", "fail"}:
            return report(
                "invalid",
                generated_at=generated_at,
                error=f"{artifact_path} has unsupported status {payload.get('status')!r}.",
            )

        raw_requirements = payload.get("requirements")
        if not isinstance(raw_requirements, list):
            return report(
                "invalid",
                status=status,
                generated_at=generated_at,
                error=f"{artifact_path} requirements must be a list.",
            )
        raw_traceability_matrix = payload.get("traceability_matrix")
        if not isinstance(raw_traceability_matrix, dict):
            return report(
                "invalid",
                status=status,
                generated_at=generated_at,
                error=f"{artifact_path} traceability_matrix must be an object.",
            )
        raw_missing_coverage = payload.get("missing_coverage")
        if not isinstance(raw_missing_coverage, dict):
            return report(
                "invalid",
                status=status,
                generated_at=generated_at,
                error=f"{artifact_path} missing_coverage must be an object.",
            )

        requirement_counts: dict[str, int] = {}
        failing_requirements: list[ConformanceRequirementSnippetPayload] = []
        remediation_snippets: list[ConformanceRequirementSnippetPayload] = []
        for index, raw_requirement in enumerate(raw_requirements):
            if not isinstance(raw_requirement, dict):
                return report(
                    "invalid",
                    status=status,
                    generated_at=generated_at,
                    error=f"{artifact_path} requirements[{index}] must be an object.",
                )
            requirement_status = (
                _coerce_optional_string(raw_requirement.get("status")) or "unknown"
            ).lower()
            requirement_counts[requirement_status] = (
                requirement_counts.get(requirement_status, 0) + 1
            )
            requirement_id = _coerce_optional_string(raw_requirement.get("requirement_id"))
            raw_coverage = raw_requirement.get("coverage")
            coverage = (
                [str(item) for item in raw_coverage] if isinstance(raw_coverage, list) else []
            )
            snippet = ConformanceRequirementSnippetPayload(
                requirement_id=requirement_id or f"requirement[{index}]",
                status=requirement_status,
                details=_trim_conformance_text(raw_requirement.get("details")),
                remediation=_trim_conformance_text(raw_requirement.get("remediation")),
                coverage=coverage,
            )
            if (
                requirement_status != "pass"
                and len(failing_requirements) < CONFORMANCE_REPORT_SNIPPET_LIMIT
            ):
                failing_requirements.append(snippet)
            if snippet.remediation and len(remediation_snippets) < CONFORMANCE_REPORT_SNIPPET_LIMIT:
                remediation_snippets.append(snippet)

        missing_coverage: dict[str, list[str]] = {}
        missing_coverage_count = 0
        for task_name, raw_missing_items in raw_missing_coverage.items():
            if not isinstance(raw_missing_items, list):
                return report(
                    "invalid",
                    status=status,
                    generated_at=generated_at,
                    error=f"{artifact_path} missing_coverage.{task_name} must be a list.",
                )
            items = [str(item) for item in raw_missing_items]
            missing_coverage[str(task_name)] = items
            missing_coverage_count += len(items)

        state = status
        if generated_at < now - CONFORMANCE_REPORT_STALE_AFTER:
            state = "stale"

        return report(
            state,
            status=status,
            generated_at=generated_at,
            requirement_counts=dict(sorted(requirement_counts.items())),
            requirement_count=len(raw_requirements),
            missing_coverage_count=missing_coverage_count,
            missing_coverage=dict(sorted(missing_coverage.items())),
            traceability_task_count=len(raw_traceability_matrix),
            failing_requirements=failing_requirements,
            remediation_snippets=remediation_snippets,
        )

    def _latest_resumable_failed_workflow_record(
        self,
        *,
        event_id: str,
        effect_payload: dict[str, object],
        gate_payload: dict[str, object],
    ) -> dict[str, object] | None:
        candidates: list[dict[str, object]] = []

        def collect(records: dict[str, object], *, node_id_field: str) -> None:
            for record_key, raw_record in records.items():
                if not isinstance(raw_record, dict):
                    continue
                if raw_record.get("dismissed_at"):
                    continue
                if str(raw_record.get("event_id") or record_key).strip() != event_id:
                    continue
                if str(raw_record.get("status") or "").strip() != "failed":
                    continue
                source_node_id = _coerce_optional_string(raw_record.get("source_node_id"))
                source_trigger_type = _coerce_optional_string(raw_record.get("source_trigger_type"))
                node_id = _coerce_optional_string(raw_record.get(node_id_field))
                trace_id = _coerce_optional_string(raw_record.get("trace_id"))
                input_payload = raw_record.get("input_payload")
                if (
                    source_node_id is None
                    or source_trigger_type is None
                    or node_id is None
                    or trace_id is None
                    or not isinstance(input_payload, dict)
                ):
                    continue
                finished_at = _coerce_optional_datetime(raw_record.get("finished_at"))
                started_at = _coerce_optional_datetime(raw_record.get("started_at"))
                candidates.append(
                    {
                        "node_id": node_id,
                        "source_node_id": source_node_id,
                        "source_trigger_type": source_trigger_type,
                        "trace_id": trace_id,
                        "input_payload": dict(input_payload),
                        "finished_at": finished_at,
                        "started_at": started_at,
                    }
                )

        collect(effect_payload, node_id_field="effect_id")
        collect(gate_payload, node_id_field="gate_id")
        if not candidates:
            return None
        candidates.sort(
            key=lambda item: (
                item.get("finished_at")
                or item.get("started_at")
                or datetime.min.replace(tzinfo=UTC),
                str(item.get("node_id") or ""),
            ),
            reverse=True,
        )
        return candidates[0]

    def _workflow_event_journal(self) -> WorkflowEventJournal | SqliteWorkflowEventJournal:
        stores = self._workflow_run_store_paths()
        if self._workflow_uses_sqlite_store(stores):
            raw_retention_days = stores.get("orchestration_retention_days")
            return SqliteWorkflowEventJournal(
                stores["orchestration_store_path"],
                retention_days=int(raw_retention_days if raw_retention_days is not None else 30),
            )
        worker_config = self._read_worker_global_config("trigger-orchestrator").config
        runtime_dir = stores["runtime_dir"]
        journal_path = _resolve_path_setting(
            base_dir=self.project_dir,
            value=worker_config.get("workflow_event_journal_path"),
            fallback=workflow_event_journal_path(runtime_dir),
        )
        return WorkflowEventJournal(journal_path)

    def _collect_workflow_node_ids(self, config: dict[str, object]) -> set[str]:
        node_ids: set[str] = set()
        for collection_name, adapter in (
            ("triggers", _TRIGGER_DEFINITION_ADAPTER),
            ("gates", _GATE_DEFINITION_ADAPTER),
            ("effects", _EFFECT_DEFINITION_ADAPTER),
            ("queries", _QUERY_DEFINITION_ADAPTER),
        ):
            entries = config.get(collection_name, [])
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                node = adapter.validate_python(entry)
                node_ids.add(node.id)
        return node_ids

    def _normalize_workflow_editor_config(self, payload: dict[str, object]) -> dict[str, object]:
        candidate = json.loads(json.dumps(payload))
        if not isinstance(candidate, dict):
            return payload
        candidate = migrate_agent_prompt_lifecycle_ports(candidate)

        triggers = candidate.get("triggers")
        gates = candidate.get("gates")
        effects = candidate.get("effects")
        queries = candidate.get("queries")
        edges = candidate.get("edges")
        if triggers is None:
            triggers = []
            candidate["triggers"] = triggers
        if gates is None:
            gates = []
            candidate["gates"] = gates
        if effects is None:
            effects = []
            candidate["effects"] = effects
        if queries is None:
            queries = []
            candidate["queries"] = queries
        if edges is None:
            edges = []
            candidate["edges"] = edges
        if (
            not isinstance(triggers, list)
            or not isinstance(gates, list)
            or not isinstance(effects, list)
            or not isinstance(queries, list)
        ):
            return candidate
        if not isinstance(edges, list):
            return candidate

        output_schemas = derive_workflow_output_schemas(candidate)
        for gate in gates:
            if not isinstance(gate, dict):
                continue
            gate_type = str(gate.get("type") or "").strip()
            gate_id = str(gate.get("id") or "").strip()
            if gate_type == "gate:await-agent":
                gate.pop("completion_trigger_id", None)
            if gate_type in {
                "gate:filter",
                "gate:debounce",
                "gate:deserialize",
                "gate:serialize",
                "gate:map",
                "gate:dedupe",
                "gate:await-agent",
                "gate:for",
                "gate:merge",
            }:
                gate["outputs"] = output_schemas.get(gate_id, {})

        return candidate

    def _validate_workflow_editor_layout(
        self,
        payload: dict[str, object],
        *,
        node_ids: set[str],
        allow_unknown_nodes: bool = False,
    ) -> dict[str, object]:
        candidate = dict(payload)
        if "nodes" not in candidate:
            candidate = {"nodes": candidate.get("nodes", {}) if payload else {}}
        try:
            document = WorkflowEditorLayoutDocument.model_validate(candidate)
        except ValidationError as exc:
            raise _safe_validation_error(exc, context="Invalid workflow editor layout") from exc
        normalized_nodes: dict[str, dict[str, float]] = {}
        unknown_node_ids: list[str] = []
        for node_id, position in document.nodes.items():
            if node_id not in node_ids:
                if allow_unknown_nodes:
                    continue
                unknown_node_ids.append(node_id)
                continue
            normalized_nodes[node_id] = {"x": position.x, "y": position.y}
        if unknown_node_ids:
            unknown_list = ", ".join(sorted(unknown_node_ids))
            raise ValueError(f"Workflow layout references unknown node IDs: {unknown_list}")
        return {"nodes": normalized_nodes}

    def _strip_system_managed_workflows(self, config: dict[str, object]) -> dict[str, object]:
        candidate = json.loads(json.dumps(config))
        if not isinstance(candidate, dict):
            return config
        system_ids: set[str] = set()
        for collection_name in ("triggers", "gates", "effects", "queries"):
            entries = candidate.get(collection_name)
            if not isinstance(entries, list):
                continue
            candidate[collection_name] = [
                entry
                for entry in entries
                if not (
                    isinstance(entry, dict)
                    and _is_system_managed_workflow_node_id(str(entry.get("id") or "").strip())
                )
            ]
            system_ids.update(
                str(entry.get("id") or "").strip()
                for entry in entries
                if isinstance(entry, dict)
                and _is_system_managed_workflow_node_id(str(entry.get("id") or "").strip())
            )
        if isinstance(candidate.get("edges"), list) and system_ids:
            candidate["edges"] = [
                edge
                for edge in candidate["edges"]
                if not (
                    isinstance(edge, dict)
                    and (
                        str(edge.get("source_id") or "").strip() in system_ids
                        or str(edge.get("target_id") or "").strip() in system_ids
                    )
                )
            ]
        return candidate

    def _with_system_chat_assistant_workflow(
        self, config: dict[str, object]
    ) -> tuple[dict[str, object], bool]:
        merged = json.loads(json.dumps(config))
        if not isinstance(merged, dict):
            merged = {}
        fragment = _system_chat_assistant_workflow_fragment()
        changed = False
        for collection_name in ("triggers", "effects"):
            current_entries = merged.get(collection_name)
            entries = (
                [entry for entry in current_entries if isinstance(entry, dict)]
                if isinstance(current_entries, list)
                else []
            )
            by_id = {str(entry.get("id") or "").strip(): entry for entry in entries}
            for system_entry in fragment[collection_name]:
                system_id = str(system_entry.get("id") or "").strip()
                if by_id.get(system_id) != system_entry:
                    by_id[system_id] = dict(system_entry)
                    changed = True
            editable_entries = [
                entry
                for entry in entries
                if not _is_system_managed_workflow_node_id(str(entry.get("id") or "").strip())
            ]
            editable_entries.extend(dict(entry) for entry in fragment[collection_name])
            merged[collection_name] = editable_entries

        edge_entries = (
            [entry for entry in merged.get("edges", []) if isinstance(entry, dict)]
            if isinstance(merged.get("edges"), list)
            else []
        )
        non_system_edges = [
            edge
            for edge in edge_entries
            if not (
                _is_system_managed_workflow_node_id(str(edge.get("source_id") or "").strip())
                or _is_system_managed_workflow_node_id(str(edge.get("target_id") or "").strip())
            )
        ]
        desired_edges = non_system_edges + [dict(edge) for edge in fragment["edges"]]
        if edge_entries != desired_edges:
            changed = True
        merged["edges"] = desired_edges
        return merged, changed

    def _merge_system_managed_workflows(
        self, *, editable_config: dict[str, object], base_config: dict[str, object] | None
    ) -> dict[str, object]:
        merged = json.loads(json.dumps(editable_config))
        if not isinstance(merged, dict) or not isinstance(base_config, dict):
            return merged
        system_nodes: dict[str, list[dict[str, object]]] = {}
        system_ids: set[str] = set()
        for collection_name in ("triggers", "gates", "effects", "queries"):
            entries = base_config.get(collection_name)
            if not isinstance(entries, list):
                continue
            nodes = [
                dict(entry)
                for entry in entries
                if isinstance(entry, dict)
                and _is_system_managed_workflow_node_id(str(entry.get("id") or "").strip())
            ]
            if nodes:
                system_nodes[collection_name] = nodes
                system_ids.update(str(entry.get("id") or "").strip() for entry in nodes)
        for collection_name in ("triggers", "gates", "effects", "queries"):
            current_entries = merged.get(collection_name)
            editable_entries = (
                [entry for entry in current_entries if isinstance(entry, dict)]
                if isinstance(current_entries, list)
                else []
            )
            editable_entries = [
                entry
                for entry in editable_entries
                if not _is_system_managed_workflow_node_id(str(entry.get("id") or "").strip())
            ]
            editable_entries.extend(system_nodes.get(collection_name, []))
            merged[collection_name] = editable_entries
        if isinstance(base_config.get("edges"), list) and system_ids:
            editable_edges = (
                [edge for edge in merged.get("edges", []) if isinstance(edge, dict)]
                if isinstance(merged.get("edges"), list)
                else []
            )
            editable_edges.extend(
                dict(edge)
                for edge in base_config["edges"]
                if isinstance(edge, dict)
                and (
                    str(edge.get("source_id") or "").strip() in system_ids
                    or str(edge.get("target_id") or "").strip() in system_ids
                )
            )
            merged["edges"] = editable_edges
        return merged

    def _upsert_trigger_config(
        self, trigger_id: str, payload: dict[str, object]
    ) -> TriggerConfigMutationPayload:
        normalized_trigger_id = str(trigger_id).strip()
        if not normalized_trigger_id:
            raise ValueError("trigger_id is required")
        if _is_system_managed_workflow_node_id(normalized_trigger_id):
            raise ValueError(
                f"Trigger is system-managed and cannot be mutated: {normalized_trigger_id}"
            )
        normalized_payload = dict(payload)
        payload_trigger_id = str(normalized_payload.get("id", "")).strip()
        if payload_trigger_id and payload_trigger_id != normalized_trigger_id:
            raise ValueError("trigger_id must match payload.id when payload.id is provided")
        normalized_payload["id"] = normalized_trigger_id
        try:
            validated_trigger = _TRIGGER_DEFINITION_ADAPTER.validate_python(normalized_payload)
        except ValidationError as exc:
            raise _safe_validation_error(exc, context="Invalid trigger config") from exc
        normalized_payload = validated_trigger.model_dump(mode="json", exclude_none=True)

        current = self.trigger_repository.read_document()
        config = dict(current.config)
        existing_triggers_raw = config.get("triggers", [])
        if not isinstance(existing_triggers_raw, list):
            raise ValueError("Invalid trigger-orchestrator config: triggers must be a JSON array.")
        existing_triggers = [entry for entry in existing_triggers_raw if isinstance(entry, dict)]
        replacing = False
        for index, existing in enumerate(existing_triggers):
            existing_id = str(existing.get("id", "")).strip()
            if existing_id == normalized_trigger_id:
                existing_triggers[index] = normalized_payload
                replacing = True
                break
        if not replacing:
            next_config = dict(config)
            next_config["triggers"] = existing_triggers + [normalized_payload]
            self._seed_new_ticket_created_trigger_cursors(
                previous_config=config,
                next_config=next_config,
            )
            existing_triggers.append(normalized_payload)
        config["triggers"] = existing_triggers
        write_result = self.trigger_repository.write_document(config)
        return TriggerConfigMutationPayload(
            trigger_id=normalized_trigger_id,
            operation="updated" if replacing else "added",
            path=write_result.path,
            config=_workflow_config_for_legacy_mutation_response(write_result.config),
        )

    def _delete_trigger_config(self, trigger_id: str) -> TriggerConfigMutationPayload:
        normalized_trigger_id = str(trigger_id).strip()
        if not normalized_trigger_id:
            raise ValueError("trigger_id is required")
        if _is_system_managed_workflow_node_id(normalized_trigger_id):
            raise ValueError(
                f"Trigger is system-managed and cannot be mutated: {normalized_trigger_id}"
            )
        current = self.trigger_repository.read_document()
        config = dict(current.config)
        triggers_raw = config.get("triggers", [])
        if not isinstance(triggers_raw, list):
            raise ValueError("Invalid trigger-orchestrator config: triggers must be a JSON array.")
        filtered = []
        removed = False
        for entry in triggers_raw:
            if not isinstance(entry, dict):
                continue
            entry_id = str(entry.get("id", "")).strip()
            if entry_id == normalized_trigger_id:
                removed = True
                continue
            filtered.append(entry)
        if not removed:
            raise ValueError(f"Trigger not found: {normalized_trigger_id}")
        config["triggers"] = filtered
        write_result = self.trigger_repository.write_document(config)
        return TriggerConfigMutationPayload(
            trigger_id=normalized_trigger_id,
            operation="deleted",
            path=write_result.path,
            config=_workflow_config_for_legacy_mutation_response(write_result.config),
        )

    def _upsert_effect_config(
        self, effect_id: str, payload: dict[str, object]
    ) -> EffectConfigMutationPayload:
        normalized_effect_id = str(effect_id).strip()
        if not normalized_effect_id:
            raise ValueError("effect_id is required")
        if _is_system_managed_workflow_node_id(normalized_effect_id):
            raise ValueError(
                f"Effect is system-managed and cannot be mutated: {normalized_effect_id}"
            )
        normalized_payload = dict(payload)
        normalized_source_id = str(normalized_payload.pop("source_id", "")).strip()
        payload_effect_id = str(normalized_payload.get("id", "")).strip()
        if payload_effect_id and payload_effect_id != normalized_effect_id:
            raise ValueError("effect_id must match payload.id when payload.id is provided")
        normalized_payload["id"] = normalized_effect_id
        try:
            validated_effect = _EFFECT_DEFINITION_ADAPTER.validate_python(normalized_payload)
        except ValidationError as exc:
            raise _safe_validation_error(exc, context="Invalid effect config") from exc
        normalized_payload = validated_effect.model_dump(mode="json", exclude_none=True)

        current = self._read_worker_global_config("trigger-orchestrator")
        config = dict(current.config)
        existing_effects_raw = config.get("effects", [])
        if not isinstance(existing_effects_raw, list):
            raise ValueError("Invalid trigger-orchestrator config: effects must be a JSON array.")
        existing_effects = [entry for entry in existing_effects_raw if isinstance(entry, dict)]
        existing_edges_raw = config.get("edges", [])
        if not isinstance(existing_edges_raw, list):
            existing_edges_raw = []
        existing_edges = [entry for entry in existing_edges_raw if isinstance(entry, dict)]
        replacing = False
        for index, existing in enumerate(existing_effects):
            existing_id = str(existing.get("id", "")).strip()
            if existing_id == normalized_effect_id:
                existing_effects[index] = normalized_payload
                replacing = True
                break
        if not replacing:
            existing_effects.append(normalized_payload)

        filtered_edges: list[dict[str, object]] = []
        for edge in existing_edges:
            target_id = str(edge.get("target_id", "")).strip()
            if target_id == normalized_effect_id:
                continue
            filtered_edges.append(edge)
        if normalized_source_id:
            filtered_edges.append(
                {"source_id": normalized_source_id, "target_id": normalized_effect_id}
            )
        config["effects"] = existing_effects
        config["edges"] = filtered_edges
        write_result = self._write_worker_global_config("trigger-orchestrator", config)
        return EffectConfigMutationPayload(
            effect_id=normalized_effect_id,
            operation="updated" if replacing else "added",
            path=write_result.path,
            config=_workflow_config_for_legacy_mutation_response(write_result.config),
        )

    def _delete_effect_config(self, effect_id: str) -> EffectConfigMutationPayload:
        normalized_effect_id = str(effect_id).strip()
        if not normalized_effect_id:
            raise ValueError("effect_id is required")
        if _is_system_managed_workflow_node_id(normalized_effect_id):
            raise ValueError(
                f"Effect is system-managed and cannot be mutated: {normalized_effect_id}"
            )
        current = self._read_worker_global_config("trigger-orchestrator")
        config = dict(current.config)
        effects_raw = config.get("effects", [])
        if not isinstance(effects_raw, list):
            raise ValueError("Invalid trigger-orchestrator config: effects must be a JSON array.")
        filtered = []
        removed = False
        for entry in effects_raw:
            if not isinstance(entry, dict):
                continue
            entry_id = str(entry.get("id", "")).strip()
            if entry_id == normalized_effect_id:
                removed = True
                continue
            filtered.append(entry)
        if not removed:
            raise ValueError(f"Effect not found: {normalized_effect_id}")
        edges_raw = config.get("edges", [])
        filtered_edges = []
        if isinstance(edges_raw, list):
            for entry in edges_raw:
                if not isinstance(entry, dict):
                    continue
                source_id = str(entry.get("source_id", "")).strip()
                target_id = str(entry.get("target_id", "")).strip()
                if source_id == normalized_effect_id or target_id == normalized_effect_id:
                    continue
                filtered_edges.append(entry)
        config["effects"] = filtered
        config["edges"] = filtered_edges
        write_result = self._write_worker_global_config("trigger-orchestrator", config)
        return EffectConfigMutationPayload(
            effect_id=normalized_effect_id,
            operation="deleted",
            path=write_result.path,
            config=_workflow_config_for_legacy_mutation_response(write_result.config),
        )

    def _next_local_issue_number(self) -> str:
        current = 0
        if self.local_issue_counter_path.exists():
            try:
                current = int(
                    self.local_issue_counter_path.read_text(encoding="utf-8").strip() or "0"
                )
            except ValueError:
                current = 0
        current += 1
        self.local_issue_counter_path.parent.mkdir(parents=True, exist_ok=True)
        self.local_issue_counter_path.write_text(f"{current}\n", encoding="utf-8")
        return str(current)

    @contextmanager
    def _local_issue_creation_lock(self):
        self.local_issue_create_lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.local_issue_create_lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def _canonical_local_issue_id(self, issue_number: str, directory_path: str) -> str:
        return _scoped_issue_id(issue_number, directory_path=directory_path, provider="local")

    def _canonical_issue_id(self, record: LocalIssueRecord) -> str:
        remote = record.remote_reference
        if remote is not None and remote.provider == "azure_devops" and remote.work_item_id:
            return _scoped_issue_id(
                remote.work_item_id,
                directory_path=record.directory_path,
                provider="azure_devops",
                organization=remote.organization,
                project=remote.project,
            )
        return self._canonical_local_issue_id(record.issue_number, record.directory_path)

    def create_local_issue(
        self,
        *,
        title: str = "",
        body: str = "",
        repo_source: str | None = None,
        yaml_path: str | None = None,
        directory_path: str = "root",
        metadata: dict[str, Any] | None = None,
    ) -> LocalIssueRecord:
        return self.issue_git_admin.create_local_issue(
            title=title,
            body=body,
            repo_source=repo_source,
            yaml_path=yaml_path,
            directory_path=directory_path,
            metadata=metadata,
        )

    def update_local_issue(
        self,
        issue_identifier: str,
        *,
        title: str,
        body: str,
        state: str,
        repo_source: str | None = None,
    ) -> LocalIssueRecord:
        return self.issue_git_admin.update_local_issue(
            issue_identifier,
            title=title,
            body=body,
            state=state,
            repo_source=repo_source,
        )

    def _write_local_issue_yaml(
        self, issue: LocalIssueRecord, *, updated_at: datetime | None = None
    ) -> None:
        yaml_path = Path(issue.yaml_path)
        payload: dict[str, object] = {}
        if yaml_path.exists():
            try:
                loaded = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
            except Exception:
                loaded = {}
            if isinstance(loaded, dict):
                payload = dict(loaded)
        payload["issue_number"] = issue.issue_number
        payload["title"] = issue.title
        payload["body"] = issue.body
        payload["state"] = issue.state
        payload["source"] = issue.source or "local_gui"
        fields = _local_issue_fields(issue)
        if fields:
            payload["fields"] = fields
        else:
            payload.pop("fields", None)
        metadata_steps = issue.metadata.get("steps") if isinstance(issue.metadata, dict) else None
        if isinstance(metadata_steps, list) and all(
            isinstance(step, str) for step in metadata_steps
        ):
            payload["steps"] = list(metadata_steps)
        else:
            payload.pop("steps", None)
        metadata_plan_summary = (
            issue.metadata.get("plan_summary") if isinstance(issue.metadata, dict) else None
        )
        if isinstance(metadata_plan_summary, str) and metadata_plan_summary.strip():
            payload["plan_summary"] = metadata_plan_summary
        else:
            payload.pop("plan_summary", None)
        if updated_at is not None:
            payload["updated_at"] = updated_at.isoformat()
        yaml_path.parent.mkdir(parents=True, exist_ok=True)
        yaml_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    def create_issue(
        self,
        *,
        title: str = "",
        body: str = "",
        repo_source: str | None = None,
        yaml_path: str | None = None,
        directory_path: str = "root",
        metadata: dict[str, Any] | None = None,
    ) -> LocalIssueRecord:
        return self.issue_git_admin.create_issue(
            title=title,
            body=body,
            repo_source=repo_source,
            yaml_path=yaml_path,
            directory_path=directory_path,
            metadata=metadata,
        )

    def _refresh_proxy_cache_from_remote(
        self,
        issue: LocalIssueRecord,
        remote_payload: dict[str, object],
    ) -> LocalIssueRecord:
        issue.metadata = dict(issue.metadata)
        remote_comments = [
            dict(comment)
            for comment in list(remote_payload.get("comments") or [])
            if isinstance(comment, dict)
        ]
        issue.metadata["proxy_cache"] = {
            "title": str(remote_payload.get("title") or issue.title),
            "description": str(remote_payload.get("description") or issue.body),
            "state": str(remote_payload.get("state") or ""),
            "url": str(remote_payload.get("url") or ""),
            "updated_at": str(remote_payload.get("updated_at") or ""),
            "comments": remote_comments,
        }
        issue.title = str(remote_payload.get("title") or issue.title).strip() or issue.title
        issue.body = str(remote_payload.get("description") or issue.body).strip() or issue.body
        issue.state = str(remote_payload.get("state") or issue.state or "").strip() or None
        issue.url = str(remote_payload.get("url") or issue.url or "").strip() or None
        issue.comments = remote_comments
        if issue.remote_reference is not None:
            issue.remote_reference.last_synced_at = _utc_now()
            issue.remote_reference.last_known_remote_state = issue.state
            issue.remote_reference.work_item_url = issue.url or issue.remote_reference.work_item_url
        issue.updated_at = _utc_now()
        self.local_issue_store.put(issue.issue_id, issue)
        return issue

    def _proxy_target_for_issue(self, issue: LocalIssueRecord) -> tuple[str, str] | None:
        remote = issue.remote_reference
        if remote is not None and remote.provider and remote.work_item_id:
            return remote.provider, remote.work_item_id
        return self.issue_service.resolve_proxy_target(
            issue_number=issue.issue_number,
            issue_source=issue.source,
            metadata=issue.metadata,
        )

    def get_issue(
        self,
        issue_id: str,
        *,
        include_remote_comments: bool = True,
        sync_proxy_cache: bool = True,
    ) -> dict[str, object]:
        return self.issue_git_admin.get_issue(
            issue_id,
            include_remote_comments=include_remote_comments,
            sync_proxy_cache=sync_proxy_cache,
        )

    def read_issue(
        self,
        issue_number: str,
        *,
        include_remote_comments: bool = True,
        sync_proxy_cache: bool = True,
    ) -> dict[str, object]:
        return self.issue_git_admin.read_issue(
            issue_number,
            include_remote_comments=include_remote_comments,
            sync_proxy_cache=sync_proxy_cache,
        )

    def add_issue_comment(
        self,
        issue_id: str,
        comment: str,
        *,
        sync_proxy_cache: bool = True,
    ) -> dict[str, object]:
        return self.issue_git_admin.add_issue_comment(
            issue_id, comment, sync_proxy_cache=sync_proxy_cache
        )

    def post_issue_comment(
        self,
        issue_number: str,
        comment: str,
        *,
        sync_proxy_cache: bool = True,
    ) -> dict[str, object]:
        return self.issue_git_admin.post_issue_comment(
            issue_number,
            comment,
            sync_proxy_cache=sync_proxy_cache,
        )

    def set_issue_plan(
        self,
        issue_id: str,
        steps: list[str],
        summary: str | None = None,
    ) -> dict[str, object]:
        return self.issue_git_admin.set_issue_plan(issue_id, steps, summary=summary)

    def set_issue_state(
        self,
        issue_id: str,
        state: str,
        *,
        sync_proxy_cache: bool = True,
    ) -> dict[str, object]:
        return self.issue_git_admin.set_issue_state(
            issue_id, state, sync_proxy_cache=sync_proxy_cache
        )

    def update_issue(
        self,
        issue_id: str,
        *,
        state: str | None = None,
        fields: dict[str, object] | None = None,
        sync_proxy_cache: bool = True,
    ) -> dict[str, object]:
        return self.issue_git_admin.update_issue(
            issue_id,
            state=state,
            fields=fields,
            sync_proxy_cache=sync_proxy_cache,
        )

    def update_issue_metadata(
        self,
        *,
        issue_id: str,
        patch: Mapping[str, Any] | None = None,
        merge_patch: Mapping[str, Any] | None = None,
        expected_updated_at: str | None = None,
        sync_proxy_cache: bool = True,
    ) -> dict[str, object]:
        return self.issue_git_admin.update_issue_metadata(
            issue_id=issue_id,
            patch=patch,
            merge_patch=merge_patch,
            expected_updated_at=expected_updated_at,
            sync_proxy_cache=sync_proxy_cache,
        )

    def claim_next_issue(
        self,
        *,
        session_id: str,
    ) -> LocalIssueRecord | None:
        return self.issue_git_admin.claim_next_issue(session_id=session_id)

    def complete_issue(
        self,
        *,
        session_id: str,
        issue_id: str,
        summary: str,
    ) -> LocalIssueRecord:
        return self.issue_git_admin.complete_issue(
            session_id=session_id, issue_id=issue_id, summary=summary
        )

    def create_issue_vm(
        self,
        *,
        issue: str,
        session_id: str | None = None,
        model: str | None = None,
        vm_user: str = "agent",
        ttl_hours: int | None = None,
        discord_channels: list[str] | None = None,
        email_mailboxes: list[str] | None = None,
        email_folders: list[str] | None = None,
        dotnet: bool = False,
        nuget: str | None = None,
        mssql: bool = False,
        mssql_init_root: str | None = None,
        playwright: bool = False,
        codex_skills: list[str] | None = None,
        directory_path: str = "root",
    ) -> VmActionResult:
        return self.vm_agent_admin.create_issue_vm(
            issue=issue,
            session_id=session_id,
            model=model,
            vm_user=vm_user,
            ttl_hours=ttl_hours,
            discord_channels=discord_channels,
            email_mailboxes=email_mailboxes,
            email_folders=email_folders,
            dotnet=dotnet,
            nuget=nuget,
            mssql=mssql,
            mssql_init_root=mssql_init_root,
            playwright=playwright,
            codex_skills=codex_skills,
            directory_path=directory_path,
        )

    def create_issue_vm_from_local_issue(
        self,
        issue_identifier: str,
        *,
        model: str | None = None,
        vm_user: str = "agent",
        dotnet: bool = False,
        nuget: str | None = None,
        mssql: bool = False,
        mssql_init_root: str | None = None,
        playwright: bool = False,
        codex_skills: list[str] | None = None,
    ) -> VmActionResult:
        return self.vm_agent_admin.create_issue_vm_from_local_issue(
            issue_identifier,
            model=model,
            vm_user=vm_user,
            dotnet=dotnet,
            nuget=nuget,
            mssql=mssql,
            mssql_init_root=mssql_init_root,
            playwright=playwright,
            codex_skills=codex_skills,
        )

    def create_vm(
        self,
        *,
        provider: str | None = None,
        vm_name: str,
        session_id: str | None = None,
        labels: dict[str, str] | None = None,
        model: str | None = None,
        vm_user: str = "agent",
        ttl_hours: int | None = None,
        allowed_tools: list[str | ToolName] | None = None,
        allowed_script_names: list[str] | None = None,
        discord_channels: list[str] | None = None,
        teams_channel_allowlist: list[str] | None = None,
        teams_chat_allowlist: list[str] | None = None,
        teams_user_allowlist: list[str] | None = None,
        email_mailboxes: list[str] | None = None,
        email_folders: list[str] | None = None,
        communication_file_receive_enabled: bool | None = None,
        communication_file_send_enabled: bool | None = None,
        communication_file_max_bytes: int | None = None,
        communication_file_require_approval_for_send: bool | None = None,
        dotnet: bool = False,
        nuget: bool = False,
        mssql: bool = False,
        mssql_init_root: str | None = None,
        playwright: bool = False,
        codex_skills: list[str] | None = None,
        directory_path: str = "root",
    ) -> VmActionResult:
        payload: dict[str, Any] = {}
        if provider is not None:
            payload["provider"] = provider
        if teams_channel_allowlist is not None:
            payload["teams_channel_allowlist"] = teams_channel_allowlist
        if teams_chat_allowlist is not None:
            payload["teams_chat_allowlist"] = teams_chat_allowlist
        if teams_user_allowlist is not None:
            payload["teams_user_allowlist"] = teams_user_allowlist
        if communication_file_receive_enabled is not None:
            payload["communication_file_receive_enabled"] = communication_file_receive_enabled
        if communication_file_send_enabled is not None:
            payload["communication_file_send_enabled"] = communication_file_send_enabled
        if communication_file_max_bytes is not None:
            payload["communication_file_max_bytes"] = communication_file_max_bytes
        if communication_file_require_approval_for_send is not None:
            payload["communication_file_require_approval_for_send"] = (
                communication_file_require_approval_for_send
            )
        return self.vm_agent_admin.create_vm(
            vm_name=vm_name,
            session_id=session_id,
            labels=labels,
            model=model,
            vm_user=vm_user,
            ttl_hours=ttl_hours,
            allowed_tools=allowed_tools,
            allowed_script_names=allowed_script_names,
            discord_channels=discord_channels,
            email_mailboxes=email_mailboxes,
            email_folders=email_folders,
            dotnet=dotnet,
            nuget=nuget,
            mssql=mssql,
            mssql_init_root=mssql_init_root,
            playwright=playwright,
            codex_skills=codex_skills,
            directory_path=directory_path,
            **payload,
        )

    def clone_repo_into_vm(
        self,
        *,
        vm_name: str,
        session_id: str | None = None,
        repo_source: str,
        clone_branch: str | None = None,
        vm_user: str = "agent",
        directory_path: str = "root",
    ) -> VmActionResult:
        return self.vm_agent_admin.clone_repo_into_vm(
            vm_name=vm_name,
            session_id=session_id,
            repo_source=repo_source,
            clone_branch=clone_branch,
            vm_user=vm_user,
            directory_path=directory_path,
        )

    def bootstrap_python_in_vm(
        self,
        *,
        vm_name: str,
        session_id: str | None = None,
        host_checkout: str,
        target_dir: str,
        vm_user: str = "agent",
    ) -> VmActionResult:
        return self.vm_agent_admin.bootstrap_python_in_vm(
            vm_name=vm_name,
            session_id=session_id,
            host_checkout=host_checkout,
            target_dir=target_dir,
            vm_user=vm_user,
        )

    def bootstrap_frontend_in_vm(
        self,
        *,
        vm_name: str,
        session_id: str | None = None,
        host_checkout: str,
        install_roots: list[str] | None,
        vm_user: str = "agent",
    ) -> VmActionResult:
        return self.vm_agent_admin.bootstrap_frontend_in_vm(
            vm_name=vm_name,
            session_id=session_id,
            host_checkout=host_checkout,
            install_roots=install_roots,
            vm_user=vm_user,
        )

    def destroy_vm(self, target: str) -> VmActionResult:
        return self._execution_backend().destroy(target)

    def destroy_issue_vm(self, target: str) -> VmActionResult:
        return self.vm_agent_admin.destroy_issue_vm(target)

    def delete_local_issue(self, issue_identifier: str) -> VmActionResult:
        return self.issue_git_admin.delete_local_issue(issue_identifier)


def _log_page_from_end(
    text: str,
    *,
    limit: int,
    offset: int,
    query: str | None = None,
) -> dict[str, object]:
    lines = text.splitlines()
    normalized_query = str(query or "").strip()
    if normalized_query:
        needle = normalized_query.casefold()
        lines = [line for line in lines if needle in line.casefold()]
    total_lines = len(lines)
    normalized_limit = max(1, int(limit))
    normalized_offset = max(0, int(offset))
    end_index = max(0, total_lines - normalized_offset)
    start_index = max(0, end_index - normalized_limit)
    page_lines = lines[start_index:end_index]
    return {
        "text": "\n".join(page_lines) + ("\n" if page_lines else ""),
        "limit": normalized_limit,
        "offset": normalized_offset,
        "returned_lines": len(page_lines),
        "total_lines": total_lines,
        "start_line": start_index + 1 if page_lines else 0,
        "end_line": end_index if page_lines else 0,
        "has_more_before": start_index > 0,
        "query": normalized_query or None,
    }
