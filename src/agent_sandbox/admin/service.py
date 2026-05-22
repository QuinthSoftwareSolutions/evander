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
from collections import Counter, deque
from collections.abc import Iterator, Mapping
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
from agent_sandbox.capacity_health import (
    DiskUsageTelemetry,
    DirectorySizeTelemetry,
    HealthState,
    SessionStorageSample,
    TelemetryError,
    TelemetryStatus,
    collect_disk_usage,
    collect_recursive_directory_size,
    top_session_storage,
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
from agent_sandbox.admin.release_metadata import read_release_metadata
from agent_sandbox.admin.services.local_issue_identity import LocalIssueIdentityResolver
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
from agent_sandbox.messaging.targets import COMMUNICATION_TARGETS_DOMAIN
from agent_sandbox.ownership import DirectoryOwnershipQueries
from agent_sandbox.policy_config import EffectivePolicyConfig
from agent_sandbox.provider_config import ProviderConfigResolver
from agent_sandbox.repositories.chat_assistant_sessions import JsonChatAssistantSessionRepository
from agent_sandbox.repositories.session_manifests import as_session_manifest_repository
from agent_sandbox.communication_artifacts import CommunicationArtifactStore
from agent_sandbox.effects.registry import EffectProviderFactory
from agent_sandbox.events.provider_models import AgentEnqueuePromptEffectDefinition
from agent_sandbox.execution.container_worker_client import (
    ContainerWorkerClient,
    ContainerWorkerClientError,
)
from agent_sandbox.published_artifacts import (
    PublishedArtifact,
    PublishedArtifactMetadata,
    PublishedArtifactStore,
    validate_artifact_key,
)
from agent_sandbox.session_artifacts import (
    AGENT_ARTIFACTS_ENV,
    DEFAULT_AGENT_ARTIFACTS_DIR,
    normalize_artifact_relative_path,
)
from agent_sandbox.services import parse_teams_target
from agent_sandbox.repositories.skills_library import (
    SkillLibraryEntry,
    SkillLibraryMetadata,
    SkillLibraryRepository,
    normalize_skill_names,
)
from agent_sandbox.issues.service import IssueService
from agent_sandbox.vm.defaults_config import EffectiveVmDefaultsConfig, VmDefaultsConfigResolver
from agent_sandbox.pr_feedback.store import PrFeedbackStore
from agent_sandbox.models import (
    ApprovalQueueHealth,
    ApprovalRequest,
    ApprovalStatus,
    AgentRecord,
    ContainerSessionLifecycle,
    ContainerSessionLifecycleAction,
    ContainerSessionLifecycleActions,
    ContainerSessionLifecycleState,
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
    _append_builtin_hidden_workflows,
    _compose_configured_workflow_ir,
    compile_worker_global_config_with_diagnostics,
)
from agent_sandbox.worker.models import WorkerTelemetryPayload
from agent_sandbox.repositories.triggers import (
    JsonTriggerEventRepository,
    TriggerConfigDocument,
    TriggerRepository,
    TriggerEventRepository,
    WorkerConfigTriggerRepository,
)
from agent_sandbox.repositories.orchestration import (
    JsonWorkflowConfigSnapshotRepository,
    SqliteWorkflowConfigSnapshotRepository,
)
from agent_sandbox.repositories.orchestration_db import (
    connect_orchestration_db,
    datetime_to_storage,
    ensure_orchestration_schema,
)
from agent_sandbox.messaging.teams_auth import (
    DeviceCodeLoginSession,
    JsonTeamsIdentityCredentialStore,
    MicrosoftDeviceCodeAuthConfig,
    TeamsAuthCredentials,
    TeamsDeviceCodeTokenProvider,
)
from agent_sandbox.events.internal import (
    EXTERNAL_WORKFLOW_EVENT_PREFIX,
    InternalEventBus,
    InternalEventSource,
    validate_internal_event_type,
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
from agent_sandbox.triggers.providers._local_tickets import (
    seed_new_ticket_created_trigger_cursors,
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
from agent_sandbox.worker.security import sanitize_text

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
_ADMIN_CONFIG_MUTATION_THREAD_LOCK = threading.RLock()
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
    revision: str


class WorkerGlobalConfigWritePayload(BaseModel):
    worker_id: str
    path: str
    config: dict[str, object]
    revision: str


class AdminRevisionConflict(ValueError):
    def __init__(self, message: str, *, details: dict[str, Any]) -> None:
        super().__init__(message)
        self.details = details


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


class WorkflowRunSearchFilterPayload(BaseModel):
    node_id: str | None = None
    graph_id: str | None = None
    graph_name: str | None = None
    without_session: bool = False
    include_completed: bool = True


class WorkflowRunSearchItemPayload(BaseModel):
    event_id: str
    trace_id: str | None = None
    run_state: str = "active"
    source_node_id: str | None = None
    source_trigger_type: str | None = None
    delivery_state: str | None = None
    occurred_at: datetime | None = None
    latest_activity_at: datetime | None = None
    node_ids: list[str] = Field(default_factory=list)
    matched_node_ids: list[str] = Field(default_factory=list)
    session_ids: list[str] = Field(default_factory=list)
    has_agent_session: bool = False
    status_counts: dict[str, int] = Field(default_factory=dict)
    nodes: list[WorkflowRunNodeActivityPayload] = Field(default_factory=list)
    error: str | None = None


class WorkflowRunSearchPayload(BaseModel):
    worker_id: str = "trigger-orchestrator"
    generated_at: datetime
    filters: WorkflowRunSearchFilterPayload
    runs: list[WorkflowRunSearchItemPayload] = Field(default_factory=list)
    matched: int = 0
    returned: int = 0
    limit: int = 25
    has_more: bool = False


class WorkflowRunLookupPayload(BaseModel):
    run_id: str | None = None
    event_id: str | None = None
    trigger_event_id: str | None = None
    trace_id: str | None = None
    session_id: str | None = None
    artifact_id: str | None = None


class WorkflowRunDataAvailabilityPayload(BaseModel):
    graph_snapshot: str = "available"
    binding_trace: str = "available"
    approvals: str = "available"
    agent_links: str = "available"
    communication_artifacts: str = "available"
    tool_calls: str = "available"
    trigger_event: str = "available"


class WorkflowRunCandidateMatchPayload(BaseModel):
    event_id: str
    trace_id: str | None = None
    run_state: str | None = None
    latest_activity_at: datetime | None = None
    source_node_id: str | None = None
    source_trigger_type: str | None = None
    delivery_state: str | None = None
    matched_by: list[str] = Field(default_factory=list)


class WorkflowRunGraphSnapshotPayload(BaseModel):
    revision: str
    captured_at: datetime
    snapshot_type: str | None = None
    source: str | None = None
    node_count: int = 0
    edge_count: int = 0
    triggers: list[dict[str, Any]] = Field(default_factory=list)
    gates: list[dict[str, Any]] = Field(default_factory=list)
    effects: list[dict[str, Any]] = Field(default_factory=list)
    queries: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)
    workflow_graphs: Any | None = None


class WorkflowRunGraphPayload(BaseModel):
    config_revision: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    snapshot: WorkflowRunGraphSnapshotPayload | None = None
    data_availability: WorkflowRunDataAvailabilityPayload = Field(
        default_factory=WorkflowRunDataAvailabilityPayload
    )


class WorkflowRunTriggerEventPayload(BaseModel):
    event_id: str
    trigger_id: str
    trigger_type: str
    trace_id: str
    occurred_at: datetime | None = None
    delivery_state: str | None = None
    inflight_at: datetime | None = None
    delivered_at: datetime | None = None
    clear_reason: str | None = None
    error: str | None = None
    idempotency_key: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class WorkflowRunApprovalLinkPayload(BaseModel):
    request_id: str
    session_id: str | None = None
    action_type: str
    tool_name: str | None = None
    directory_path: str
    payload_digest: str
    status: str
    approver: str | None = None
    decision_at: datetime | None = None
    decision_reason: str | None = None
    expires_at: datetime | None = None
    human_summary: str | None = None


class WorkflowRunAgentLinkPayload(BaseModel):
    node_id: str
    node_type: str | None = None
    session_id: str | None = None
    agent_id: str | None = None
    backend: str | None = None
    agent_backend: str | None = None
    agent_available: bool = False
    agent_status: str | None = None
    agent_missing_reason: str | None = None
    target_name: str | None = None
    vm_name: str | None = None
    prompt: str | None = None
    prompt_summary: str | None = None
    prompt_status: str | None = None
    prompt_missing_reason: str | None = None
    dispatch_id: str | None = None
    thread_id: str | None = None
    execution_id: str | None = None
    artifact_ids: list[str] = Field(default_factory=list)
    status: str | None = None
    waiting: bool | None = None
    completed: bool | None = None
    session_status: str = "session_missing"
    session_available: bool = False
    session_missing_reason: str | None = None
    session_href: str | None = None
    log_status: str = "log_unavailable"
    log_available: bool = False
    log_href: str | None = None
    log_reference_href: str | None = None
    log_missing_reason: str | None = None
    log_reference: dict[str, Any] | None = None
    result_summary: str | None = None
    result_status: str | None = None
    result_missing_reason: str | None = None
    result_empty: bool | None = None


class WorkflowRunCommunicationArtifactLinkPayload(BaseModel):
    artifact_id: str
    session_id: str | None = None
    provider: str | None = None
    source_message_id: str | None = None
    source_attachment_id: str | None = None
    source_channel: str | None = None
    source_mailbox: str | None = None
    original_filename: str | None = None
    filename: str | None = None
    mime_type: str | None = None
    byte_count: int | None = None
    sha256: str | None = None
    created_at: datetime | None = None
    expires_at: datetime | None = None
    staged_at: datetime | None = None
    status: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkflowRunToolCallLinkPayload(BaseModel):
    created_at: datetime
    node_id: str | None = None
    tool_name: str | None = None
    request_id: str | None = None
    correlation_id: str | None = None
    approval_request_id: str | None = None
    workflow_run_id: str | None = None
    trace_id: str | None = None
    session_id: str | None = None
    vm_id: str | None = None
    decision: str | None = None
    status: str | None = None
    error_summary: str | None = None
    href: str | None = None
    filter: dict[str, Any] | None = None
    args_summary: str | None = None
    result_summary: str | None = None
    args_missing_reason: str | None = None
    result_status: str | None = None
    result_missing_reason: str | None = None
    result_empty: bool = False
    args: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    redacted_args: dict[str, Any] = Field(default_factory=dict)
    redacted_result: dict[str, Any] = Field(default_factory=dict)


class WorkflowRunNodeRecordPayload(BaseModel):
    record_kind: str
    execution_id: str | None = None
    event_id: str | None = None
    trace_id: str | None = None
    node_id: str | None = None
    node_type: str | None = None
    status: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None
    source_node_id: str | None = None
    source_trigger_type: str | None = None
    delivery_state: str | None = None
    occurred_at: datetime | None = None
    input_payload: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    resolved_config: dict[str, Any] = Field(default_factory=dict)
    binding_trace: dict[str, Any] | None = None
    binding_error: str | None = None
    error: str | None = None
    published_event_id: str | None = None
    completion_event_enqueued_at: datetime | None = None
    config_revision: str | None = None


class WorkflowRunNodeDetailPayload(BaseModel):
    node_id: str
    node_type: str | None = None
    display_name: str | None = None
    source_kind: str | None = None
    execution_id: str | None = None
    event_id: str | None = None
    trace_id: str | None = None
    status: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None
    occurred_at: datetime | None = None
    delivery_state: str | None = None
    resolved_config: dict[str, Any] = Field(default_factory=dict)
    binding_trace: dict[str, Any] | None = None
    binding_error: str | None = None
    input_payload: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    inputs_by_source: dict[str, dict[str, Any]] = Field(default_factory=dict)
    input_event_ids_by_source: dict[str, str] = Field(default_factory=dict)
    input_occurred_at_by_source: dict[str, datetime] = Field(default_factory=dict)
    error: str | None = None
    published_event_id: str | None = None
    completion_event_enqueued_at: datetime | None = None
    config_revision: str | None = None
    retry_resume_eligible: bool = False
    retry_resume_reason: str | None = None
    missing_fields: list[str] = Field(default_factory=list)
    approval_links: list[WorkflowRunApprovalLinkPayload] = Field(default_factory=list)
    agent_links: list[WorkflowRunAgentLinkPayload] = Field(default_factory=list)
    communication_artifact_links: list[WorkflowRunCommunicationArtifactLinkPayload] = Field(
        default_factory=list
    )
    tool_call_links: list[WorkflowRunToolCallLinkPayload] = Field(default_factory=list)
    records: list[WorkflowRunNodeRecordPayload] = Field(default_factory=list)


class WorkflowRunRecoveryActionPayload(BaseModel):
    eligible: bool = False
    reason: str | None = None
    event_id: str | None = None
    target_node_id: str | None = None
    source_node_id: str | None = None
    source_trigger_type: str | None = None
    source_event_id: str | None = None


class WorkflowRunRecoveryPayload(BaseModel):
    clear: WorkflowRunRecoveryActionPayload = Field(
        default_factory=WorkflowRunRecoveryActionPayload
    )
    resume: WorkflowRunRecoveryActionPayload = Field(
        default_factory=WorkflowRunRecoveryActionPayload
    )


class WorkflowRunDetailPayload(BaseModel):
    worker_id: str = "trigger-orchestrator"
    generated_at: datetime
    lookup: WorkflowRunLookupPayload
    candidate_matches: list[WorkflowRunCandidateMatchPayload] = Field(default_factory=list)
    lookup_candidates: list[WorkflowRunCandidateMatchPayload] = Field(default_factory=list)
    matched_by: list[str] = Field(default_factory=list)
    event_id: str
    trace_id: str | None = None
    source_node_id: str | None = None
    source_trigger_type: str | None = None
    delivery_state: str | None = None
    occurred_at: datetime | None = None
    latest_activity_at: datetime | None = None
    run_state: str = "active"
    active_nodes: list[WorkflowRunNodeActivityPayload] = Field(default_factory=list)
    failed_node_id: str | None = None
    failed_node_status: str | None = None
    failed_at: datetime | None = None
    error: str | None = None
    resumable: bool = False
    recovery: WorkflowRunRecoveryPayload = Field(default_factory=WorkflowRunRecoveryPayload)
    trigger_event: WorkflowRunTriggerEventPayload | None = None
    graph: WorkflowRunGraphPayload = Field(default_factory=WorkflowRunGraphPayload)
    nodes: list[WorkflowRunNodeDetailPayload] = Field(default_factory=list)
    tool_call_links: list[WorkflowRunToolCallLinkPayload] = Field(default_factory=list)
    data_availability: WorkflowRunDataAvailabilityPayload = Field(
        default_factory=WorkflowRunDataAvailabilityPayload
    )


SAMPLE_WORKFLOW_RUN_ID = "sample:first-run:run"
SAMPLE_WORKFLOW_TRACE_ID = "sample:first-run:trace"
SAMPLE_WORKFLOW_APPROVAL_REQUEST_ID = "sample:first-run:approval"
SAMPLE_WORKFLOW_SESSION_ID = "sample:first-run:session"
SAMPLE_WORKFLOW_DISPATCH_ID = "sample:first-run:dispatch"
SAMPLE_WORKFLOW_THREAD_ID = "sample:first-run:thread"
SAMPLE_WORKFLOW_REQUEST_ID = "sample:first-run:request"
SAMPLE_WORKFLOW_EXECUTION_ID = "sample:first-run:execution"
SAMPLE_WORKFLOW_ARTIFACT_ID = "comm_0123456789abcdef0123456789abcdef"
SAMPLE_WORKFLOW_GRAPH_ID = "sample:first-run:graph"
SAMPLE_WORKFLOW_REVISION = "sample:first-run:revision"
SAMPLE_TRIGGER_NODE_ID = "sample-trigger"
SAMPLE_AGENT_PROMPT_NODE_ID = "sample-agent-prompt"
SAMPLE_APPROVAL_BOUNDARY_NODE_ID = "sample-approval-boundary"
SAMPLE_SAFE_STOP_NODE_ID = "sample-safe-stop"


class WorkflowSampleTutorialStepPayload(BaseModel):
    step_id: str
    title: str
    body: str
    status: str = "pending"
    node_id: str | None = None


class WorkflowSampleStatePayload(BaseModel):
    active: bool = False
    launched_at: datetime | None = None
    run: WorkflowRunSummaryPayload | None = None
    detail: WorkflowRunDetailPayload | None = None
    tutorial_steps: list[WorkflowSampleTutorialStepPayload] = Field(default_factory=list)
    current_step: WorkflowSampleTutorialStepPayload | None = None


class ToolCallAuditRelatedLinkPayload(BaseModel):
    kind: str
    label: str
    value: str
    href: str | None = None


class ToolCallAuditEntryPayload(BaseModel):
    event_type: str = "tool_call"
    created_at: datetime
    session_id: str | None = None
    vm_id: str | None = None
    directory_path: str | None = None
    agent_role: str | None = None
    actor_user_id: str | None = None
    tool_name: str | None = None
    request_id: str | None = None
    correlation_id: str | None = None
    approval_request_id: str | None = None
    mcp_tool_approval_lease_id: str | None = None
    workflow_run_id: str | None = None
    trace_id: str | None = None
    duration_ms: int | None = None
    decision: str | None = None
    status: str | None = None
    error_summary: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    related_links: list[ToolCallAuditRelatedLinkPayload] = Field(default_factory=list)


class ToolCallAuditCountsPayload(BaseModel):
    scanned: int = 0
    matched: int = 0
    returned: int = 0
    skipped_malformed: int = 0
    skipped_non_tool_call: int = 0
    status_counts: dict[str, int] = Field(default_factory=dict)


class ToolCallAuditListPayload(BaseModel):
    items: list[ToolCallAuditEntryPayload] = Field(default_factory=list)
    counts: ToolCallAuditCountsPayload = Field(default_factory=ToolCallAuditCountsPayload)
    limit: int = 100
    has_more: bool = False
    next_cursor: str | None = None


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


class ConformanceReportRunPayload(BaseModel):
    kind: str
    exit_code: int
    started_at: datetime
    completed_at: datetime
    duration_seconds: float
    output: str | None = None
    reports: ConformanceReportsPayload


class WorkflowRunClearPayload(BaseModel):
    worker_id: str = "trigger-orchestrator"
    event_id: str
    cleared_at: datetime
    queue_records_updated: int = 0
    effect_records_updated: int = 0
    gate_records_updated: int = 0
    await_agent_records_removed: int = 0


class WorkflowNodeFailuresDismissPayload(BaseModel):
    worker_id: str = "trigger-orchestrator"
    node_id: str
    dismissed_at: datetime
    effect_records_updated: int = 0
    gate_records_updated: int = 0


class PublishedArtifactListPayload(BaseModel):
    items: list[PublishedArtifactMetadata]
    limit: int
    total: int
    next_cursor: str | None = None


class DeploymentStatusPayload(BaseModel):
    active: bool = False
    message: str | None = None
    source: str | None = None
    started_at: datetime | None = None
    updated_at: datetime | None = None
    ttl_seconds: int = Field(default=21600, ge=60, le=86400)
    stale: bool = False


class CapacityControlPlaneStoragePayload(BaseModel):
    total: DirectorySizeTelemetry
    areas: list[DirectorySizeTelemetry] = Field(default_factory=list)


class CapacityControlPlanePayload(BaseModel):
    disk: DiskUsageTelemetry
    storage: CapacityControlPlaneStoragePayload


class CapacitySessionStoragePayload(BaseModel):
    measured_total: DirectorySizeTelemetry
    measured_session_count: int = 0
    largest_sessions: list[SessionStorageSample] = Field(default_factory=list)
    oldest_sessions: list[SessionStorageSample] = Field(default_factory=list)
    missing_telemetry: list[DirectorySizeTelemetry] = Field(default_factory=list)


class CapacitySessionSummaryPayload(BaseModel):
    total_sessions: int = 0
    total_agents: int = 0
    lifecycle_counts: dict[str, int] = Field(default_factory=dict)
    status_counts: dict[str, int] = Field(default_factory=dict)
    backend_counts: dict[str, int] = Field(default_factory=dict)
    directory_counts: dict[str, int] = Field(default_factory=dict)
    directory_breakdowns: dict[str, dict[str, Any]] = Field(default_factory=dict)
    age_buckets: dict[str, int] = Field(default_factory=dict)
    storage: CapacitySessionStoragePayload


class CapacityWorkerTelemetryEntryPayload(BaseModel):
    worker_url: str
    directory_paths: list[str] = Field(default_factory=list)
    discovered_from: list[str] = Field(default_factory=list)
    reachable: bool = False
    last_checked_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    last_heartbeat_source: str | None = None
    healthz_status: TelemetryStatus = TelemetryStatus.MISSING
    telemetry_status: TelemetryStatus = TelemetryStatus.MISSING
    healthz: dict[str, Any] | None = None
    telemetry: WorkerTelemetryPayload | None = None
    errors: list[TelemetryError] = Field(default_factory=list)


class CapacityWorkerTelemetrySummaryPayload(BaseModel):
    total_workers: int = 0
    reachable_workers: int = 0
    telemetry_available_workers: int = 0
    entries: list[CapacityWorkerTelemetryEntryPayload] = Field(default_factory=list)


class CapacityQueueTelemetryPayload(BaseModel):
    status: TelemetryStatus = TelemetryStatus.MISSING
    health: HealthState = HealthState.UNKNOWN
    backend: str | None = None
    active_record_count: int = 0
    pending_count: int = 0
    inflight_count: int = 0
    oldest_event_id: str | None = None
    oldest_occurred_at: datetime | None = None
    oldest_age_seconds: float | None = None
    errors: list[TelemetryError] = Field(default_factory=list)


class CapacityPromptTelemetryPayload(BaseModel):
    status: TelemetryStatus = TelemetryStatus.MISSING
    health: HealthState = HealthState.UNKNOWN
    unread_count: int = 0
    oldest_message_id: str | None = None
    oldest_created_at: datetime | None = None
    oldest_age_seconds: float | None = None
    errors: list[TelemetryError] = Field(default_factory=list)


class CapacityHealthPayload(BaseModel):
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    control_plane: CapacityControlPlanePayload
    sessions: CapacitySessionSummaryPayload
    workers: CapacityWorkerTelemetrySummaryPayload
    queue: CapacityQueueTelemetryPayload
    prompts: CapacityPromptTelemetryPayload


class ReleaseChangelogEntryPayload(BaseModel):
    text: str
    kind: str | None = None


class ReleaseChangelogPayload(BaseModel):
    source: str | None = None
    path: str | None = None
    heading: str | None = None
    entries: list[ReleaseChangelogEntryPayload] = Field(default_factory=list)


class ReleaseNewsPayload(BaseModel):
    source: str | None = None
    path: str | None = None
    index: int = 0
    heading: str | None = None
    paragraphs: list[str] = Field(default_factory=list)
    has_previous: bool = False
    has_next: bool = False
    previous_index: int | None = None
    next_index: int | None = None


class ReleaseInfoPayload(BaseModel):
    version: str | None = None
    version_source: str | None = None
    build: str | None = None
    build_source: str | None = None
    changelog: ReleaseChangelogPayload | None = None
    news: ReleaseNewsPayload | None = None
    missing_reasons: list[str] = Field(default_factory=list)


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
    description: str | None = Field(default=None, exclude_if=lambda value: value is None)
    metadata: dict[str, Any] = Field(default_factory=dict, exclude_if=lambda value: not value)


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
    json_value_type: str | None = None
    default_value: Any = None
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


def _field_option(
    value: str,
    label: str | None = None,
    *,
    description: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> WorkflowEditorFieldOption:
    return WorkflowEditorFieldOption(
        value=value,
        label=label or value,
        description=description,
        metadata=metadata or {},
    )


def _communication_target_capabilities(target: dict[str, Any]) -> list[str]:
    policy = target.get("policy")
    raw_capabilities = policy.get("capabilities") if isinstance(policy, dict) else []
    if not isinstance(raw_capabilities, list):
        return []
    capabilities = {
        str(capability).strip()
        for capability in raw_capabilities
        if str(capability).strip()
    }
    return sorted(capabilities)


def _communication_target_shape(target: dict[str, Any]) -> str | None:
    provider = str(target.get("provider") or "").strip()
    address = str(target.get("address") or "").strip()
    if provider == "teams" and address:
        parsed_target = parse_teams_target(address)
        return f"teams_{parsed_target.kind}"
    if provider == "discord" and address:
        return "discord_channel"
    if provider == "email" and address:
        return "email_recipient"
    return None


def _communication_target_option_metadata(
    target: dict[str, Any], *, purpose: str
) -> dict[str, Any] | None:
    target_id = str(target.get("id") or "").strip()
    provider = str(target.get("provider") or "").strip()
    address = str(target.get("address") or "").strip()
    if not target_id or not provider or not address:
        return None

    capabilities = _communication_target_capabilities(target)
    target_shape = _communication_target_shape(target)
    if target_shape is None:
        return None

    metadata: dict[str, Any] = {
        "provider": provider,
        "address": address,
        "target_shape": target_shape,
        "policy_capabilities": capabilities,
    }
    if provider == "teams":
        parsed_target = parse_teams_target(address)
        metadata["teams_target_kind"] = parsed_target.kind
        metadata["teams_target_normalized"] = parsed_target.normalized
    if purpose == "send-message":
        metadata["thread_id"] = {
            "top_level": {
                "supported": "write" in capabilities,
                "requires_capability": "write",
            },
            "reply": {
                "supported": provider == "teams"
                and target_shape == "teams_channel"
                and "reply" in capabilities,
                "requires_capability": "reply",
                "supported_target_shapes": ["teams_channel"],
                "unsupported_target_shapes": [
                    "teams_chat",
                    "teams_user",
                    "discord_channel",
                    "email_recipient",
                ],
            },
        }
    elif purpose == "notification":
        metadata["thread_id"] = {
            "top_level": {
                "supported": "notify" in capabilities,
                "requires_capability": "notify",
            },
            "reply_hint": {
                "supported": provider == "teams" and target_shape == "teams_channel",
                "supported_target_shapes": ["teams_channel"],
                "unsupported_target_shapes": [
                    "teams_chat",
                    "teams_user",
                    "discord_channel",
                    "email_recipient",
                ],
            },
        }
    else:
        return None
    return metadata


def _communication_target_option(
    target: dict[str, Any], *, purpose: str
) -> WorkflowEditorFieldOption | None:
    target_id = str(target.get("id") or "").strip()
    provider = str(target.get("provider") or "").strip()
    metadata = _communication_target_option_metadata(target, purpose=purpose)
    if not target_id or not provider or metadata is None:
        return None

    capabilities = metadata["policy_capabilities"]
    capability_suffix = f", {', '.join(capabilities)}" if capabilities else ""
    return _field_option(
        target_id,
        f"{target_id} ({provider}{capability_suffix})",
        metadata=metadata,
    )


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
    json_value_type: str | None = None,
    default_value: Any = None,
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
        json_value_type=json_value_type,
        default_value=default_value,
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
    "trigger:ado:work-item-matching": (
        "Start when an Azure DevOps work item first matches or advances its changed date."
    ),
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
    "effect:issue:upsert-ado-proxy": (
        "Create or refresh local proxy tickets from Azure DevOps work items."
    ),
    "effect:ado:update-work-item": (
        "Update an Azure DevOps work item with an optional expected-state guard."
    ),
    "effect:pull-request:update": (
        "Update pull request metadata or tags with an optional status guard."
    ),
    "effect:event:emit": "Emit an internal workflow event for other graphs.",
    "effect:communication:send-message": (
        "Send a message through a directory-scoped communication target."
    ),
    "effect:notification:send": (
        "Send a notification through a directory-scoped communication target."
    ),
    "effect:session:update-metadata": "Patch metadata on a host-side session record.",
    "effect:session:approve-mcp-tools-for-run": (
        "Approve MCP tools for one agent session during the current workflow run."
    ),
    "effect:session:renew-if-expired": (
        "Extend a host-side session only when its expiry has already passed."
    ),
    "effect:artifact:stage": "Copy a file or directory from /home/agent into the session artifacts folder.",
    "effect:artifact:publish-artifacts": (
        "Compress and publish the session artifacts folder to the durable artifact store."
    ),
    "effect:git:promote-agent-branch": (
        "Promote an agent branch into a trusted host checkout."
    ),
    "effect:pr:create": "Create a pull request from a host-side checkout.",
    "query:vm:run-state": "Read the current run state for a VM-backed agent target.",
    "query:git:pr-note": "Read pull-request note context from a host checkout.",
    "query:pull-request:list": "List provider pull requests with optional status, branch, time, or tag filters.",
    "query:pull-request:feedback": "Fetch provider pull-request feedback without owning cursor state.",
    "query:session-context": "Read session context and metadata.",
    "query:session:list": "List sessions by generic manifest metadata.",
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
        type="trigger:ado:work-item-matching",
        label="ADO work item matching",
        fields=[
            _field("directory_path", "Directory path", "string"),
            _field("project", "Project", "string"),
            _field(
                "wiql",
                "WIQL",
                "string",
                multiline=True,
                description="Explicit WIQL query. When set, filter fields are not applied.",
            ),
            _field(
                "current_iteration",
                "Current iteration",
                "boolean",
                description="Resolve the team's current iteration before querying.",
            ),
            _field("team", "Team", "string"),
            _field("iteration", "Iteration path", "string"),
            _field("area_path", "Area path", "string"),
            _field("states", "States", "string_list"),
            _field("types", "Types", "string_list"),
            _field("tags_any", "Tags any", "string_list"),
            _field("text_search", "Text search", "string"),
            _field("top", "Top", "number"),
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
                json_value_type="array",
                default_value=[],
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
                "tool_argument_policies",
                "Tool argument policy",
                "json",
                bindable=True,
                description="Tool-keyed argument policy rules to attach to the created session.",
                json_value_type="object",
                default_value={},
                options=_tool_field_options(),
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
                "existing_agent_behavior",
                "Existing agent behavior",
                "enum",
                description="What effect:agent:create should do when the target session already exists.",
                options=[
                    _field_option("upsert", "Upsert existing agent"),
                    _field_option("noop", "No-op if existing"),
                ],
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
                "tool_argument_policies",
                "Tool argument policy",
                "json",
                bindable=True,
                description="Tool-keyed argument policy rules to attach to the created session.",
                json_value_type="object",
                default_value={},
                options=_tool_field_options(),
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
        type="effect:artifact:stage",
        label="Stage artifact",
        fields=[
            _field("target", "Target", "string", bindable=True),
            _field("session_id", "Session ID", "string", bindable=True),
            _field("vm_name", "VM name", "string", bindable=True),
            _field(
                "path",
                "Path",
                "string",
                required=True,
                bindable=True,
                description="File or directory path that resolves under /home/agent.",
                placeholder="repos/project/output/report.html",
            ),
            _field(
                "destination_path",
                "Destination path",
                "string",
                bindable=True,
                description="Optional relative destination under /artifacts. Defaults to the source basename.",
                placeholder="reports/report.html",
            ),
            _field("vm_user", "VM user", "string", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:artifact:publish-artifacts",
        label="Publish artifacts",
        fields=[
            _field("target", "Target", "string", bindable=True),
            _field("session_id", "Session ID", "string", bindable=True),
            _field("vm_name", "VM name", "string", bindable=True),
            _field(
                "key",
                "Key",
                "string",
                required=True,
                bindable=True,
                description="Immutable key for the published tar.gz archive.",
                placeholder="runs/${{trigger.event_id}}",
            ),
            _field("vm_user", "VM user", "string", bindable=True),
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
            _field(
                "resume_thread_metadata_key",
                "Resume thread metadata key",
                "string",
                bindable=True,
            ),
            _field(
                "resume_thread_metadata_collection",
                "Resume thread metadata collection",
                "string",
                bindable=True,
                placeholder="agent_thread_ids",
            ),
            _field(
                "mcp_tool_approval_lease_id",
                "MCP tool approval lease ID",
                "string",
                bindable=True,
                description=(
                    "Lease ID from Approve MCP tools for run. Direct upstream approval "
                    "outputs are detected automatically."
                ),
            ),
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
            _field(
                "resume_thread_metadata_key",
                "Resume thread metadata key",
                "string",
                bindable=True,
            ),
            _field(
                "resume_thread_metadata_collection",
                "Resume thread metadata collection",
                "string",
                bindable=True,
                placeholder="agent_thread_ids",
            ),
            _field(
                "mcp_tool_approval_lease_id",
                "MCP tool approval lease ID",
                "string",
                bindable=True,
                description=(
                    "Lease ID from Approve MCP tools for run. Direct upstream approval "
                    "outputs are detected automatically."
                ),
            ),
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
        type="effect:issue:upsert-ado-proxy",
        label="Issue upsert ADO proxy",
        fields=[
            _field("directory_path", "Directory path", "string", bindable=True),
            _field("organization_url", "Organization URL", "string", bindable=True),
            _field("project", "Project", "string", bindable=True),
            _field("repo_source", "Repo source", "string", bindable=True),
            _field("yaml_output_dir", "YAML output dir", "string", bindable=True),
            _field("item", "Item", "json", bindable=True),
            _field("items", "Items", "json", bindable=True),
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
            _field(
                "communication_target_id",
                "Communication target",
                "enum",
                required=True,
                description=(
                    "Directory-scoped target used to send a message. Teams channel, chat, "
                    "or user targets need write or reply capability; Discord targets need "
                    "write capability."
                ),
            ),
            _field("message", "Message", "string", required=True, bindable=True, multiline=True),
            _field(
                "thread_id",
                "Thread ID",
                "string",
                bindable=True,
                description=(
                    "Optional Teams channel reply ID. Leave blank for a top-level send. "
                    "Teams chat and user targets, and Discord targets, do not support "
                    "thread replies."
                ),
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:notification:send",
        label="Send notification",
        fields=[
            _field(
                "communication_target_id",
                "Communication target ID",
                "string",
                required=True,
                description=(
                    "Directory-scoped target used to send a notification. Teams, Discord, "
                    "and email targets need notify capability."
                ),
            ),
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
            _field(
                "thread_id",
                "Thread ID",
                "string",
                bindable=True,
                description=(
                    "Optional Teams channel reply hint. Leave blank for a top-level "
                    "notification. Teams chat and user targets reject thread replies at "
                    "runtime, and Discord or email targets are not thread destinations."
                ),
            ),
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
        type="effect:session:approve-mcp-tools-for-run",
        label="Approve MCP tools for run",
        fields=[
            _field("target", "Target", "string", bindable=True),
            _field("session_id", "Session ID", "string", bindable=True),
            _field("vm_name", "VM name", "string", bindable=True),
            _field(
                "tools",
                "Tools",
                "string_list",
                required=True,
                bindable=True,
                description=(
                    "MCP tools to approve only for this workflow run and session."
                ),
                options=_tool_field_options(),
            ),
            _field(
                "expires_in_seconds",
                "Expires in seconds",
                "number",
                bindable=True,
                description=(
                    "Optional lease lifetime. Defaults to 24 hours and never extends "
                    "past the session expiry."
                ),
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:session:renew-if-expired",
        label="Session renew if expired",
        fields=[
            _field("target", "Target", "string", bindable=True),
            _field("session_id", "Session ID", "string", bindable=True),
            _field("vm_name", "VM name", "string", bindable=True),
            _field(
                "extend_by",
                "Extend by",
                "string",
                bindable=True,
                description="Duration from now for renewed expired sessions, such as 8h, 2d, or PT8H.",
            ),
            _field("directory_path", "Directory path", "string", bindable=True),
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
        kind="effect",
        type="effect:pull-request:update",
        label="Pull request update",
        fields=[
            _field(
                "provider",
                "Provider",
                "enum",
                required=True,
                bindable=True,
                options=[
                    _field_option("azure_devops", "Azure DevOps"),
                    _field_option("ado", "ADO alias"),
                ],
            ),
            _field("repo", "Repo", "string", required=True, bindable=True),
            _field("pr_id", "PR ID", "string", required=True, bindable=True),
            _field("directory_path", "Directory path", "string", bindable=True),
            _field("organization_url", "Organization URL", "string", bindable=True),
            _field("project", "Project", "string", bindable=True),
            _field("title", "Title", "string", bindable=True),
            _field("description", "Description", "string", bindable=True, multiline=True),
            _field("status", "Status", "string", bindable=True),
            _field("add_tags", "Add tags", "string_list", bindable=True),
            _field("remove_tags", "Remove tags", "string_list", bindable=True),
            _field("expected_statuses", "Expected statuses", "string_list", bindable=True),
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
        type="query:session:list",
        label="Session list",
        fields=[
            _field("directory_path", "Directory path", "string", bindable=True),
            _field("metadata_equals", "Metadata equals", "json", bindable=True),
            _field("metadata_exists", "Metadata exists", "json", bindable=True),
            _field("metadata_prefix", "Metadata prefix", "json", bindable=True),
            _field("state", "State", "string", bindable=True),
            _field("active_only", "Active only", "boolean", bindable=True),
            _field("limit", "Limit", "number", bindable=True),
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
        type="query:pull-request:list",
        label="Pull request list",
        fields=[
            _field(
                "provider",
                "Provider",
                "enum",
                required=True,
                bindable=True,
                options=[
                    _field_option("azure_devops", "Azure DevOps"),
                    _field_option("ado", "ADO alias"),
                ],
            ),
            _field("repo", "Repo", "string", required=True, bindable=True),
            _field("directory_path", "Directory path", "string", bindable=True),
            _field("organization_url", "Organization URL", "string", bindable=True),
            _field("project", "Project", "string", bindable=True),
            _field("status", "Status", "string", bindable=True),
            _field("creator_id", "Creator ID", "string", bindable=True),
            _field("reviewer_id", "Reviewer ID", "string", bindable=True),
            _field("source_ref_name", "Source ref", "string", bindable=True),
            _field("target_ref_name", "Target ref", "string", bindable=True),
            _field("source_repository_id", "Source repository ID", "string", bindable=True),
            _field("repository_id", "Repository ID", "string", bindable=True),
            _field("min_time", "Min time", "string", bindable=True),
            _field("max_time", "Max time", "string", bindable=True),
            _field("query_time_range_type", "Time range type", "string", bindable=True),
            _field("include_links", "Include links", "boolean", bindable=True),
            _field("max_comment_length", "Max comment length", "number", bindable=True),
            _field(
                "tags",
                "Tags",
                "string",
                bindable=True,
                description="Tag expression with and, or, not, and parentheses.",
                placeholder="(TagA or TagB) and not TagC",
            ),
            _field("top", "Top", "number", bindable=True),
            _field("skip", "Skip", "number", bindable=True),
            _field("cursor", "Cursor", "string", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="query",
        type="query:pull-request:feedback",
        label="Pull request feedback",
        fields=[
            _field(
                "provider",
                "Provider",
                "enum",
                required=True,
                bindable=True,
                options=[
                    _field_option("azure_devops", "Azure DevOps"),
                    _field_option("ado", "ADO alias"),
                    _field_option("github", "GitHub"),
                ],
            ),
            _field("repo", "Repo", "string", required=True, bindable=True),
            _field("pr_id", "PR ID", "string", required=True, bindable=True),
            _field("directory_path", "Directory path", "string", bindable=True),
            _field("organization_url", "Organization URL", "string", bindable=True),
            _field("project", "Project", "string", bindable=True),
            _field("cursor", "Cursor", "string", bindable=True),
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


def _read_merged_json_objects(*paths: Path | None) -> dict[str, object]:
    payload: dict[str, object] = {}
    for path in paths:
        payload.update(_read_json_object(path))
    return payload


def _collect_workflow_keyed_values(payload: Any, *, key_suffix: str | None = None) -> list[str]:
    values: list[str] = []

    def walk(value: Any, key: str | None = None) -> None:
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                walk(child_value, str(child_key))
            return
        if isinstance(value, list):
            for child_value in value:
                walk(child_value, key)
            return
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                return
            if key_suffix is not None and (key is None or not key.lower().endswith(key_suffix)):
                return
            values.append(normalized)

    walk(payload)
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _collect_workflow_artifact_ids(payload: Any) -> list[str]:
    values: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for child_value in value.values():
                walk(child_value)
            return
        if isinstance(value, list):
            for child_value in value:
                walk(child_value)
            return
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                return
            try:
                from agent_sandbox.communication_artifacts import validate_artifact_id

                values.append(validate_artifact_id(normalized))
            except ValueError:
                pass

    walk(payload)
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


CONFORMANCE_REPORT_STALE_AFTER = timedelta(days=7)
CONFORMANCE_REPORT_SNIPPET_LIMIT = 10
CONFORMANCE_REPORT_TEXT_LIMIT = 500
CONFORMANCE_REPORT_RUN_TIMEOUT_SECONDS = 120
TRIGGER_CONFORMANCE_GUIDANCE = (
    "PYTHONPATH=src python -m pytest -q tests/test_trigger_conformance_suite.py"
)
EFFECT_CONFORMANCE_GUIDANCE = (
    "PYTHONPATH=src python -m pytest -q tests/test_effect_conformance_suite.py"
)
CONFORMANCE_REPORT_SPECS = {
    "trigger": {
        "kind": "trigger",
        "label": "Trigger",
        "artifact_path": "trigger-conformance-report.json",
        "guidance": TRIGGER_CONFORMANCE_GUIDANCE,
        "env_var": "AGENT_SANDBOX_TRIGGER_CONFORMANCE_REPORT",
        "suite_path": "tests/test_trigger_conformance_suite.py",
    },
    "effect": {
        "kind": "effect",
        "label": "Effect",
        "artifact_path": "effect-conformance-report.json",
        "guidance": EFFECT_CONFORMANCE_GUIDANCE,
        "env_var": "AGENT_SANDBOX_EFFECT_CONFORMANCE_REPORT",
        "suite_path": "tests/test_effect_conformance_suite.py",
    },
}


def _trim_conformance_text(
    value: object, *, limit: int = CONFORMANCE_REPORT_TEXT_LIMIT
) -> str | None:
    text = _coerce_optional_string(value)
    if text is None:
        return None
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3].rstrip()}..."


def _trim_conformance_process_output(value: object, *, limit: int = 2000) -> str:
    text = _coerce_optional_string(value)
    if text is None:
        return ""
    normalized = text.strip()
    if len(normalized) <= limit:
        return normalized
    return f"...{normalized[-limit:].lstrip()}"


def _coerce_conformance_process_stream(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace").strip()
    else:
        text = str(value).strip()
    return text or None


def _join_conformance_process_output(*values: object) -> str:
    parts = [_coerce_conformance_process_stream(value) for value in values]
    return "\n".join(part for part in parts if part)


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
    tool_argument_policies: dict[str, list[Any]] | None = None,
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
        approval_required_tools=list(policy.approval_required_tools),
        tool_argument_policies=tool_argument_policies or {},
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


def _session_tool_argument_policies_from_payload(
    session: SessionManifest, value: object
) -> dict[str, list[Any]]:
    try:
        validated = SessionManifest.model_validate(
            {
                "session_id": session.session_id,
                "allowed_tools": list(session.allowed_tools),
                "expires_at": session.expires_at,
                "tool_argument_policies": value,
            }
        )
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc
    return validated.tool_argument_policies


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
        "approval_required_tools",
        "tool_argument_policies",
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
    if "approval_required_tools" in payload:
        update["approval_required_tools"] = _normalize_allowed_tools(
            _normalize_plain_string_list(
                payload.get("approval_required_tools"),
                field_name="approval_required_tools",
            )
        )
    if "tool_argument_policies" in payload:
        update["tool_argument_policies"] = _session_tool_argument_policies_from_payload(
            session,
            payload.get("tool_argument_policies"),
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
        self.local_issue_identity_resolver = LocalIssueIdentityResolver(
            local_issue_store=self.local_issue_store,
            read_issue_number_from_yaml=_read_issue_number_from_yaml,
        )
        self.local_issue_counter_path = self.config.runtime_dir / "local-issue-counter.txt"
        self.local_issue_create_lock_path = self.config.runtime_dir / "local-issue-create.lock"
        self.admin_config_mutation_lock_path = (
            self.config.runtime_dir / "admin-config-mutations.lock"
        )
        self.local_issue_yaml_dir = self.config.runtime_dir / "issues"
        self.local_issue_yaml_dir.mkdir(parents=True, exist_ok=True)
        self._workflow_sample_lock = threading.RLock()
        self._workflow_sample_active = False
        self._workflow_sample_launched_at: datetime | None = None
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
                read_workflow_run_detail=self._read_workflow_run_detail,
                read_workflow_run_search=self._read_workflow_run_search,
                clear_workflow_run=self._clear_workflow_run,
                dismiss_workflow_node_failures=self._dismiss_workflow_node_failures,
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
            local_issue_identity_resolver=self.local_issue_identity_resolver,
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
            local_issue_identity_resolver=self.local_issue_identity_resolver,
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
                resolve_local_issue_record=self.local_issue_identity_resolver.resolve_record,
                resolve_local_issue_key=self.local_issue_identity_resolver.resolve_key,
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
                resolve_local_issue_record=self.local_issue_identity_resolver.resolve_record,
                resolve_local_issue_key=self.local_issue_identity_resolver.resolve_key,
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

    def read_capacity_health(self) -> CapacityHealthPayload:
        now = _utc_now()
        sessions = sorted(
            self.runtime.session_repository.list_sessions(),
            key=lambda session: (session.created_at, session.session_id),
        )
        agent_statuses = self._capacity_session_statuses()
        worker_entries = self._capacity_worker_telemetry_entries(sessions)
        session_storage = self._capacity_session_storage_payload(sessions, worker_entries)
        control_plane_storage_areas = self._capacity_control_plane_storage_areas()
        control_plane_storage = self._capacity_storage_aggregate(
            control_plane_storage_areas,
            scope="control_plane_known_storage",
        )
        control_plane_disk = collect_disk_usage(
            self.config.runtime_dir,
            scope="control_plane_runtime",
        )

        backend_counts: Counter[str] = Counter()
        directory_counts: Counter[str] = Counter()
        lifecycle_counts: Counter[str] = Counter()
        status_counts: Counter[str] = Counter()
        age_buckets: Counter[str] = Counter()
        directory_breakdowns: dict[str, dict[str, Any]] = {}
        for session in sessions:
            backend = self._capacity_session_backend(session)
            directory_path = self._agent_record_directory_path(session.directory_path)
            backend_counts[backend] += 1
            directory_counts[directory_path] += 1
            agent_status = str(agent_statuses.get(session.session_id) or "").strip() or "unknown"
            status_counts[agent_status] += 1
            lifecycle_bucket = self._capacity_session_lifecycle_bucket(
                session,
                agent_status=agent_status,
                now=now,
            )
            age_bucket = self._capacity_age_bucket(now - session.created_at)
            lifecycle_counts[lifecycle_bucket] += 1
            age_buckets[age_bucket] += 1
            directory_summary = directory_breakdowns.setdefault(
                directory_path,
                {
                    "total_sessions": 0,
                    "lifecycle_counts": Counter(),
                    "status_counts": Counter(),
                    "backend_counts": Counter(),
                    "age_buckets": Counter(),
                },
            )
            directory_summary["total_sessions"] += 1
            directory_summary["lifecycle_counts"][lifecycle_bucket] += 1
            directory_summary["status_counts"][agent_status] += 1
            directory_summary["backend_counts"][backend] += 1
            directory_summary["age_buckets"][age_bucket] += 1

        queue_payload = self._capacity_queue_telemetry(now=now)
        prompt_payload = self._capacity_prompt_telemetry(now=now)

        return CapacityHealthPayload(
            generated_at=now,
            control_plane=CapacityControlPlanePayload(
                disk=control_plane_disk,
                storage=CapacityControlPlaneStoragePayload(
                    total=control_plane_storage,
                    areas=control_plane_storage_areas,
                ),
            ),
            sessions=CapacitySessionSummaryPayload(
                total_sessions=len(sessions),
                total_agents=len(agent_statuses),
                lifecycle_counts=dict(lifecycle_counts),
                status_counts=dict(status_counts),
                backend_counts=dict(backend_counts),
                directory_counts=dict(directory_counts),
                directory_breakdowns={
                    directory_path: {
                        "total_sessions": int(summary["total_sessions"]),
                        "lifecycle_counts": dict(summary["lifecycle_counts"]),
                        "status_counts": dict(summary["status_counts"]),
                        "backend_counts": dict(summary["backend_counts"]),
                        "age_buckets": dict(summary["age_buckets"]),
                    }
                    for directory_path, summary in sorted(directory_breakdowns.items())
                },
                age_buckets=dict(age_buckets),
                storage=session_storage,
            ),
            workers=CapacityWorkerTelemetrySummaryPayload(
                total_workers=len(worker_entries),
                reachable_workers=sum(1 for entry in worker_entries if entry.reachable),
                telemetry_available_workers=sum(
                    1 for entry in worker_entries if entry.telemetry is not None
                ),
                entries=worker_entries,
            ),
            queue=queue_payload,
            prompts=prompt_payload,
        )

    def _capacity_session_statuses(self) -> dict[str, str]:
        try:
            records = self.list_agents(
                include_inactive=True,
                reconcile_containers=False,
                all_directories=True,
            )
        except Exception:
            return {}
        statuses: dict[str, tuple[int, datetime | None, str]] = {}
        kind_rank = {
            "container_worker_session": 0,
            "vm_session": 0,
            "vm": 1,
        }
        for record in records:
            session_id = str(record.session_id or "").strip()
            if not session_id:
                continue
            kind = str((record.host or {}).get("kind") or "").strip()
            if kind not in kind_rank:
                continue
            updated_at = record.updated_at or record.started_at
            rank = kind_rank[kind]
            current = statuses.get(session_id)
            if current is None or rank < current[0] or (
                rank == current[0]
                and updated_at is not None
                and (current[1] is None or updated_at > current[1])
            ):
                statuses[session_id] = (rank, updated_at, str(record.status or "").strip())
        return {session_id: value[2] for session_id, value in statuses.items()}

    def _capacity_session_backend(self, session: SessionManifest) -> str:
        metadata = session.metadata if isinstance(session.metadata, dict) else {}
        backend = str(metadata.get("execution_backend") or session.vm_provider or "").strip()
        normalized = backend.casefold()
        if normalized in {"libvirt", "libvirt_vm"}:
            return "libvirt_vm"
        if normalized == "container_worker_vm":
            return "container_worker_vm"
        if self._is_container_session(session):
            return "container_worker_vm"
        return backend or "libvirt_vm"

    @staticmethod
    def _capacity_session_lifecycle_bucket(
        session: SessionManifest,
        *,
        agent_status: str,
        now: datetime,
    ) -> str:
        expired = session.expires_at <= now
        normalized = agent_status.strip().casefold()
        if not expired and normalized in {"preparing", "starting", "running"}:
            return "active"
        if normalized in {"failed", "stale"}:
            return "failed_stale"
        if expired:
            return "retained"
        return "waiting"

    @staticmethod
    def _capacity_age_bucket(age: timedelta) -> str:
        seconds = max(0, int(age.total_seconds()))
        if seconds < 60 * 60:
            return "lt_1h"
        if seconds < 24 * 60 * 60:
            return "lt_1d"
        if seconds < 7 * 24 * 60 * 60:
            return "lt_7d"
        return "gte_7d"

    def _capacity_control_plane_storage_areas(self) -> list[DirectorySizeTelemetry]:
        areas: list[DirectorySizeTelemetry] = []
        paths: list[tuple[str, Path]] = [
            ("control_plane_session_store", self.config.session_store_path),
            ("control_plane_agent_prompt_store", self.runtime.agent_prompt_store.path),
            ("control_plane_local_issues", self.local_issue_yaml_dir),
            ("control_plane_container_worker_executions", self._container_worker_execution_repository().path),
            ("control_plane_communication_artifacts", self._communication_artifact_store().root),
            ("control_plane_published_artifacts", self._published_artifact_store().root),
        ]
        try:
            workflow_stores = self._workflow_run_store_paths()
        except Exception:
            workflow_stores = {}
        if workflow_stores:
            paths.extend(
                [
                    ("workflow_event_queue", Path(workflow_stores["event_queue_store_path"])),
                    (
                        "workflow_orchestration_store",
                        Path(workflow_stores["orchestration_store_path"]),
                    ),
                    (
                        "workflow_effect_executions",
                        Path(workflow_stores["effect_execution_store_path"]),
                    ),
                    (
                        "workflow_gate_executions",
                        Path(workflow_stores["gate_execution_store_path"]),
                    ),
                    (
                        "workflow_config_snapshots",
                        Path(workflow_stores["workflow_config_snapshot_store_path"]),
                    ),
                    (
                        "workflow_await_agent_store",
                        Path(workflow_stores["await_agent_store_path"]),
                    ),
                    (
                        "workflow_legacy_await_agent_store",
                        Path(workflow_stores["legacy_await_agent_store_path"]),
                    ),
                ]
            )
        for scope, path in paths:
            areas.append(collect_recursive_directory_size(path, scope=scope))
        return areas

    def _capacity_storage_aggregate(
        self,
        areas: list[DirectorySizeTelemetry],
        *,
        scope: str,
    ) -> DirectorySizeTelemetry:
        available = [area for area in areas if area.status is TelemetryStatus.AVAILABLE]
        errors = [error for area in areas for error in area.errors]
        if available:
            status = TelemetryStatus.AVAILABLE
            health = HealthState.OK if not errors else HealthState.WARNING
        elif any(area.status is TelemetryStatus.PERMISSION_DENIED for area in areas):
            status = TelemetryStatus.PERMISSION_DENIED
            health = HealthState.UNKNOWN
        elif any(area.status is TelemetryStatus.MISSING for area in areas):
            status = TelemetryStatus.MISSING
            health = HealthState.UNKNOWN
        else:
            status = TelemetryStatus.UNSUPPORTED
            health = HealthState.UNKNOWN
        return DirectorySizeTelemetry(
            scope=scope,
            status=status,
            health=health,
            size_bytes=sum(area.size_bytes for area in available),
            file_count=sum(area.file_count for area in available),
            directory_count=sum(area.directory_count for area in available),
            symlink_count=sum(area.symlink_count for area in available),
            error_count=len(errors),
            errors=errors,
        )

    def _capacity_session_storage_payload(
        self,
        sessions: list[SessionManifest],
        worker_entries: list[CapacityWorkerTelemetryEntryPayload],
    ) -> CapacitySessionStoragePayload:
        samples: dict[str, SessionStorageSample] = {}
        measured_total_bytes = 0
        measured_session_count = 0
        missing_telemetry: list[DirectorySizeTelemetry] = []
        missing_telemetry_seen: set[str] = set()

        for entry in worker_entries:
            telemetry = entry.telemetry
            if telemetry is None:
                if entry.worker_url not in missing_telemetry_seen:
                    missing_telemetry.append(
                        self._capacity_missing_storage_entry(
                            scope=f"session_storage:worker:{entry.worker_url}",
                            status=entry.telemetry_status,
                            message=entry.errors[0].message
                            if entry.errors
                            else "worker session storage telemetry is unavailable",
                        )
                    )
                    missing_telemetry_seen.add(entry.worker_url)
                continue
            measured_total_bytes += telemetry.session_storage.total.size_bytes
            measured_session_count += telemetry.session_storage.session_count
            for sample in (
                *telemetry.session_storage.largest_sessions,
                *telemetry.session_storage.oldest_sessions,
            ):
                current = samples.get(sample.session_id)
                if current is None:
                    samples[sample.session_id] = sample
                    continue
                samples[sample.session_id] = SessionStorageSample(
                    session_id=sample.session_id,
                    size_bytes=max(current.size_bytes, sample.size_bytes),
                    backend=current.backend or sample.backend,
                    directory_path=current.directory_path or sample.directory_path,
                    created_at=(
                        current.created_at
                        if current.created_at is not None
                        and (sample.created_at is None or current.created_at <= sample.created_at)
                        else sample.created_at
                    ),
                    updated_at=(
                        current.updated_at
                        if current.updated_at is not None
                        and (sample.updated_at is None or current.updated_at >= sample.updated_at)
                        else sample.updated_at
                    ),
                )

        libvirt_session_count = sum(
            1 for session in sessions if self._capacity_session_backend(session) == "libvirt_vm"
        )
        if libvirt_session_count > 0:
            missing_telemetry.append(
                self._capacity_missing_storage_entry(
                    scope="session_storage:libvirt_vm",
                    status=TelemetryStatus.UNSUPPORTED,
                    message="VM-local session storage is not host-readable",
                )
            )

        available_samples = list(samples.values())
        return CapacitySessionStoragePayload(
            measured_total=DirectorySizeTelemetry(
                scope="session_storage:container_worker_vm",
                status=TelemetryStatus.AVAILABLE if measured_session_count > 0 else TelemetryStatus.MISSING,
                health=HealthState.OK if measured_session_count > 0 else HealthState.UNKNOWN,
                size_bytes=measured_total_bytes,
                file_count=0,
                directory_count=0,
                symlink_count=0,
            ),
            measured_session_count=measured_session_count,
            largest_sessions=top_session_storage(available_samples, limit=10, order="largest"),
            oldest_sessions=top_session_storage(available_samples, limit=10, order="oldest"),
            missing_telemetry=missing_telemetry,
        )

    def _capacity_missing_storage_entry(
        self,
        *,
        scope: str,
        status: TelemetryStatus,
        message: str,
    ) -> DirectorySizeTelemetry:
        return DirectorySizeTelemetry(
            scope=scope,
            status=status,
            health=HealthState.UNKNOWN,
            error_count=1,
            errors=[TelemetryError(status=status, message=message)],
        )

    def _capacity_worker_telemetry_entries(
        self,
        sessions: list[SessionManifest],
    ) -> list[CapacityWorkerTelemetryEntryPayload]:
        descriptors = self._capacity_worker_descriptors(sessions)
        entries: list[CapacityWorkerTelemetryEntryPayload] = []
        for descriptor in descriptors:
            entries.append(self._capacity_worker_telemetry_entry(descriptor))
        return entries

    def _capacity_worker_descriptors(
        self,
        sessions: list[SessionManifest],
    ) -> list[dict[str, Any]]:
        descriptors: dict[str, dict[str, Any]] = {}

        def register(
            worker_url: str | None,
            *,
            directory_path: str | None,
            token_env: str | None,
            timeout_seconds: float | None,
            discovered_from: str,
        ) -> None:
            normalized_url = str(worker_url or "").strip().rstrip("/")
            if not normalized_url:
                return
            descriptor = descriptors.setdefault(
                normalized_url,
                {
                    "worker_url": normalized_url,
                    "directory_paths": set(),
                    "discovered_from": set(),
                    "token_env": token_env,
                    "timeout_seconds": timeout_seconds,
                },
            )
            if directory_path:
                descriptor["directory_paths"].add(self._agent_record_directory_path(directory_path))
            descriptor["discovered_from"].add(discovered_from)
            if descriptor.get("token_env") is None and token_env:
                descriptor["token_env"] = token_env
            if descriptor.get("timeout_seconds") is None and timeout_seconds is not None:
                descriptor["timeout_seconds"] = timeout_seconds

        try:
            root_config = self.runtime.provider_config.resolve_for_directory("root")
        except Exception:
            root_config = None
        if root_config is not None:
            register(
                root_config.container_worker_base_url,
                directory_path="root",
                token_env=root_config.container_worker_token_env,
                timeout_seconds=root_config.container_worker_request_timeout_seconds,
                discovered_from="configured:root",
            )

        now = _utc_now()
        for session in sessions:
            directory_path = self._agent_record_directory_path(session.directory_path)
            if self._is_container_session(session) and session.expires_at > now:
                try:
                    resolved_config = self.runtime.provider_config.resolve_for_directory(
                        session.directory_path
                    )
                except Exception:
                    resolved_config = None
                if resolved_config is not None:
                    register(
                        resolved_config.container_worker_base_url,
                        directory_path=directory_path,
                        token_env=resolved_config.container_worker_token_env,
                        timeout_seconds=resolved_config.container_worker_request_timeout_seconds,
                        discovered_from=f"configured:{directory_path}",
                    )
                register(
                    self._container_worker_url_from_metadata(session.metadata),
                    directory_path=directory_path,
                    token_env=(
                        resolved_config.container_worker_token_env
                        if resolved_config is not None
                        else None
                    ),
                    timeout_seconds=(
                        resolved_config.container_worker_request_timeout_seconds
                        if resolved_config is not None
                        else None
                    ),
                    discovered_from=f"session:{session.session_id}",
                )

        ordered = sorted(
            descriptors.values(),
            key=lambda item: (
                str(item["worker_url"]),
                sorted(str(path) for path in item["directory_paths"]),
            ),
        )
        for descriptor in ordered:
            descriptor["directory_paths"] = sorted(str(path) for path in descriptor["directory_paths"])
            descriptor["discovered_from"] = sorted(str(item) for item in descriptor["discovered_from"])
            if descriptor.get("token_env") is None:
                descriptor["token_env"] = "AGENT_SANDBOX_WORKER_TOKEN"
            if descriptor.get("timeout_seconds") is None:
                descriptor["timeout_seconds"] = 30.0
        return ordered

    def _capacity_worker_telemetry_entry(
        self,
        descriptor: dict[str, Any],
    ) -> CapacityWorkerTelemetryEntryPayload:
        worker_url = str(descriptor.get("worker_url") or "").strip().rstrip("/")
        token_env = str(descriptor.get("token_env") or "AGENT_SANDBOX_WORKER_TOKEN").strip()
        timeout_seconds = float(descriptor.get("timeout_seconds") or 30.0)
        checked_at = _utc_now()
        entry = CapacityWorkerTelemetryEntryPayload(
            worker_url=worker_url,
            directory_paths=[str(path) for path in descriptor.get("directory_paths", [])],
            discovered_from=[str(item) for item in descriptor.get("discovered_from", [])],
            last_checked_at=checked_at,
        )
        worker_client = ContainerWorkerClient(
            base_url=worker_url,
            token_env=token_env,
            timeout_seconds=timeout_seconds,
        )
        try:
            healthz = worker_client.health()
            entry = entry.model_copy(
                update={
                    "healthz_status": TelemetryStatus.AVAILABLE,
                    "healthz": healthz,
                    "reachable": True,
                    "last_heartbeat_at": checked_at,
                    "last_heartbeat_source": "healthz_live_probe",
                }
            )
        except ContainerWorkerClientError as exc:
            status = self._capacity_telemetry_status_from_error(exc)
            entry.errors.append(TelemetryError(status=status, message=str(exc)))
            entry = entry.model_copy(update={"healthz_status": status})

        try:
            telemetry = worker_client.telemetry()
            entry = entry.model_copy(
                update={
                    "telemetry_status": TelemetryStatus.AVAILABLE,
                    "telemetry": telemetry,
                    "reachable": True,
                    "last_heartbeat_at": telemetry.generated_at,
                    "last_heartbeat_source": "worker_telemetry_live_probe",
                }
            )
        except ContainerWorkerClientError as exc:
            status = self._capacity_telemetry_status_from_error(exc)
            entry.errors.append(TelemetryError(status=status, message=str(exc)))
            entry = entry.model_copy(
                update={
                    "telemetry_status": status,
                }
            )
        return entry

    @staticmethod
    def _capacity_telemetry_status_from_error(exc: Exception) -> TelemetryStatus:
        message = str(exc).casefold()
        if any(token in message for token in ("not configured", "unauthorized", "forbidden")):
            return TelemetryStatus.PERMISSION_DENIED
        if any(token in message for token in ("connect", "timeout", "timed out")):
            return TelemetryStatus.MISSING
        if any(token in message for token in ("non-json", "non-object", "json", "unsupported")):
            return TelemetryStatus.UNSUPPORTED
        if any(token in message for token in ("404", "405")):
            return TelemetryStatus.UNSUPPORTED
        if any(token in message for token in ("401", "403")):
            return TelemetryStatus.PERMISSION_DENIED
        return TelemetryStatus.UNSUPPORTED

    def _capacity_queue_telemetry(self, *, now: datetime) -> CapacityQueueTelemetryPayload:
        try:
            stores = self._workflow_run_store_paths()
        except Exception:
            return CapacityQueueTelemetryPayload(
                status=TelemetryStatus.MISSING,
                health=HealthState.UNKNOWN,
                errors=[
                    TelemetryError(
                        status=TelemetryStatus.MISSING,
                        message="workflow queue telemetry is unavailable",
                    )
                ],
            )
        backend = str(stores.get("orchestration_store_backend") or "").strip().casefold() or None
        try:
            queue_store, _, _ = self._workflow_run_store_bundle(stores)
            active_records = list(queue_store.list_active_records())
        except FileNotFoundError:
            return CapacityQueueTelemetryPayload(
                status=TelemetryStatus.MISSING,
                health=HealthState.UNKNOWN,
                backend=backend,
                errors=[TelemetryError(status=TelemetryStatus.MISSING, message="trigger queue is missing")],
            )
        except PermissionError:
            return CapacityQueueTelemetryPayload(
                status=TelemetryStatus.PERMISSION_DENIED,
                health=HealthState.UNKNOWN,
                backend=backend,
                errors=[
                    TelemetryError(
                        status=TelemetryStatus.PERMISSION_DENIED,
                        message="permission denied while reading trigger queue",
                    )
                ],
            )
        except (OSError, ValueError):
            return CapacityQueueTelemetryPayload(
                status=TelemetryStatus.UNSUPPORTED,
                health=HealthState.UNKNOWN,
                backend=backend,
                errors=[
                    TelemetryError(
                        status=TelemetryStatus.UNSUPPORTED,
                        message="trigger queue telemetry is unavailable",
                    )
                ],
            )

        pending = [record for record in active_records if record.delivery_state == "pending"]
        inflight = [record for record in active_records if record.delivery_state == "inflight"]
        oldest = min(
            active_records,
            key=lambda record: (record.occurred_at, record.event_id),
            default=None,
        )
        if oldest is None:
            health = HealthState.OK
        elif pending or inflight:
            health = HealthState.WARNING
        else:
            health = HealthState.OK
        oldest_age_seconds = None
        if oldest is not None:
            oldest_age_seconds = max(0.0, (now - oldest.occurred_at).total_seconds())
        return CapacityQueueTelemetryPayload(
            status=TelemetryStatus.AVAILABLE,
            health=health,
            backend=backend,
            active_record_count=len(active_records),
            pending_count=len(pending),
            inflight_count=len(inflight),
            oldest_event_id=oldest.event_id if oldest is not None else None,
            oldest_occurred_at=oldest.occurred_at if oldest is not None else None,
            oldest_age_seconds=oldest_age_seconds,
        )

    def _capacity_prompt_telemetry(self, *, now: datetime) -> CapacityPromptTelemetryPayload:
        try:
            raw = self.runtime.agent_prompt_store.read_raw()
        except FileNotFoundError:
            return CapacityPromptTelemetryPayload(
                status=TelemetryStatus.MISSING,
                health=HealthState.UNKNOWN,
                errors=[
                    TelemetryError(
                        status=TelemetryStatus.MISSING,
                        message="agent prompt store is missing",
                    )
                ],
            )
        except PermissionError:
            return CapacityPromptTelemetryPayload(
                status=TelemetryStatus.PERMISSION_DENIED,
                health=HealthState.UNKNOWN,
                errors=[
                    TelemetryError(
                        status=TelemetryStatus.PERMISSION_DENIED,
                        message="permission denied while reading agent prompts",
                    )
                ],
            )
        except (OSError, ValueError, json.JSONDecodeError):
            return CapacityPromptTelemetryPayload(
                status=TelemetryStatus.UNSUPPORTED,
                health=HealthState.UNKNOWN,
                errors=[
                    TelemetryError(
                        status=TelemetryStatus.UNSUPPORTED,
                        message="agent prompt telemetry is unavailable",
                    )
                ],
            )

        messages = raw.get("messages", {})
        unread_entries: list[tuple[str, datetime]] = []
        if isinstance(messages, dict):
            for message_id, value in messages.items():
                if not isinstance(value, dict):
                    continue
                status = str(value.get("status") or "").strip().casefold()
                if status != "unread":
                    continue
                created_at = _coerce_optional_datetime(value.get("created_at"))
                if created_at is None:
                    continue
                unread_entries.append((str(message_id), created_at))
        unread_entries.sort(key=lambda item: (item[1], item[0]))
        oldest = unread_entries[0] if unread_entries else None
        return CapacityPromptTelemetryPayload(
            status=TelemetryStatus.AVAILABLE,
            health=HealthState.OK if not unread_entries else HealthState.WARNING,
            unread_count=len(unread_entries),
            oldest_message_id=oldest[0] if oldest is not None else None,
            oldest_created_at=oldest[1] if oldest is not None else None,
            oldest_age_seconds=(
                max(0.0, (now - oldest[1]).total_seconds()) if oldest is not None else None
            ),
        )

    def read_release_info(self, *, news_index: int = 0) -> ReleaseInfoPayload:
        release_metadata = read_release_metadata(self.project_dir, news_index=news_index)
        return ReleaseInfoPayload(
            version=release_metadata.version,
            version_source=release_metadata.version_source,
            build=release_metadata.build,
            build_source=release_metadata.build_source,
            changelog=(
                None
                if release_metadata.changelog is None
                else ReleaseChangelogPayload(
                    source=release_metadata.changelog.source,
                    path=release_metadata.changelog.path,
                    heading=release_metadata.changelog.heading,
                    entries=[
                        ReleaseChangelogEntryPayload(
                            text=entry.text,
                            kind=entry.kind,
                        )
                        for entry in release_metadata.changelog.entries
                    ],
                )
            ),
            news=(
                None
                if release_metadata.news is None
                else ReleaseNewsPayload(
                    source=release_metadata.news.source,
                    path=release_metadata.news.path,
                    index=release_metadata.news.index,
                    heading=release_metadata.news.heading,
                    paragraphs=release_metadata.news.paragraphs,
                    has_previous=release_metadata.news.has_previous,
                    has_next=release_metadata.news.has_next,
                    previous_index=release_metadata.news.previous_index,
                    next_index=release_metadata.news.next_index,
                )
            ),
            missing_reasons=release_metadata.missing_reasons,
        )

    def release_changelog_file(self) -> Path:
        changelog_path = self.project_dir / "CHANGELOG.md"
        if not changelog_path.is_file():
            raise FileNotFoundError(f"{changelog_path.name} is missing.")
        return changelog_path

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
        summaries = self.operations_admin.list_sessions(
            include_expired=include_expired,
            expired_only=expired_only,
            directory_path=directory_path,
            direct_only=direct_only,
        )
        return [self._session_summary_with_container_lifecycle(summary) for summary in summaries]

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
        removed_assistant_mappings = self._delete_chat_assistant_session_mappings({session_id})
        cleanup_parts: list[str] = []
        if worker_session_deleted:
            cleanup_parts.append("worker_session_deleted=1")
        if removed_mappings:
            cleanup_parts.append(f"removed_mappings={removed_mappings}")
        if removed_approvals:
            cleanup_parts.append(f"removed_approvals={removed_approvals}")
        if removed_assistant_mappings:
            cleanup_parts.append(f"removed_assistant_mappings={removed_assistant_mappings}")
        if cleanup_parts:
            result.message = f"{result.message}; " + " ".join(cleanup_parts)
        return result

    def cleanup_container_session(self, session_id: str) -> SessionActionResult:
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            raise ValueError("session_id is required")
        session = self.runtime.session_repository.get_session(normalized_session_id)
        if session is None:
            raise ValueError(f"Unknown session: {normalized_session_id}")
        if not self._is_container_session(session):
            raise ValueError(
                f"Session {normalized_session_id!r} is not a container worker session"
            )

        lifecycle = self._container_session_lifecycle(session)
        if lifecycle is None or not lifecycle.actions.cleanup.supported:
            reason = (
                lifecycle.actions.cleanup.reason
                if lifecycle is not None and lifecycle.actions.cleanup.reason
                else "Cleanup is not currently supported for this session."
            )
            raise ValueError(reason)

        execution_records = self._container_execution_records_for_session(normalized_session_id)
        worker_backend = self._container_worker_backend(
            directory_path=session.directory_path,
            worker_url=self._container_worker_url_for_target(
                session=session,
                records=execution_records,
            ),
        )
        attempted_at = _utc_now()
        try:
            worker_backend.delete_session(normalized_session_id)
        except ContainerWorkerClientError as exc:
            completed_at = _utc_now()
            sanitized_error = sanitize_text(str(exc))
            updated_session = self._container_session_lifecycle_tombstone(
                session,
                state=ContainerSessionLifecycleState.CLEANUP_FAILED,
                cleanup_status="cleanup_failed",
                attempted_at=attempted_at,
                completed_at=completed_at,
                cleanup_error=sanitized_error,
            )
            self.runtime.session_repository.upsert_session(updated_session)
            return SessionActionResult(
                success=False,
                session_id=normalized_session_id,
                action="cleanup_container_session",
                message=(
                    f"Cleanup failed for container worker session {normalized_session_id}: "
                    f"{sanitized_error}"
                ),
            )

        completed_at = _utc_now()
        updated_session = self._container_session_lifecycle_tombstone(
            session,
            state=ContainerSessionLifecycleState.CLEANED_UP,
            cleanup_status="cleanup_succeeded",
            attempted_at=attempted_at,
            completed_at=completed_at,
            cleanup_error=None,
        )
        self.runtime.session_repository.upsert_session(updated_session)
        return SessionActionResult(
            success=True,
            session_id=normalized_session_id,
            action="cleanup_container_session",
            message=f"Cleaned up container worker session {normalized_session_id}",
        )

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
    def _container_lifecycle_action(
        *,
        supported: bool,
        kind: str | None,
        label: str,
        reason: str | None = None,
    ) -> ContainerSessionLifecycleAction:
        return ContainerSessionLifecycleAction(
            supported=supported,
            kind=kind,
            label=label,
            reason=reason,
        )

    @staticmethod
    def _container_session_lifecycle_metadata(session: SessionManifest) -> dict[str, Any]:
        metadata = dict(session.metadata) if isinstance(session.metadata, dict) else {}
        lifecycle = metadata.get("container_lifecycle")
        payload: dict[str, Any] = dict(lifecycle) if isinstance(lifecycle, dict) else {}
        for key in (
            "state",
            "cleanup_status",
            "cleanup_attempted_at",
            "cleanup_completed_at",
            "cleanup_error",
        ):
            if key in metadata and key not in payload:
                payload[key] = metadata[key]
        return payload

    @staticmethod
    def _container_record_lifecycle_metadata(record: Any) -> dict[str, Any]:
        metadata = dict(record.metadata) if isinstance(record.metadata, dict) else {}
        lifecycle = metadata.get("container_lifecycle")
        payload: dict[str, Any] = dict(lifecycle) if isinstance(lifecycle, dict) else {}
        for key in (
            "state",
            "cleanup_status",
            "cleanup_attempted_at",
            "cleanup_completed_at",
            "cleanup_error",
        ):
            if key in metadata and key not in payload:
                payload[key] = metadata[key]
        return payload

    @staticmethod
    def _container_session_lifecycle_tombstone(
        session: SessionManifest,
        *,
        state: ContainerSessionLifecycleState,
        cleanup_status: str,
        attempted_at: datetime,
        completed_at: datetime,
        cleanup_error: str | None,
    ) -> SessionManifest:
        metadata = dict(session.metadata) if isinstance(session.metadata, dict) else {}
        lifecycle = metadata.get("container_lifecycle")
        payload: dict[str, Any] = dict(lifecycle) if isinstance(lifecycle, dict) else {}
        payload.update(
            {
                "state": state.value,
                "cleanup_status": cleanup_status,
                "cleanup_attempted_at": attempted_at,
                "cleanup_completed_at": completed_at,
                "cleanup_error": cleanup_error,
            }
        )
        metadata["container_lifecycle"] = payload
        updated_expires_at = session.expires_at
        if state == ContainerSessionLifecycleState.CLEANED_UP and session.expires_at > completed_at:
            updated_expires_at = completed_at
        return session.model_copy(
            update={
                "metadata": metadata,
                "expires_at": updated_expires_at,
            }
        )

    @staticmethod
    def _container_execution_lifecycle_status(record: Any) -> str:
        normalized = str(getattr(record, "last_status", "") or "").strip().casefold()
        if normalized in {"created", "starting"}:
            return "starting"
        if normalized == "running":
            return "running"
        if normalized in {"stopped", "exited", "removed"}:
            return "stopped"
        if normalized in {"failed", "timeout", "timed_out"}:
            return "failed"
        return "unknown"

    def _session_summary_with_container_lifecycle(
        self, summary: SessionSummary
    ) -> SessionSummary:
        if not self._is_container_session(summary):
            return summary.model_copy(update={"container_lifecycle": None})
        executions = self._container_execution_records_for_session(summary.session_id)
        lifecycle = self._container_session_lifecycle(summary, executions=executions)
        return summary.model_copy(update={"container_lifecycle": lifecycle})

    def _container_session_lifecycle(
        self,
        session: SessionManifest,
        *,
        executions: list[Any] | None = None,
    ) -> ContainerSessionLifecycle | None:
        if not self._is_container_session(session):
            return None
        execution_records = sorted(
            list(executions or self._container_execution_records_for_session(session.session_id)),
            key=self._container_execution_sort_key,
            reverse=True,
        )
        session_lifecycle_payload = self._container_session_lifecycle_metadata(session)
        record_lifecycle_payloads = [
            self._container_record_lifecycle_metadata(record) for record in execution_records
        ]
        metadata_payloads = [session_lifecycle_payload, *record_lifecycle_payloads]

        def _first_value(payloads: list[dict[str, Any]], *keys: str) -> Any:
            for payload in payloads:
                for key in keys:
                    if key not in payload:
                        continue
                    value = payload.get(key)
                    if value is None:
                        continue
                    if isinstance(value, str):
                        text = value.strip()
                        if not text:
                            continue
                        return text
                    return value
            return None

        def _max_datetime(*keys: str) -> datetime | None:
            candidates: list[datetime] = []
            for payload in metadata_payloads:
                for key in keys:
                    parsed = _coerce_optional_datetime(payload.get(key))
                    if parsed is not None:
                        candidates.append(parsed)
            if not candidates:
                return None
            return max(candidates)

        session_cleanup_status = (
            str(_first_value([session_lifecycle_payload], "cleanup_status", "state") or "")
            .strip()
            .casefold()
        )
        record_cleanup_status = (
            str(_first_value(record_lifecycle_payloads, "cleanup_status", "state") or "")
            .strip()
            .casefold()
        )
        cleanup_attempted_at = _max_datetime("cleanup_attempted_at")
        cleanup_completed_at = _max_datetime("cleanup_completed_at")
        cleanup_error = _coerce_optional_string(
            _first_value(metadata_payloads, "cleanup_error")
        )

        active_records = [
            record
            for record in execution_records
            if self._container_execution_lifecycle_status(record) in {"starting", "running"}
        ]
        if active_records:
            state = (
                ContainerSessionLifecycleState.RUNNING
                if any(
                    self._container_execution_lifecycle_status(record) == "running"
                    for record in active_records
                )
                else ContainerSessionLifecycleState.STARTING
            )
        elif session_cleanup_status in {
            "cleaned_up",
            "cleanup_succeeded",
            "succeeded",
            "success",
        }:
            state = ContainerSessionLifecycleState.CLEANED_UP
        elif (
            session_cleanup_status in {"cleanup_failed", "failed", "error"}
            or record_cleanup_status in {"cleanup_failed", "failed", "error"}
            or cleanup_error
        ):
            state = ContainerSessionLifecycleState.CLEANUP_FAILED
        else:
            if execution_records:
                latest_record = execution_records[0]
                latest_status = self._container_execution_lifecycle_status(latest_record)
                if latest_status == "failed":
                    state = ContainerSessionLifecycleState.FAILED
                elif latest_status == "stopped":
                    state = ContainerSessionLifecycleState.STOPPED
                elif session.expires_at <= _utc_now():
                    state = ContainerSessionLifecycleState.ABANDONED
                else:
                    state = ContainerSessionLifecycleState.PREPARED
            elif session.expires_at <= _utc_now():
                state = ContainerSessionLifecycleState.ABANDONED
            else:
                state = ContainerSessionLifecycleState.PREPARED

        last_heartbeat_at = None
        active_heartbeat_candidates: list[datetime] = []
        for record in execution_records:
            if self._container_execution_lifecycle_status(record) not in {"starting", "running"}:
                continue
            updated_at = getattr(record, "updated_at", None)
            if isinstance(updated_at, datetime):
                active_heartbeat_candidates.append(updated_at)
        if active_heartbeat_candidates:
            last_heartbeat_at = max(active_heartbeat_candidates)

        completion_candidates: list[datetime] = []
        for record in execution_records:
            finished_at = getattr(record, "finished_at", None)
            if isinstance(finished_at, datetime):
                completion_candidates.append(finished_at)
        last_completion_at = max(completion_candidates) if completion_candidates else None
        last_signal_at = max(
            [timestamp for timestamp in (last_heartbeat_at, last_completion_at) if timestamp is not None],
            default=None,
        )

        cleanup_supported = state in {
            ContainerSessionLifecycleState.STOPPED,
            ContainerSessionLifecycleState.FAILED,
            ContainerSessionLifecycleState.ABANDONED,
            ContainerSessionLifecycleState.CLEANUP_FAILED,
        }
        if cleanup_supported:
            cleanup_reason = None
        elif state == ContainerSessionLifecycleState.CLEANED_UP:
            cleanup_reason = "Session has already been cleaned up."
        elif state in {
            ContainerSessionLifecycleState.RUNNING,
            ContainerSessionLifecycleState.STARTING,
        }:
            cleanup_reason = "Session still has active executions."
        else:
            cleanup_reason = "Cleanup is not currently supported for this session."

        recovery_supported = (
            state not in {
                ContainerSessionLifecycleState.RUNNING,
                ContainerSessionLifecycleState.STARTING,
                ContainerSessionLifecycleState.CLEANED_UP,
            }
            and session.expires_at <= _utc_now()
        )
        if recovery_supported:
            recovery_reason = None
        elif state == ContainerSessionLifecycleState.CLEANED_UP:
            recovery_reason = "Session has already been cleaned up."
        elif state in {
            ContainerSessionLifecycleState.RUNNING,
            ContainerSessionLifecycleState.STARTING,
        }:
            recovery_reason = "Session still has active executions."
        else:
            recovery_reason = "Session is not expired."

        return ContainerSessionLifecycle(
            state=state,
            last_heartbeat_at=last_heartbeat_at,
            last_completion_at=last_completion_at,
            last_signal_at=last_signal_at,
            cleanup_attempted_at=cleanup_attempted_at,
            cleanup_completed_at=cleanup_completed_at,
            cleanup_error=cleanup_error,
            actions=ContainerSessionLifecycleActions(
                cleanup=self._container_lifecycle_action(
                    supported=cleanup_supported,
                    kind="cleanup_container_session",
                    label="Cleanup session",
                    reason=cleanup_reason,
                ),
                recovery=self._container_lifecycle_action(
                    supported=recovery_supported,
                    kind="renew_session",
                    label="Renew session",
                    reason=recovery_reason,
                ),
            ),
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
            "approval_required_tools": [
                tool.value if isinstance(tool, ToolName) else str(tool)
                for tool in session.approval_required_tools
            ],
            "tool_argument_policies": {
                tool_name: [rule.model_dump(mode="json") for rule in rules]
                for tool_name, rules in session.tool_argument_policies.items()
            },
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

    def effective_tool_policy_payload(
        self, session: SessionManifest | None
    ) -> dict[str, Any] | None:
        if session is None:
            return None
        effective_policy = self.runtime.policy_config.resolve_for_session_manifest(session)
        approval_required_tools: list[str] = []
        seen_tools: set[str] = set()
        for tool in effective_policy.approval_required_tools:
            value = tool.value if isinstance(tool, ToolName) else str(tool)
            if value in seen_tools:
                continue
            seen_tools.add(value)
            approval_required_tools.append(value)
        for tool in session.approval_required_tools:
            value = tool.value if isinstance(tool, ToolName) else str(tool)
            if value in seen_tools:
                continue
            seen_tools.add(value)
            approval_required_tools.append(value)

        tool_argument_policies: dict[str, list[dict[str, Any]]] = {}
        for source in (effective_policy.tool_argument_policies, session.tool_argument_policies):
            for tool_name, rules in source.items():
                tool_rules = tool_argument_policies.setdefault(tool_name, [])
                tool_rules.extend(rule.model_dump(mode="json") for rule in rules)

        return {
            "allowed_tools": [
                tool.value if isinstance(tool, ToolName) else str(tool)
                for tool in session.allowed_tools
            ],
            "approval_required_tools": approval_required_tools,
            "tool_argument_policies": tool_argument_policies,
        }

    def _agent_record_from_vm(self, summary: VmSummary) -> AgentRecord:
        power_state = str(summary.power_state or summary.libvirt_state or "unknown").strip()
        status = "running" if power_state == "running" else "stopped"
        directory_path = summary.directory_paths[0] if len(summary.directory_paths) == 1 else "root"
        session_policy = None
        effective_tool_policy = None
        if len(summary.session_ids) == 1:
            session = self.runtime.session_repository.get_session(summary.session_ids[0])
            session_policy = self._agent_session_policy_payload(session)
            effective_tool_policy = self.effective_tool_policy_payload(session)
        return AgentRecord(
            agent_id=summary.vm_name,
            backend="libvirt_vm",
            target_name=summary.vm_name,
            session_id=summary.session_ids[0] if len(summary.session_ids) == 1 else None,
            directory_path=directory_path,
            status=status,
            updated_at=summary.last_state_update_at,
            effective_tool_policy=effective_tool_policy,
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

    def _agent_record_from_stale_vm_session(
        self,
        session: SessionManifest,
        *,
        inventory_error: str | None = None,
    ) -> AgentRecord:
        vm_name = str(session.vm_id or session.vm_provider_id or session.session_id).strip()
        directory_path = self._agent_record_directory_path(session.directory_path)
        metadata = dict(session.metadata) if isinstance(session.metadata, dict) else {}
        labels = _normalize_session_labels(metadata.get("labels"))
        host: dict[str, Any] = {
            "kind": "vm_session",
            "vm_name": vm_name,
            "provider": str(session.vm_provider or "libvirt").strip() or "libvirt",
            "session_ids": [session.session_id],
            "labels": labels,
            "directory_paths": [directory_path],
            "inventory_status": "missing",
            "session_policy": self._agent_session_policy_payload(session),
        }
        if inventory_error:
            host["inventory_error"] = inventory_error
        return AgentRecord(
            agent_id=vm_name,
            backend="libvirt_vm",
            target_name=vm_name,
            session_id=session.session_id,
            directory_path=directory_path,
            status="stale",
            updated_at=session.created_at,
            effective_tool_policy=self.effective_tool_policy_payload(session),
            host=host,
            capabilities={
                "start": False,
                "shutdown": False,
                "resume": False,
                "enable_disable": False,
                "kill": False,
                "logs": False,
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
        session = self.runtime.session_repository.get_session(record.session_id)
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
            "session_policy": self._agent_session_policy_payload(session),
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
            effective_tool_policy=self.effective_tool_policy_payload(session),
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
        lifecycle = self._container_session_lifecycle(session, executions=execution_records)
        status = self._container_session_status(execution_records)
        if lifecycle is not None and lifecycle.state in {
            ContainerSessionLifecycleState.ABANDONED,
            ContainerSessionLifecycleState.CLEANUP_FAILED,
            ContainerSessionLifecycleState.CLEANED_UP,
        }:
            status = lifecycle.state.value
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
            effective_tool_policy=self.effective_tool_policy_payload(session),
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
        vm_inventory_error: str | None = None
        inventoried_vm_names: set[str] = set()
        inventoried_session_ids: set[str] = set()
        if selected_backend in {"all", "libvirt_vm"}:
            try:
                vm_summaries = self.list_vms(
                    include_inactive=include_inactive,
                    directory_path=None if all_directories else normalized_directory,
                    direct_only=False if all_directories else direct_only,
                )
            except Exception as exc:
                vm_inventory_error = str(exc)
                vm_summaries = []
            for summary in vm_summaries:
                inventoried_vm_names.add(str(summary.vm_name or "").strip())
                inventoried_session_ids.update(
                    str(session_id or "").strip() for session_id in summary.session_ids
                )
                record = self._agent_record_from_vm(summary)
                if include_inactive or self._agent_status_is_active(record.status):
                    records.append(record)
            for session in sorted(
                self.runtime.session_repository.list_sessions(),
                key=lambda item: (item.created_at, item.session_id),
                reverse=True,
            ):
                vm_name = str(session.vm_id or "").strip()
                if not vm_name or self._is_container_session(session):
                    continue
                if vm_name in inventoried_vm_names or session.session_id in inventoried_session_ids:
                    continue
                if not all_directories and not self._agent_record_matches_directory(
                    session.directory_path,
                    normalized_directory,
                    direct_only=direct_only,
                ):
                    continue
                record = self._agent_record_from_stale_vm_session(
                    session,
                    inventory_error=vm_inventory_error,
                )
                if include_inactive or self._agent_status_is_active(record.status):
                    records.append(record)
            if selected_backend == "libvirt_vm" and vm_inventory_error and not records:
                raise RuntimeError(vm_inventory_error)

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
                **(
                    {"tool_argument_policies": payload["tool_argument_policies"]}
                    if "tool_argument_policies" in payload
                    else {}
                ),
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
            session = self.runtime.session_repository.get_session(session_id) if session_id else None
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
                "effective_tool_policy": self.effective_tool_policy_payload(session),
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
            **(
                {"tool_argument_policies": payload["tool_argument_policies"]}
                if "tool_argument_policies" in payload
                else {}
            ),
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
        session = session_repository.get_session(session_id)
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
            "effective_tool_policy": self.effective_tool_policy_payload(session),
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

    def _delete_chat_assistant_session_mappings(self, session_ids: set[str]) -> int:
        repository = JsonChatAssistantSessionRepository(
            self.config.runtime_dir / "chat-assistant-session-mappings.json"
        )
        return repository.delete_mappings_for_sessions(session_ids)

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
            "approval_required_tools",
            "tool_argument_policies",
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
                "approval_required_tools",
                "tool_argument_policies",
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
            "effective_tool_policy": self.effective_tool_policy_payload(updated),
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
            session_ids_to_delete = {session.session_id for session in matches}
            result = self.destroy_vm(vm_name)
            removed_assistant_mappings = (
                self._delete_chat_assistant_session_mappings(session_ids_to_delete)
                if result.success
                else 0
            )
            message = result.message
            if removed_assistant_mappings:
                message = f"{message} removed_assistant_mappings={removed_assistant_mappings}"
            return {
                "success": result.success,
                "agent_id": vm_name,
                "backend": "libvirt_vm",
                "target_name": vm_name,
                "action": "delete",
                "message": message,
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
        removed_assistant_mappings = self._delete_chat_assistant_session_mappings({session_id})
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
                f"removed_assistant_mappings={removed_assistant_mappings} "
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

    def _container_execution_log_chunk(
        self,
        worker_backend: Any,
        execution: Any,
        *,
        lines: int,
        mode: str,
    ) -> str:
        header_parts = [
            f"execution={execution.execution_id}",
            f"status={self._container_agent_status(execution.last_status)}",
            f"worker_job={execution.worker_job_id}",
            f"log={mode}",
        ]
        if execution.started_at is not None:
            header_parts.append(f"started={execution.started_at.isoformat()}")
        if execution.finished_at is not None:
            header_parts.append(f"finished={execution.finished_at.isoformat()}")
        try:
            text = worker_backend.get_log_snapshot(
                execution.execution_id,
                lines=lines,
                mode=mode,
            ).rstrip()
        except Exception as exc:
            text = f"[log unavailable: {exc}]"
        header = "=== " + " ".join(header_parts) + " ==="
        return header if not text else header + "\n" + text

    @staticmethod
    def _join_container_log_chunks(chunks: list[str]) -> str:
        return "\n\n".join(chunk for chunk in chunks if chunk).rstrip() + "\n"

    def _container_session_log_page(
        self,
        *,
        session_id: str,
        execution_records: list[Any],
        worker_backend: Any,
        limit: int,
        offset: int,
        query: str | None,
    ) -> dict[str, object]:
        if not execution_records:
            return _log_page_from_end(
                f"No container executions have run for session {session_id}.\n",
                limit=limit,
                offset=offset,
                query=query,
            )

        normalized_query = str(query or "").strip()
        sorted_records = sorted(execution_records, key=self._container_execution_sort_key)
        if not normalized_query and offset == 0:
            page = _log_page_from_end(
                self._container_execution_log_chunk(
                    worker_backend,
                    sorted_records[-1],
                    lines=0,
                    mode="augmented",
                ),
                limit=limit,
                offset=0,
            )
            if len(sorted_records) > 1:
                page["has_more_before"] = True
                page["total_lines"] = None
                page["start_line"] = None
                page["end_line"] = None
            return page

        chunks: list[str] = []
        truncated_before = False
        target_lines = max(1, limit) + max(0, offset) + 1
        line_count = 0
        records_to_read = (
            list(enumerate(sorted_records))
            if normalized_query
            else list(reversed(list(enumerate(sorted_records))))
        )
        for original_index, execution in records_to_read:
            mode = "augmented" if original_index == len(sorted_records) - 1 else "raw"
            chunk = self._container_execution_log_chunk(
                worker_backend,
                execution,
                lines=0,
                mode=mode,
            )
            if normalized_query:
                chunks.append(chunk)
                continue
            chunks.insert(0, chunk)
            line_count += len(chunk.splitlines()) + (1 if len(chunks) > 1 else 0)
            if line_count >= target_lines:
                truncated_before = original_index > 0
                break

        page = _log_page_from_end(
            self._join_container_log_chunks(chunks),
            limit=limit,
            offset=offset,
            query=normalized_query or None,
        )
        if truncated_before:
            page["has_more_before"] = True
            page["total_lines"] = None
            page["start_line"] = None
            page["end_line"] = None
        return page

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
                return self._container_session_log_page(
                    session_id=session_id,
                    execution_records=execution_records,
                    worker_backend=worker_backend,
                    limit=normalized_limit,
                    offset=normalized_offset,
                    query=normalized_query or None,
                )
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
            elif action == "prompt-agent":
                return self._prompt_admin_agent(agent_id, backend=selected_backend, payload=body)
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
            if action == "prompt-agent":
                return self._prompt_admin_agent(
                    agent_id,
                    backend=selected_backend,
                    payload=body,
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

    def _prompt_admin_agent(
        self,
        agent_id: str,
        *,
        backend: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        prompt = self._agent_optional_string(payload, "prompt")
        if not prompt:
            raise ValueError("prompt-agent requires a non-empty prompt")
        resume_thread_id = self._agent_optional_string(payload, "resume_thread_id")
        vm_user = self._agent_optional_string(payload, "vm_user") or "agent"

        if backend == "libvirt_vm":
            session = self._resolve_optional_vm_agent_session(agent_id)
            session_id = session.session_id if session is not None else None
            target_name = (
                str((session.vm_id if session is not None else None) or agent_id).strip()
                or agent_id
            )
            directory_path = (
                str((session.directory_path if session is not None else None) or "root").strip()
                or "root"
            )
            agent_response_id = target_name
        elif backend == "container_worker_vm":
            session, record = self._resolve_container_agent_session(agent_id, require_session=True)
            if session is None:
                raise ValueError(f"Unknown container worker agent target: {agent_id}")
            del record
            session_id = session.session_id
            target_name = self._container_session_target_name(session)
            directory_path = str(getattr(session, "directory_path", None) or "root").strip() or "root"
            agent_response_id = session_id
        else:
            raise ValueError(f"Unknown agent backend: {backend}")

        target = session_id or target_name
        effect_id = f"admin-agent-prompt:{backend}:{target}"
        effect = AgentEnqueuePromptEffectDefinition(
            id=effect_id,
            display_name="Admin prompt agent",
            type="effect:agent:enqueue-prompt",
            target=target,
            session_id=session_id,
            vm_name=target_name,
            prompt=prompt,
            resume_thread_id=resume_thread_id,
            vm_user=vm_user,
            queue_key=effect_id,
        )
        provider = EffectProviderFactory(
            provider_config_resolver=self.runtime.provider_config,
            admin_service=self,
        ).build(effect, directory_path=directory_path)
        dispatch_started_at = datetime.now(UTC)
        try:
            result = provider.execute()
        except RuntimeError as exc:
            queued_result = self._queued_admin_prompt_result_after_dispatch_error(
                queue_key=effect_id,
                target_name=target_name,
                session_id=session_id,
                backend=backend,
                attempted_at=dispatch_started_at,
                error=exc,
            )
            if queued_result is None:
                raise
            result = queued_result
        if (
            backend == "container_worker_vm"
            and not bool(result.get("queued"))
            and str(result.get("output_mode") or "").strip() == "on_completion"
        ):
            self._record_direct_container_admin_prompt_execution(
                effect_id=effect_id,
                target=target,
                target_name=target_name,
                session_id=session_id,
                prompt=prompt,
                resume_thread_id=resume_thread_id,
                vm_user=vm_user,
                directory_path=directory_path,
                result=result,
            )
        queued = bool(result.get("queued"))
        message = str(result.get("message") or "").strip()
        if not message:
            message = (
                f"Queued prompt for {target_name}"
                if queued
                else f"Dispatched prompt to {target_name}"
            )
        response = dict(result)
        response.update(
            {
                "success": bool(result.get("success", True)),
                "agent_id": agent_response_id,
                "backend": backend,
                "target_name": target_name,
                "action": "prompt-agent",
                "message": message,
                "status": "queued" if queued else "dispatched",
                "session_id": session_id,
            }
        )
        return response

    def _queued_admin_prompt_result_after_dispatch_error(
        self,
        *,
        queue_key: str,
        target_name: str,
        session_id: str | None,
        backend: str,
        attempted_at: datetime,
        error: RuntimeError,
    ) -> dict[str, Any] | None:
        from agent_sandbox.messaging.agent_prompt_queue import AgentPromptQueueStore

        queued = AgentPromptQueueStore(self.config.runtime_dir / "queued-agent-prompts.json").get(
            queue_key
        )
        if queued is None:
            return None
        queued_updated_at = queued.updated_at
        if queued_updated_at.tzinfo is None:
            queued_updated_at = queued_updated_at.replace(tzinfo=UTC)
        if queued_updated_at < attempted_at - timedelta(seconds=1):
            return None
        queued_session_id = str(queued.session_id or "").strip() or session_id
        queued_target_name = str(queued.vm_name or "").strip() or target_name
        return {
            "success": True,
            "queued": True,
            "queue_key": queued.queue_key,
            "vm_name": queued_target_name,
            "session_id": queued_session_id,
            "coalesced_count": queued.coalesced_count,
            "execution_backend": backend,
            "message": f"Queued prompt for {queued_target_name}",
            "dispatch_error": str(error),
        }

    def _record_direct_container_admin_prompt_execution(
        self,
        *,
        effect_id: str,
        target: str,
        target_name: str,
        session_id: str | None,
        prompt: str,
        resume_thread_id: str | None,
        vm_user: str,
        directory_path: str,
        result: Mapping[str, Any],
    ) -> None:
        dispatch_id = str(result.get("dispatch_id") or "").strip()
        worker_execution_id = str(result.get("execution_id") or "").strip()
        if not dispatch_id or not worker_execution_id or not session_id:
            raise RuntimeError(
                "prompt-agent container dispatch did not include execution tracking identifiers"
            )

        from agent_sandbox.daemons.effect_orchestrator import (
            EffectExecutionRecord,
            JsonExecutionStore,
            SqliteExecutionStore,
        )

        event_id = f"admin-agent-prompt:{session_id}:{dispatch_id}"
        idempotency_key = f"{event_id}::{effect_id}"
        stores = self._workflow_run_store_paths()
        if self._workflow_uses_sqlite_store(stores):
            execution_store = SqliteExecutionStore(Path(stores["orchestration_store_path"]))
        else:
            execution_store = JsonExecutionStore(Path(stores["effect_execution_store_path"]))
        if execution_store.find_execution_by_idempotency_key(idempotency_key) is not None:
            return

        resolved_config = {
            "target": target,
            "vm_name": target_name,
            "session_id": session_id,
            "prompt": prompt,
            "model": result.get("model"),
            "vm_user": vm_user,
            "resume_thread_id": resume_thread_id,
            "directory_path": directory_path,
            "execution_backend": "container_worker_vm",
            "queue_key": effect_id,
        }
        input_payload = {
            "target": target,
            "vm_name": target_name,
            "session_id": session_id,
            "prompt": prompt,
            "model": result.get("model"),
            "vm_user": vm_user,
            "resume_thread_id": resume_thread_id,
            "queue_key": effect_id,
        }
        outputs = dict(result)
        outputs.update(
            {
                "dispatch_id": dispatch_id,
                "thread_id": str(result.get("thread_id") or resume_thread_id or dispatch_id),
                "success": True,
                "queued": False,
                "output_mode": "on_completion",
                "vm_name": target_name,
                "session_id": session_id,
                "execution_backend": "container_worker_vm",
                "execution_id": worker_execution_id,
                "queue_key": effect_id,
            }
        )
        now = datetime.now(UTC)
        execution_store.record_execution(
            EffectExecutionRecord(
                execution_id=(
                    f"exec-{hashlib.sha1(idempotency_key.encode('utf-8')).hexdigest()[:16]}"
                ),
                idempotency_key=idempotency_key,
                event_id=event_id,
                trace_id=f"admin-agent-prompt:{session_id}",
                source_node_id=effect_id,
                source_trigger_type="admin:agent:prompt",
                effect_id=effect_id,
                effect_type="effect:agent:enqueue-prompt",
                status="waiting",
                started_at=now,
                finished_at=now,
                resolved_config_hash=self._stable_json_revision(resolved_config),
                resolved_config=resolved_config,
                input_payload=input_payload,
                outputs=outputs,
            )
        )

    def _resolve_optional_vm_agent_session(self, agent_id: str) -> SessionManifest | None:
        direct_session = self.runtime.session_repository.get_session(agent_id)
        if direct_session is not None:
            return direct_session
        matches = self.runtime.session_repository.list_sessions_for_vm(agent_id)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            session_ids = ", ".join(sorted(session.session_id for session in matches))
            raise ValueError(f"VM {agent_id!r} is associated with multiple sessions: {session_ids}")
        return None

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
        persist_thread_id: bool = True,
        mcp_gateway_url: str | None = None,
    ) -> VmActionResult:
        return self.vm_agent_admin.resume_agent(
            target,
            prompt,
            model=model,
            vm_user=vm_user,
            dispatch_id=dispatch_id,
            resume_thread_id=resume_thread_id,
            resume_mode=resume_mode,
            persist_thread_id=persist_thread_id,
            mcp_gateway_url=mcp_gateway_url,
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

    def list_published_artifacts(
        self,
        *,
        key: str | None = None,
        published_after: datetime | None = None,
        published_before: datetime | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> PublishedArtifactListPayload:
        normalized_limit = max(1, min(int(limit or 50), 200))
        try:
            offset = int(str(cursor or "0").strip() or "0")
        except ValueError as exc:
            raise ValueError("cursor must be a non-negative integer offset") from exc
        if offset < 0:
            raise ValueError("cursor must be a non-negative integer offset")
        records = self._published_artifact_store().list_metadata(
            key=key,
            published_after=published_after,
            published_before=published_before,
        )
        page = records[offset : offset + normalized_limit]
        next_offset = offset + normalized_limit
        return PublishedArtifactListPayload(
            items=page,
            limit=normalized_limit,
            total=len(records),
            next_cursor=str(next_offset) if next_offset < len(records) else None,
        )

    def get_published_artifact(self, key: str) -> PublishedArtifactMetadata:
        return self._published_artifact_store().get(validate_artifact_key(key)).metadata

    def published_artifact_file(self, key: str) -> PublishedArtifact:
        return self._published_artifact_store().get(validate_artifact_key(key))

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
        return self.local_issue_identity_resolver.find_by_remote_identifier(issue_identifier)

    def _resolve_local_issue_record(self, issue_identifier: str) -> LocalIssueRecord:
        return self.local_issue_identity_resolver.resolve_record(issue_identifier)

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
            if isinstance(payload, dict) and _approval_audit_payload_matches_request(
                payload, request
            ):
                matches.append(payload)
            if len(matches) >= limit:
                break
        matches.reverse()
        return matches

    def list_tool_call_audit_entries(
        self,
        *,
        session_id: str | None = None,
        tool_name: str | None = None,
        status: str | None = None,
        directory_path: str | None = None,
        direct_only: bool = False,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        q: str | None = None,
        limit: int | None = None,
        recent_only: bool = False,
    ) -> ToolCallAuditListPayload:
        normalized_limit = 100 if limit is None else max(1, min(int(limit), 500))
        normalized_status = _tool_call_normalize_status(status)
        normalized_directory_path = (
            self._agent_record_directory_path(directory_path)
            if directory_path is not None
            else None
        )
        if recent_only and _tool_call_audit_tail_scan_supported(
            session_id=session_id,
            tool_name=tool_name,
            status=normalized_status,
            created_after=created_after,
            created_before=created_before,
            q=q,
            limit=normalized_limit,
        ):
            return self._list_recent_tool_call_audit_entries_from_tail(
                session_id=session_id,
                tool_name=tool_name,
                status=normalized_status,
                directory_path=normalized_directory_path,
                direct_only=direct_only,
                created_after=created_after,
                created_before=created_before,
                q=q,
                limit=normalized_limit,
            )
        entries: deque[ToolCallAuditEntryPayload] = deque(maxlen=normalized_limit)
        counts = ToolCallAuditCountsPayload()
        if not self.config.audit_log_path.exists():
            return ToolCallAuditListPayload(limit=normalized_limit)
        try:
            with self.config.audit_log_path.open(encoding="utf-8") as handle:
                for raw_line in handle:
                    line = raw_line.strip()
                    if not line:
                        continue
                    counts.scanned += 1
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        counts.skipped_malformed += 1
                        continue
                    if not isinstance(payload, dict):
                        counts.skipped_malformed += 1
                        continue
                    event_type = str(payload.get("event_type") or "").strip().casefold()
                    if event_type != "tool_call":
                        counts.skipped_non_tool_call += 1
                        continue
                    entry = _tool_call_audit_entry_from_payload(payload)
                    if entry is None:
                        counts.skipped_malformed += 1
                        continue
                    if not _tool_call_audit_entry_matches_filters(
                        entry,
                        session_id=session_id,
                        tool_name=tool_name,
                        status=normalized_status,
                        directory_path=normalized_directory_path,
                        direct_only=direct_only,
                        created_after=created_after,
                        created_before=created_before,
                        q=q,
                    ):
                        continue
                    counts.matched += 1
                    counts.status_counts[entry.status or "success"] = (
                        counts.status_counts.get(entry.status or "success", 0) + 1
                    )
                    entries.append(entry)
        except Exception:
            return ToolCallAuditListPayload(limit=normalized_limit)
        items = list(entries)
        items.reverse()
        counts.returned = len(items)
        return ToolCallAuditListPayload(
            items=items,
            counts=ToolCallAuditCountsPayload(
                scanned=counts.scanned,
                matched=counts.matched,
                returned=counts.returned,
                skipped_malformed=counts.skipped_malformed,
                skipped_non_tool_call=counts.skipped_non_tool_call,
                status_counts=dict(sorted(counts.status_counts.items())),
            ),
            limit=normalized_limit,
            has_more=counts.matched > len(items),
        )

    def _list_recent_tool_call_audit_entries_from_tail(
        self,
        *,
        session_id: str | None,
        tool_name: str | None,
        status: str | None,
        directory_path: str | None,
        direct_only: bool,
        created_after: datetime | None,
        created_before: datetime | None,
        q: str | None,
        limit: int,
    ) -> ToolCallAuditListPayload:
        if not self.config.audit_log_path.exists():
            return ToolCallAuditListPayload(limit=limit)

        counts = ToolCallAuditCountsPayload()
        items: list[ToolCallAuditEntryPayload] = []
        has_more = False
        try:
            for line in _iter_file_lines_reverse(self.config.audit_log_path):
                line = line.strip()
                if not line:
                    continue
                counts.scanned += 1
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    counts.skipped_malformed += 1
                    continue
                if not isinstance(payload, dict):
                    counts.skipped_malformed += 1
                    continue
                event_type = str(payload.get("event_type") or "").strip().casefold()
                if event_type != "tool_call":
                    counts.skipped_non_tool_call += 1
                    continue
                entry = _tool_call_audit_entry_from_payload(payload)
                if entry is None:
                    counts.skipped_malformed += 1
                    continue
                if not _tool_call_audit_entry_matches_filters(
                    entry,
                    session_id=session_id,
                    tool_name=tool_name,
                    status=status,
                    directory_path=directory_path,
                    direct_only=direct_only,
                    created_after=created_after,
                    created_before=created_before,
                    q=q,
                ):
                    continue
                counts.matched += 1
                if len(items) >= limit:
                    has_more = True
                    break
                counts.status_counts[entry.status or "success"] = (
                    counts.status_counts.get(entry.status or "success", 0) + 1
                )
                items.append(entry)
        except Exception:
            return ToolCallAuditListPayload(limit=limit)

        counts.returned = len(items)
        return ToolCallAuditListPayload(
            items=items,
            counts=ToolCallAuditCountsPayload(
                scanned=counts.scanned,
                matched=counts.matched,
                returned=counts.returned,
                skipped_malformed=counts.skipped_malformed,
                skipped_non_tool_call=counts.skipped_non_tool_call,
                status_counts=dict(sorted(counts.status_counts.items())),
            ),
            limit=limit,
            has_more=has_more,
        )

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
        return self.local_issue_identity_resolver.resolve_key(identifier)

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

    def write_worker_global_config(
        self,
        worker_id: str,
        payload: dict[str, object],
        *,
        expected_revision: str | None = None,
    ):
        return self.workflow_config_admin.write_worker_global_config(
            worker_id, payload, expected_revision=expected_revision
        )

    def read_workflow_editor_document(self) -> WorkflowEditorDocumentPayload:
        return self.workflow_config_admin.read_workflow_editor_document()

    def read_workflow_editor_catalog(
        self, directory_path: str | None = None
    ) -> WorkflowEditorCatalogPayload:
        return self.workflow_config_admin.read_workflow_editor_catalog(
            directory_path=directory_path
        )

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
        expected_revision: str | None = None,
    ) -> WorkflowEditorSavePayload:
        return self.workflow_config_admin.save_workflow_editor_document(
            config=config, layout=layout, graphs=graphs, expected_revision=expected_revision
        )

    def read_workflow_run_snapshot(self) -> WorkflowRunSnapshotPayload:
        return self.workflow_config_admin.read_workflow_run_snapshot()

    def read_workflow_run_detail(
        self,
        *,
        run_id: str | None = None,
        event_id: str | None = None,
        trigger_event_id: str | None = None,
        trace_id: str | None = None,
        session_id: str | None = None,
        artifact_id: str | None = None,
    ) -> WorkflowRunDetailPayload:
        return self.workflow_config_admin.read_workflow_run_detail(
            run_id=run_id,
            event_id=event_id,
            trigger_event_id=trigger_event_id,
            trace_id=trace_id,
            session_id=session_id,
            artifact_id=artifact_id,
        )

    def read_workflow_sample_state(self) -> WorkflowSampleStatePayload:
        with self._workflow_sample_lock:
            active = self._workflow_sample_active
            launched_at = self._workflow_sample_launched_at
        return self._workflow_sample_state_payload(
            active=active,
            launched_at=launched_at,
        )

    def launch_workflow_sample(self) -> WorkflowSampleStatePayload:
        with self._workflow_sample_lock:
            if not self._workflow_sample_active or self._workflow_sample_launched_at is None:
                self._workflow_sample_active = True
                self._workflow_sample_launched_at = _utc_now()
            active = self._workflow_sample_active
            launched_at = self._workflow_sample_launched_at
        return self._workflow_sample_state_payload(
            active=active,
            launched_at=launched_at,
        )

    def reset_workflow_sample(self) -> WorkflowSampleStatePayload:
        with self._workflow_sample_lock:
            self._workflow_sample_active = False
            self._workflow_sample_launched_at = None
        return self._workflow_sample_state_payload(
            active=False,
            launched_at=None,
        )

    def read_workflow_run_search(
        self,
        *,
        node_id: str | None = None,
        graph_id: str | None = None,
        without_session: bool = False,
        include_completed: bool = True,
        limit: int | None = None,
    ) -> WorkflowRunSearchPayload:
        return self.workflow_config_admin.read_workflow_run_search(
            node_id=node_id,
            graph_id=graph_id,
            without_session=without_session,
            include_completed=include_completed,
            limit=limit,
        )

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

    def run_conformance_report(self, kind: str) -> ConformanceReportRunPayload:
        return self._run_conformance_report(kind)

    def clear_workflow_run(self, event_id: str) -> WorkflowRunClearPayload:
        return self.workflow_config_admin.clear_workflow_run(event_id)

    def dismiss_workflow_node_failures(self, node_id: str) -> WorkflowNodeFailuresDismissPayload:
        return self.workflow_config_admin.dismiss_workflow_node_failures(node_id)

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

    @contextmanager
    def admin_config_mutation_lock(self):
        path = self.admin_config_mutation_lock_path
        path.parent.mkdir(parents=True, exist_ok=True)
        with _ADMIN_CONFIG_MUTATION_THREAD_LOCK:
            with path.open("a+", encoding="utf-8") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _worker_global_config_path(self, worker_id: str) -> Path | None:
        relative = _WORKER_GLOBAL_CONFIG_PATHS.get(worker_id)
        if relative is None:
            return None
        return self.project_dir / relative

    def _stable_json_revision(self, payload: object) -> str:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _worker_global_config_revision(self, config: dict[str, object]) -> str:
        return self._stable_json_revision(config)

    def _read_worker_global_config_unlocked(self, worker_id: str) -> WorkerGlobalConfigPayload:
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
            worker_id=worker_id,
            path=str(path),
            exists=exists,
            config=config,
            revision=self._worker_global_config_revision(config),
        )

    def _read_worker_global_config(self, worker_id: str) -> WorkerGlobalConfigPayload:
        return self._read_worker_global_config_unlocked(worker_id)

    def _write_worker_global_config_unlocked(
        self, worker_id: str, payload: dict[str, object]
    ) -> WorkerGlobalConfigWritePayload:
        path = self._worker_global_config_path(worker_id)
        if path is None:
            raise ValueError(f"Unknown worker: {worker_id}")
        config = self._validate_worker_global_config_payload(worker_id, payload)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config, indent=2, sort_keys=True), encoding="utf-8")
        return WorkerGlobalConfigWritePayload(
            worker_id=worker_id,
            path=str(path),
            config=config,
            revision=self._worker_global_config_revision(config),
        )

    def _write_worker_global_config(
        self,
        worker_id: str,
        payload: dict[str, object],
        *,
        expected_revision: str | None = None,
    ) -> WorkerGlobalConfigWritePayload:
        with self.admin_config_mutation_lock():
            if expected_revision is not None:
                current = self._read_worker_global_config_unlocked(worker_id)
                if current.revision != expected_revision:
                    raise AdminRevisionConflict(
                        "Worker configuration changed before save. Reload the worker settings before saving again.",
                        details={
                            "current_revision": current.revision,
                            "current": current.model_dump(mode="json"),
                        },
                    )
            return self._write_worker_global_config_unlocked(worker_id, payload)

    def _read_trigger_document(self) -> TriggerConfigDocument:
        payload = self._read_worker_global_config("trigger-orchestrator")
        return TriggerConfigDocument(path=payload.path, config=payload.config)

    def _write_trigger_document(self, config: dict[str, object]) -> TriggerConfigDocument:
        payload = self._write_worker_global_config("trigger-orchestrator", config)
        return TriggerConfigDocument(path=payload.path, config=payload.config)

    def _write_trigger_document_unlocked(
        self, config: dict[str, object]
    ) -> TriggerConfigDocument:
        payload = self._write_worker_global_config_unlocked("trigger-orchestrator", config)
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
        return self.trigger_repository.read_document()

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

    def publish_external_workflow_event(
        self,
        *,
        event_type: object,
        payload: dict[str, Any],
        correlation_key: object | None = None,
        idempotency_key: str | None = None,
        source: InternalEventSource | dict[str, Any] | None = None,
    ) -> dict[str, object]:
        event_name = str(event_type or "").strip()
        if not event_name:
            raise ValueError("event_type is required")
        if event_name.startswith(EXTERNAL_WORKFLOW_EVENT_PREFIX):
            raise ValueError(
                f"event_type must not include the reserved {EXTERNAL_WORKFLOW_EVENT_PREFIX!r} prefix"
            )
        validate_internal_event_type(event_name)
        if not isinstance(payload, dict):
            raise ValueError("payload must be a JSON object")

        worker_config_payload = self._read_worker_global_config("trigger-orchestrator")
        worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
            worker_config_payload.config
        )
        bus = InternalEventBus(
            worker_config.internal_event_store_path,
            journal_path=worker_config.workflow_event_journal_path,
        )
        event = bus.publish(
            event_type=f"{EXTERNAL_WORKFLOW_EVENT_PREFIX}{event_name}",
            payload=payload,
            correlation_key=correlation_key,
            source=source,
            idempotency_key=idempotency_key,
        )
        return event.model_dump(mode="json")

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

    def _workflow_editor_revision(
        self,
        *,
        config: dict[str, object],
        layout: dict[str, object],
        graphs: list[WorkflowEditorGraphMetadata],
    ) -> str:
        return self._stable_json_revision(
            {
                "config": config,
                "layout": layout,
                "graphs": [graph.model_dump(mode="json") for graph in graphs],
            }
        )

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
        revision = self._workflow_editor_revision(
            config=normalized_config,
            layout=layout,
            graphs=graphs,
        )
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

    def _communication_target_options(
        self, directory_path: str | None, *, purpose: str
    ) -> list[WorkflowEditorFieldOption]:
        normalized_directory = self._normalize_directory_reference(directory_path)
        result = self.directory_admin.compute_effective_config(
            normalized_directory,
            domain=COMMUNICATION_TARGETS_DOMAIN,
        )
        if not result.ok or result.data is None:
            messages = [error.message for error in result.errors]
            raise ValueError(
                "; ".join(messages)
                or f"Communication targets could not be resolved for {normalized_directory!r}."
            )
        targets = result.data.resolution.effective_config.get("targets")
        if not isinstance(targets, list):
            return []

        options: list[WorkflowEditorFieldOption] = []
        for target in targets:
            if not isinstance(target, dict):
                continue
            provider = str(target.get("provider") or "").strip()
            capabilities = set(_communication_target_capabilities(target))
            if purpose == "send-message":
                if provider == "teams" and not capabilities.intersection({"write", "reply"}):
                    continue
                if provider == "discord" and "write" not in capabilities:
                    continue
                if provider not in {"teams", "discord"}:
                    continue
            elif purpose == "notification":
                if provider not in {"teams", "discord", "email"}:
                    continue
                if "notify" not in capabilities:
                    continue
            else:
                continue
            option = _communication_target_option(target, purpose=purpose)
            if option is not None:
                options.append(option)
        return options

    def _communication_send_target_options(
        self, directory_path: str | None
    ) -> list[WorkflowEditorFieldOption]:
        return self._communication_target_options(directory_path, purpose="send-message")

    def _communication_notification_target_options(
        self, directory_path: str | None
    ) -> list[WorkflowEditorFieldOption]:
        return self._communication_target_options(directory_path, purpose="notification")

    def _read_workflow_editor_catalog(
        self, directory_path: str | None = None
    ) -> WorkflowEditorCatalogPayload:
        skill_options = [_field_option(skill.name) for skill in self.skill_library.list_skills()]
        tool_options = _tool_field_options()
        communication_send_target_options = self._communication_send_target_options(
            directory_path
        )
        communication_notification_target_options = (
            self._communication_notification_target_options(directory_path)
        )
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
            if node.type == "effect:communication:send-message":
                node.fields = [
                    field.model_copy(update={"options": communication_send_target_options})
                    if field.key == "communication_target_id"
                    else field
                    for field in node.fields
                ]
            if node.type == "effect:notification:send":
                node.fields = [
                    field.model_copy(update={"options": communication_notification_target_options})
                    if field.key == "communication_target_id"
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
        runtime_validation_config = _append_builtin_hidden_workflows(
            project_dir=self.project_dir,
            payload=merged_config,
        )
        normalized_candidate = self._normalize_workflow_editor_config(runtime_validation_config)
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
                diagnostic.model_dump(mode="json", exclude_none=True)
                for diagnostic in diagnostics
            ]
            + [
                diagnostic.model_dump(mode="json", exclude_none=True)
                for diagnostic in binding_diagnostics
            ],
        )

    def _save_workflow_editor_document_unlocked(
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
        revision = self._workflow_editor_revision(
            config=validation.config,
            layout=validation.layout,
            graphs=validation.graphs,
        )
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

    def _save_workflow_editor_document(
        self,
        *,
        config: dict[str, object],
        layout: dict[str, object] | None = None,
        graphs: list[dict[str, object]] | None = None,
        expected_revision: str | None = None,
    ) -> WorkflowEditorSavePayload:
        with self.admin_config_mutation_lock():
            if expected_revision is not None:
                current = self._read_workflow_editor_document()
                if current.revision != expected_revision:
                    raise AdminRevisionConflict(
                        "Workflow editor document changed before save. Reload the workflow before saving again.",
                        details={
                            "current_revision": current.revision,
                            "current": current.model_dump(mode="json"),
                        },
                    )
            return self._save_workflow_editor_document_unlocked(
                config=config,
                layout=layout,
                graphs=graphs,
            )

    def _seed_new_ticket_created_trigger_cursors(
        self, *, previous_config: dict[str, object], next_config: dict[str, object]
    ) -> None:
        runtime_dir = _resolve_path_setting(
            base_dir=self.project_dir,
            value=next_config.get("runtime_dir"),
            fallback=self.config.runtime_dir,
        )
        seed_new_ticket_created_trigger_cursors(
            runtime_dir=runtime_dir,
            previous_config=previous_config,
            next_config=next_config,
        )

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

        await_agent_payload = _read_merged_json_objects(
            runtime_dir / "await-agent-pending.json",
            runtime_dir / "providers" / "gates" / "await-agent" / "pending.json",
        )
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

    def _read_workflow_run_search(
        self,
        *,
        node_id: str | None = None,
        graph_id: str | None = None,
        without_session: bool = False,
        include_completed: bool = True,
        limit: int | None = None,
    ) -> WorkflowRunSearchPayload:
        normalized_node_id = _coerce_optional_string(node_id)
        normalized_graph_id = _coerce_optional_string(graph_id)
        normalized_limit = max(1, min(100, int(limit or 25)))
        document = self._read_workflow_editor_document()

        target_node_ids: set[str] = set()
        graph_name: str | None = None
        if normalized_graph_id is not None:
            graph = next(
                (candidate for candidate in document.graphs if candidate.id == normalized_graph_id),
                None,
            )
            if graph is None:
                raise ValueError(f"Workflow graph not found: {normalized_graph_id}")
            target_node_ids.update(graph.node_ids)
            graph_name = graph.name
        if normalized_node_id is not None:
            target_node_ids.add(normalized_node_id)
        if not target_node_ids:
            raise ValueError("Workflow run search requires node_id or graph_id.")

        stores = self._workflow_run_store_paths()
        _, effect_store, gate_store = self._workflow_run_store_bundle(stores)
        queue_records = self._workflow_run_event_records(stores)
        effect_records = effect_store.read_all()
        gate_records = gate_store.read_all()
        await_agent_payload = _read_merged_json_objects(
            stores["legacy_await_agent_store_path"],
            stores["await_agent_store_path"],
        )
        approval_requests = self.runtime.approvals.read_all()
        artifact_store = self._communication_artifact_store()

        def normalize_record_payload(record: object) -> dict[str, Any]:
            if isinstance(record, BaseModel):
                return record.model_dump(mode="json")
            if isinstance(record, dict):
                return dict(record)
            return {}

        def latest_of(*values: object) -> datetime | None:
            parsed = [
                parsed_value
                for parsed_value in (_coerce_optional_datetime(value) for value in values)
                if parsed_value is not None
            ]
            return max(parsed) if parsed else None

        def ensure_group(groups: dict[str, dict[str, Any]], event_key: str) -> dict[str, Any]:
            group = groups.get(event_key)
            if group is None:
                group = {
                    "event_id": event_key,
                    "trace_ids": set(),
                    "session_ids": set(),
                    "artifact_ids": set(),
                    "request_ids": set(),
                    "node_ids": set(),
                    "activities": [],
                    "status_counts": {},
                    "occurred_at": None,
                    "latest_activity_at": None,
                    "source_node_id": None,
                    "source_trigger_type": None,
                    "delivery_state": None,
                    "error": None,
                }
                groups[event_key] = group
            return group

        def update_group_metadata(
            group: dict[str, Any],
            *,
            trace_id_value: str | None = None,
            session_ids: list[str] | None = None,
            artifact_ids: list[str] | None = None,
            request_ids: list[str] | None = None,
            occurred_at_value: datetime | None = None,
            latest_activity_at_value: datetime | None = None,
            source_node_id: str | None = None,
            source_trigger_type: str | None = None,
            delivery_state: str | None = None,
            error: str | None = None,
        ) -> None:
            if trace_id_value:
                group["trace_ids"].add(trace_id_value)
            for value in session_ids or []:
                group["session_ids"].add(value)
            for value in artifact_ids or []:
                group["artifact_ids"].add(value)
            for value in request_ids or []:
                group["request_ids"].add(value)
            if occurred_at_value is not None and group.get("occurred_at") is None:
                group["occurred_at"] = occurred_at_value
            previous_latest = group.get("latest_activity_at")
            if latest_activity_at_value is not None and (
                not isinstance(previous_latest, datetime) or latest_activity_at_value > previous_latest
            ):
                group["latest_activity_at"] = latest_activity_at_value
            if source_node_id and not group.get("source_node_id"):
                group["source_node_id"] = source_node_id
            if source_trigger_type and not group.get("source_trigger_type"):
                group["source_trigger_type"] = source_trigger_type
            if delivery_state and not group.get("delivery_state"):
                group["delivery_state"] = delivery_state
            if error and not group.get("error"):
                group["error"] = error

        def register_activity(
            groups: dict[str, dict[str, Any]],
            *,
            event_key: str,
            node_id_value: str | None,
            status: str | None,
            source: str,
            trace_id_value: str | None = None,
            session_payloads: list[Any] | None = None,
            artifact_payloads: list[Any] | None = None,
            request_payloads: list[Any] | None = None,
            occurred_at_value: datetime | None = None,
            started_at_value: datetime | None = None,
            finished_at_value: datetime | None = None,
            latest_activity_at_value: datetime | None = None,
            source_node_id: str | None = None,
            source_trigger_type: str | None = None,
            delivery_state: str | None = None,
            dispatch_id: str | None = None,
            error: str | None = None,
        ) -> None:
            normalized_activity_node_id = _coerce_optional_string(node_id_value)
            if normalized_activity_node_id is None:
                return
            normalized_status = (status or "unknown").strip() or "unknown"
            group = ensure_group(groups, event_key)
            group["node_ids"].add(normalized_activity_node_id)
            status_counts = group["status_counts"]
            status_counts[normalized_status] = status_counts.get(normalized_status, 0) + 1
            group["activities"].append(
                WorkflowRunNodeActivityPayload(
                    node_id=normalized_activity_node_id,
                    status=normalized_status,
                    source=source,
                    started_at=started_at_value,
                    finished_at=finished_at_value,
                    dispatch_id=dispatch_id,
                    error=error,
                )
            )
            update_group_metadata(
                group,
                trace_id_value=trace_id_value,
                session_ids=[
                    value
                    for payload in session_payloads or []
                    for value in _collect_workflow_keyed_values(payload, key_suffix="session_id")
                ],
                artifact_ids=[
                    value
                    for payload in artifact_payloads or []
                    for value in _collect_workflow_artifact_ids(payload)
                ],
                request_ids=[
                    value
                    for payload in request_payloads or []
                    for value in _collect_workflow_keyed_values(payload, key_suffix="request_id")
                ],
                occurred_at_value=occurred_at_value,
                latest_activity_at_value=latest_activity_at_value
                or latest_of(finished_at_value, started_at_value, occurred_at_value),
                source_node_id=source_node_id,
                source_trigger_type=source_trigger_type,
                delivery_state=delivery_state,
                error=error,
            )

        groups: dict[str, dict[str, Any]] = {}
        for record_key, record in queue_records.items():
            raw_record = normalize_record_payload(record)
            if not raw_record:
                continue
            event_id = _coerce_optional_string(raw_record.get("event_id")) or str(record_key)
            occurred_at = _coerce_optional_datetime(raw_record.get("occurred_at"))
            register_activity(
                groups,
                event_key=event_id,
                node_id_value=_coerce_optional_string(raw_record.get("trigger_id")),
                status=_coerce_optional_string(raw_record.get("delivery_state")) or "pending",
                source="trigger_event",
                trace_id_value=_coerce_optional_string(raw_record.get("trace_id")),
                session_payloads=[raw_record],
                artifact_payloads=[raw_record.get("payload")],
                request_payloads=[raw_record],
                occurred_at_value=occurred_at,
                started_at_value=_coerce_optional_datetime(raw_record.get("inflight_at")),
                finished_at_value=_coerce_optional_datetime(raw_record.get("delivered_at")),
                latest_activity_at_value=latest_of(
                    raw_record.get("delivered_at"),
                    raw_record.get("inflight_at"),
                    raw_record.get("occurred_at"),
                ),
                source_node_id=_coerce_optional_string(raw_record.get("trigger_id")),
                source_trigger_type=_coerce_optional_string(raw_record.get("trigger_type")),
                delivery_state=_coerce_optional_string(raw_record.get("delivery_state")),
                error=_coerce_optional_string(raw_record.get("error")),
            )

        for execution_id, record in effect_records.items():
            raw_record = normalize_record_payload(record)
            if not raw_record:
                continue
            event_id = _coerce_optional_string(raw_record.get("event_id")) or str(execution_id)
            payload_sources = [
                raw_record.get("resolved_config"),
                raw_record.get("input_payload"),
                raw_record.get("outputs"),
            ]
            register_activity(
                groups,
                event_key=event_id,
                node_id_value=_coerce_optional_string(raw_record.get("effect_id")),
                status=_coerce_optional_string(raw_record.get("status")),
                source="effect_execution",
                trace_id_value=_coerce_optional_string(raw_record.get("trace_id")),
                session_payloads=payload_sources,
                artifact_payloads=payload_sources,
                request_payloads=payload_sources,
                started_at_value=_coerce_optional_datetime(raw_record.get("started_at")),
                finished_at_value=_coerce_optional_datetime(raw_record.get("finished_at")),
                source_node_id=_coerce_optional_string(raw_record.get("source_node_id")),
                source_trigger_type=_coerce_optional_string(raw_record.get("source_trigger_type")),
                error=_coerce_optional_string(raw_record.get("error")),
            )

        for execution_id, record in gate_records.items():
            raw_record = normalize_record_payload(record)
            if not raw_record:
                continue
            event_id = _coerce_optional_string(raw_record.get("event_id")) or str(execution_id)
            payload_sources = [
                raw_record.get("resolved_config"),
                raw_record.get("input_payload"),
                raw_record.get("outputs"),
                raw_record.get("inputs_by_source"),
            ]
            register_activity(
                groups,
                event_key=event_id,
                node_id_value=_coerce_optional_string(raw_record.get("gate_id")),
                status=_coerce_optional_string(raw_record.get("status")),
                source="gate_execution",
                trace_id_value=_coerce_optional_string(raw_record.get("trace_id")),
                session_payloads=payload_sources,
                artifact_payloads=payload_sources,
                request_payloads=payload_sources,
                started_at_value=_coerce_optional_datetime(raw_record.get("started_at")),
                finished_at_value=_coerce_optional_datetime(raw_record.get("finished_at")),
                source_node_id=_coerce_optional_string(raw_record.get("source_node_id")),
                source_trigger_type=_coerce_optional_string(raw_record.get("source_trigger_type")),
                error=_coerce_optional_string(raw_record.get("error")),
            )

        for record_key, raw_record in await_agent_payload.items():
            if not isinstance(raw_record, dict):
                continue
            event_id = _coerce_optional_string(raw_record.get("prompt_event_id")) or str(record_key)
            register_activity(
                groups,
                event_key=event_id,
                node_id_value=_coerce_optional_string(raw_record.get("gate_id")),
                status="waiting",
                source="await_agent",
                trace_id_value=_coerce_optional_string(raw_record.get("trace_id")),
                session_payloads=[raw_record],
                artifact_payloads=[raw_record],
                request_payloads=[raw_record],
                occurred_at_value=_coerce_optional_datetime(raw_record.get("occurred_at")),
                started_at_value=_coerce_optional_datetime(raw_record.get("occurred_at")),
                source_node_id=_coerce_optional_string(raw_record.get("prompt_source_node_id")),
                source_trigger_type="trigger:agent:run:completed",
                dispatch_id=_coerce_optional_string(raw_record.get("dispatch_id")),
            )

        for group in groups.values():
            for request_id in list(group["request_ids"]):
                request = approval_requests.get(request_id)
                if request is not None:
                    group["session_ids"].add(request.session_id)
            for artifact_id in list(group["artifact_ids"]):
                try:
                    artifact_payload = artifact_store.payload(artifact_id)
                except FileNotFoundError:
                    continue
                artifact_session_id = _coerce_optional_string(artifact_payload.get("session_id"))
                if artifact_session_id:
                    group["session_ids"].add(artifact_session_id)

        session_ids_by_trace: dict[str, set[str]] = {}
        for group in groups.values():
            for trace_id_value in group["trace_ids"]:
                if trace_id_value:
                    session_ids_by_trace.setdefault(trace_id_value, set()).update(group["session_ids"])

        matched_runs: list[WorkflowRunSearchItemPayload] = []
        active_statuses = {"pending", "inflight", "running", "waiting"}
        for group in groups.values():
            matched_node_ids = sorted(group["node_ids"] & target_node_ids)
            if not matched_node_ids:
                continue
            effective_session_ids = set(group["session_ids"])
            for trace_id_value in group["trace_ids"]:
                effective_session_ids.update(session_ids_by_trace.get(trace_id_value, set()))
            if without_session and effective_session_ids:
                continue
            status_counts = dict(sorted(group["status_counts"].items()))
            if status_counts.get("failed", 0) > 0:
                run_state = "errored"
            elif any(status_counts.get(status, 0) > 0 for status in active_statuses):
                run_state = "active"
            else:
                run_state = "completed"
            if not include_completed and run_state == "completed":
                continue
            matched_runs.append(
                WorkflowRunSearchItemPayload(
                    event_id=str(group["event_id"]),
                    trace_id=next((value for value in sorted(group["trace_ids"]) if value), None),
                    run_state=run_state,
                    source_node_id=_coerce_optional_string(group.get("source_node_id")),
                    source_trigger_type=_coerce_optional_string(group.get("source_trigger_type")),
                    delivery_state=_coerce_optional_string(group.get("delivery_state")),
                    occurred_at=group.get("occurred_at"),
                    latest_activity_at=group.get("latest_activity_at"),
                    node_ids=sorted(group["node_ids"]),
                    matched_node_ids=matched_node_ids,
                    session_ids=sorted(effective_session_ids),
                    has_agent_session=bool(effective_session_ids),
                    status_counts=status_counts,
                    nodes=_merge_run_node_activity(group["activities"]),
                    error=_coerce_optional_string(group.get("error")),
                )
            )

        matched_runs.sort(
            key=lambda run: (
                run.latest_activity_at or run.occurred_at or datetime.min.replace(tzinfo=UTC),
                run.occurred_at or datetime.min.replace(tzinfo=UTC),
                run.event_id,
            ),
            reverse=True,
        )
        limited_runs = matched_runs[:normalized_limit]
        return WorkflowRunSearchPayload(
            generated_at=_utc_now(),
            filters=WorkflowRunSearchFilterPayload(
                node_id=normalized_node_id,
                graph_id=normalized_graph_id,
                graph_name=graph_name,
                without_session=without_session,
                include_completed=include_completed,
            ),
            runs=limited_runs,
            matched=len(matched_runs),
            returned=len(limited_runs),
            limit=normalized_limit,
            has_more=len(matched_runs) > normalized_limit,
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

    def _dismiss_workflow_node_failures(self, node_id: str) -> WorkflowNodeFailuresDismissPayload:
        normalized_node_id = str(node_id or "").strip()
        if not normalized_node_id:
            raise ValueError("Workflow node_id is required.")

        stores = self._workflow_run_store_paths()
        dismissed_at = _utc_now()
        dismiss_reason = "manual_dismiss_node_failures"

        def _dismiss_failed_records(records: dict[str, Any], *, node_id_field: str) -> int:
            updated = 0
            for execution_id, record in records.items():
                record_node_id = str(getattr(record, node_id_field, "") or "").strip()
                if record_node_id != normalized_node_id:
                    continue
                status = str(getattr(record, "status", "") or "").strip()
                if status != "failed" or getattr(record, "dismissed_at", None):
                    continue
                setattr(record, "dismissed_at", dismissed_at)
                setattr(record, "dismiss_reason", dismiss_reason)
                records[execution_id] = record
                updated += 1
            return updated

        if self._workflow_uses_sqlite_store(stores):
            _, effect_store, gate_store = self._workflow_sqlite_store_bundle(stores)
        else:
            from agent_sandbox.daemons.effect_orchestrator import (
                JsonExecutionStore,
                JsonGateExecutionStore,
            )

            effect_store = JsonExecutionStore(stores["effect_execution_store_path"])
            gate_store = JsonGateExecutionStore(stores["gate_execution_store_path"])

        effect_records_updated = effect_store.update_all(
            lambda records: _dismiss_failed_records(records, node_id_field="effect_id")
        )
        gate_records_updated = gate_store.update_all(
            lambda records: _dismiss_failed_records(records, node_id_field="gate_id")
        )

        if effect_records_updated or gate_records_updated:
            self._workflow_event_journal().append(
                "workflow.node_failures.dismissed",
                source="workflow_editor",
                subject_id=normalized_node_id,
                payload={
                    "effect_records_updated": effect_records_updated,
                    "gate_records_updated": gate_records_updated,
                },
            )

        return WorkflowNodeFailuresDismissPayload(
            node_id=normalized_node_id,
            dismissed_at=dismissed_at,
            effect_records_updated=effect_records_updated,
            gate_records_updated=gate_records_updated,
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
        workflow_config_snapshot_store_path = _resolve_path_setting(
            base_dir=self.project_dir,
            value=worker_config.get("workflow_config_snapshot_store_path"),
            fallback=runtime_dir / "workflow-config-snapshots.json",
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
            "workflow_config_snapshot_store_path": workflow_config_snapshot_store_path,
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
            start_text = datetime_to_storage(start_at)
            end_text = datetime_to_storage(end_at)
            db_path = Path(stores["orchestration_store_path"])
            ensure_orchestration_schema(db_path)
            with connect_orchestration_db(db_path) as connection:
                for (
                    event_id,
                    trigger_id,
                    trace_id,
                    occurred_at,
                    delivery_state,
                ) in connection.execute(
                    """
                    SELECT event_id, trigger_id, trace_id, occurred_at, delivery_state
                    FROM trigger_events
                    WHERE occurred_at BETWEEN ? AND ?
                    ORDER BY occurred_at ASC, event_id ASC
                    """,
                    (start_text, end_text),
                ):
                    register_activity(
                        node_id=str(trigger_id or ""),
                        timestamp=_coerce_optional_datetime(occurred_at),
                        source="trigger",
                        status=_coerce_optional_string(delivery_state) or "event",
                        run_key=_coerce_optional_string(trace_id)
                        or _coerce_optional_string(event_id),
                    )

                for (
                    execution_id,
                    event_id,
                    trace_id,
                    effect_id,
                    status,
                    started_at_text,
                    finished_at_text,
                ) in connection.execute(
                    """
                    SELECT execution_id, event_id, trace_id, effect_id, status, started_at, finished_at
                    FROM effect_executions
                    WHERE COALESCE(finished_at, started_at) BETWEEN ? AND ?
                    ORDER BY COALESCE(finished_at, started_at) ASC, execution_id ASC
                    """,
                    (start_text, end_text),
                ):
                    register_activity(
                        node_id=str(effect_id or ""),
                        timestamp=latest_datetime(finished_at_text, started_at_text),
                        source="effect",
                        status=_coerce_optional_string(status),
                        run_key=_coerce_optional_string(trace_id)
                        or _coerce_optional_string(event_id)
                        or _coerce_optional_string(execution_id),
                    )

                for (
                    execution_id,
                    event_id,
                    trace_id,
                    gate_id,
                    status,
                    started_at_text,
                    finished_at_text,
                ) in connection.execute(
                    """
                    SELECT execution_id, event_id, trace_id, gate_id, status, started_at, finished_at
                    FROM gate_executions
                    WHERE COALESCE(finished_at, started_at) BETWEEN ? AND ?
                    ORDER BY COALESCE(finished_at, started_at) ASC, execution_id ASC
                    """,
                    (start_text, end_text),
                ):
                    register_activity(
                        node_id=str(gate_id or ""),
                        timestamp=latest_datetime(finished_at_text, started_at_text),
                        source="gate",
                        status=_coerce_optional_string(status),
                        run_key=_coerce_optional_string(trace_id)
                        or _coerce_optional_string(event_id)
                        or _coerce_optional_string(execution_id),
                    )
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
                        or latest_datetime(
                            raw_record.get("delivered_at"), raw_record.get("inflight_at")
                        )
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
                    timestamp=latest_datetime(
                        raw_record.get("finished_at"), raw_record.get("started_at")
                    ),
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
                    timestamp=latest_datetime(
                        raw_record.get("finished_at"), raw_record.get("started_at")
                    ),
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
        specs = (CONFORMANCE_REPORT_SPECS["trigger"], CONFORMANCE_REPORT_SPECS["effect"])
        return ConformanceReportsPayload(
            generated_at=now,
            stale_after_seconds=int(CONFORMANCE_REPORT_STALE_AFTER.total_seconds()),
            reports=[self._read_conformance_report(now=now, **spec) for spec in specs],
        )

    def _run_conformance_report(self, kind: str) -> ConformanceReportRunPayload:
        normalized_kind = kind.strip().lower()
        spec = CONFORMANCE_REPORT_SPECS.get(normalized_kind)
        if spec is None:
            allowed = ", ".join(sorted(CONFORMANCE_REPORT_SPECS))
            raise ValueError(f"Unknown conformance report {kind!r}. Expected one of: {allowed}.")

        suite_path = Path(str(spec["suite_path"]))
        suite_file = self.project_dir / suite_path
        if not suite_file.exists():
            raise RuntimeError(f"{spec['label']} conformance suite is missing: {suite_path}")

        artifact_path = (self.config.runtime_dir / str(spec["artifact_path"])).resolve()
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env[str(spec["env_var"])] = str(artifact_path)
        src_path = str((self.project_dir / "src").resolve())
        existing_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            src_path if not existing_pythonpath else f"{src_path}{os.pathsep}{existing_pythonpath}"
        )

        command = [sys.executable, "-m", "pytest", "-q", str(suite_path)]
        started_at = _utc_now()
        try:
            result = subprocess.run(
                command,
                cwd=self.project_dir,
                env=env,
                text=True,
                capture_output=True,
                timeout=CONFORMANCE_REPORT_RUN_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            output = _trim_conformance_process_output(
                _join_conformance_process_output(exc.stdout, exc.stderr)
            )
            message = (
                f"{spec['label']} conformance suite timed out after "
                f"{CONFORMANCE_REPORT_RUN_TIMEOUT_SECONDS} seconds."
            )
            if output:
                message = f"{message}\n{output}"
            raise RuntimeError(message) from exc
        except OSError as exc:
            raise RuntimeError(f"{spec['label']} conformance suite could not be started: {exc}") from exc

        completed_at = _utc_now()
        output = _trim_conformance_process_output(
            _join_conformance_process_output(result.stdout, result.stderr)
        )
        return ConformanceReportRunPayload(
            kind=normalized_kind,
            exit_code=result.returncode,
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=max(0.0, (completed_at - started_at).total_seconds()),
            output=output or None,
            reports=self._read_conformance_reports(),
        )

    def _read_conformance_report(
        self,
        *,
        kind: str,
        label: str,
        artifact_path: str,
        guidance: str,
        env_var: str,
        suite_path: str,
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

    def _workflow_run_store_bundle(self, stores: Mapping[str, Any]) -> tuple[Any, Any, Any]:
        if self._workflow_uses_sqlite_store(stores):
            return self._workflow_sqlite_store_bundle(stores)
        from agent_sandbox.daemons.effect_orchestrator import JsonExecutionStore, JsonGateExecutionStore

        return (
            TriggerEventQueue(Path(stores["event_queue_store_path"])),
            JsonExecutionStore(Path(stores["effect_execution_store_path"])),
            JsonGateExecutionStore(Path(stores["gate_execution_store_path"])),
        )

    def _workflow_run_event_records(self, stores: Mapping[str, Any]) -> dict[str, Any]:
        if self._workflow_uses_sqlite_store(stores):
            queue_store, _, _ = self._workflow_sqlite_store_bundle(stores)
            return {
                record.event_id: record.model_dump(mode="json")
                for record in queue_store.read_all().values()
            }
        return JsonTriggerEventRepository().read_events(stores["event_queue_store_path"])

    def _workflow_config_snapshot_repository(
        self, stores: Mapping[str, Any]
    ) -> JsonWorkflowConfigSnapshotRepository | SqliteWorkflowConfigSnapshotRepository:
        if self._workflow_uses_sqlite_store(stores):
            return SqliteWorkflowConfigSnapshotRepository(Path(stores["orchestration_store_path"]))
        return JsonWorkflowConfigSnapshotRepository(
            Path(stores["workflow_config_snapshot_store_path"])
        )

    def _communication_artifact_store(self) -> CommunicationArtifactStore:
        return CommunicationArtifactStore(self.config.runtime_dir)

    def _published_artifact_store(self) -> PublishedArtifactStore:
        return PublishedArtifactStore(self.config.runtime_dir)

    def _workflow_sample_lookup_matches(self, lookup: WorkflowRunLookupPayload) -> bool:
        return any(
            value == SAMPLE_WORKFLOW_RUN_ID
            for value in (lookup.run_id, lookup.event_id, lookup.trigger_event_id)
        ) or (
            lookup.trace_id == SAMPLE_WORKFLOW_TRACE_ID
        )

    def _workflow_sample_matched_by(self, lookup: WorkflowRunLookupPayload) -> list[str]:
        matched_by: list[str] = []
        for field_name, value in (
            ("run_id", lookup.run_id),
            ("event_id", lookup.event_id),
            ("trigger_event_id", lookup.trigger_event_id),
        ):
            if value == SAMPLE_WORKFLOW_RUN_ID and field_name not in matched_by:
                matched_by.append(field_name)
        if lookup.trace_id == SAMPLE_WORKFLOW_TRACE_ID:
            matched_by.append("trace_id")
        return matched_by

    def _workflow_sample_tutorial_steps(
        self, *, active: bool
    ) -> tuple[list[WorkflowSampleTutorialStepPayload], WorkflowSampleTutorialStepPayload]:
        launch_status = "completed" if active else "active"
        inspect_status = "active" if active else "pending"
        steps = [
            WorkflowSampleTutorialStepPayload(
                step_id="launch",
                title="Launch the sample",
                body="Create the guided in-memory run without touching runtime stores.",
                status=launch_status,
            ),
            WorkflowSampleTutorialStepPayload(
                step_id="inspect",
                title="Inspect the run",
                body="Open the existing inspector and review the simulated graph.",
                status=inspect_status,
                node_id=SAMPLE_WORKFLOW_RUN_ID,
            ),
            WorkflowSampleTutorialStepPayload(
                step_id="approval",
                title="Review the approval boundary",
                body="The gate stays visibly pending so the safety boundary is easy to inspect.",
                status="pending",
                node_id=SAMPLE_APPROVAL_BOUNDARY_NODE_ID,
            ),
            WorkflowSampleTutorialStepPayload(
                step_id="safe-stop",
                title="Review the safe stop",
                body="The final effect fails safely and remains resumable in-memory.",
                status="pending",
                node_id=SAMPLE_SAFE_STOP_NODE_ID,
            ),
            WorkflowSampleTutorialStepPayload(
                step_id="reset",
                title="Reset the sample",
                body="Clear the sample back to an idle in-memory state.",
                status="pending",
            ),
        ]
        current_step = steps[1] if active else steps[0]
        return steps, current_step

    def _workflow_sample_run_summary(
        self, *, launched_at: datetime
    ) -> WorkflowRunSummaryPayload:
        trigger_started_at = launched_at
        trigger_finished_at = launched_at + timedelta(seconds=1)
        agent_started_at = launched_at + timedelta(seconds=2)
        agent_finished_at = launched_at + timedelta(seconds=4)
        approval_started_at = launched_at + timedelta(seconds=5)
        safe_stop_started_at = launched_at + timedelta(seconds=7)
        safe_stop_finished_at = launched_at + timedelta(seconds=8)
        return WorkflowRunSummaryPayload(
            event_id=SAMPLE_WORKFLOW_RUN_ID,
            run_state="errored",
            trace_id=SAMPLE_WORKFLOW_TRACE_ID,
            source_node_id=SAMPLE_TRIGGER_NODE_ID,
            source_trigger_type="trigger:event",
            delivery_state="delivered",
            occurred_at=trigger_started_at,
            latest_activity_at=safe_stop_finished_at,
            active_nodes=[
                WorkflowRunNodeActivityPayload(
                    node_id=SAMPLE_TRIGGER_NODE_ID,
                    status="delivered",
                    count=1,
                    source="sample",
                    started_at=trigger_started_at,
                    finished_at=trigger_finished_at,
                ),
                WorkflowRunNodeActivityPayload(
                    node_id=SAMPLE_AGENT_PROMPT_NODE_ID,
                    status="succeeded",
                    count=1,
                    source="sample",
                    started_at=agent_started_at,
                    finished_at=agent_finished_at,
                    dispatch_id=SAMPLE_WORKFLOW_DISPATCH_ID,
                ),
                WorkflowRunNodeActivityPayload(
                    node_id=SAMPLE_APPROVAL_BOUNDARY_NODE_ID,
                    status="waiting",
                    count=1,
                    source="sample",
                    started_at=approval_started_at,
                ),
                WorkflowRunNodeActivityPayload(
                    node_id=SAMPLE_SAFE_STOP_NODE_ID,
                    status="failed",
                    count=1,
                    source="sample",
                    started_at=safe_stop_started_at,
                    finished_at=safe_stop_finished_at,
                    error="Simulated safe stop: the sample never touches runtime stores.",
                ),
            ],
            failed_node_id=SAMPLE_SAFE_STOP_NODE_ID,
            failed_node_status="failed",
            failed_at=safe_stop_finished_at,
            error="Simulated safe stop: the sample never touches runtime stores.",
            resumable=True,
        )

    def _workflow_sample_detail(
        self,
        *,
        launched_at: datetime,
        lookup: WorkflowRunLookupPayload,
    ) -> WorkflowRunDetailPayload:
        trigger_started_at = launched_at
        trigger_finished_at = launched_at + timedelta(seconds=1)
        agent_started_at = launched_at + timedelta(seconds=2)
        agent_finished_at = launched_at + timedelta(seconds=4)
        approval_started_at = launched_at + timedelta(seconds=5)
        safe_stop_started_at = launched_at + timedelta(seconds=7)
        safe_stop_finished_at = launched_at + timedelta(seconds=8)
        approval_expires_at = launched_at + timedelta(days=1)
        matched_by = self._workflow_sample_matched_by(lookup)
        if not matched_by:
            matched_by = ["event_id"]

        trigger_event = WorkflowRunTriggerEventPayload(
            event_id=SAMPLE_WORKFLOW_RUN_ID,
            trigger_id=SAMPLE_TRIGGER_NODE_ID,
            trigger_type="trigger:event",
            trace_id=SAMPLE_WORKFLOW_TRACE_ID,
            occurred_at=trigger_started_at,
            delivery_state="delivered",
            inflight_at=trigger_started_at,
            delivered_at=trigger_finished_at,
            idempotency_key="sample:first-run:idempotency",
            payload={
                "event_type": "sample:first-run",
                "correlation_key": SAMPLE_WORKFLOW_TRACE_ID,
                "message": "Launch the guided in-memory sample.",
            },
        )

        approval_link = WorkflowRunApprovalLinkPayload(
            request_id=SAMPLE_WORKFLOW_APPROVAL_REQUEST_ID,
            session_id=SAMPLE_WORKFLOW_SESSION_ID,
            action_type="effect:agent:prompt",
            tool_name="prompt_agent",
            directory_path="root",
            payload_digest="sample:first-run:digest",
            status="pending",
            human_summary="Approve the simulated prompt before a real agent would be contacted.",
            expires_at=approval_expires_at,
        )

        artifact_link = WorkflowRunCommunicationArtifactLinkPayload(
            artifact_id=SAMPLE_WORKFLOW_ARTIFACT_ID,
            session_id=SAMPLE_WORKFLOW_SESSION_ID,
            provider="sample",
            original_filename="sample-agent-prompt.txt",
            filename="sample-agent-prompt.txt",
            mime_type="text/plain",
            created_at=agent_finished_at,
            staged_at=agent_finished_at,
            status="simulated_in_memory",
            metadata={
                "source": "admin.sample_workflow",
                "note": "In-memory artifact reference only.",
            },
        )

        tool_call_link = WorkflowRunToolCallLinkPayload(
            created_at=agent_started_at,
            node_id=SAMPLE_AGENT_PROMPT_NODE_ID,
            tool_name="prompt_agent",
            request_id=SAMPLE_WORKFLOW_REQUEST_ID,
            correlation_id=SAMPLE_WORKFLOW_DISPATCH_ID,
            approval_request_id=SAMPLE_WORKFLOW_APPROVAL_REQUEST_ID,
            workflow_run_id=SAMPLE_WORKFLOW_RUN_ID,
            trace_id=SAMPLE_WORKFLOW_TRACE_ID,
            session_id=SAMPLE_WORKFLOW_SESSION_ID,
            decision="allowed",
            status="simulated_in_memory",
            href="#tool-calls?q=sample:first-run:request",
            args_summary="Prompt a simulated agent with the sample trigger payload.",
            result_summary="The in-memory agent acknowledged the prompt without touching runtime stores.",
            result_status="simulated_in_memory",
            result_empty=False,
            args={
                "prompt": "Launch the guided sample run.",
                "trigger_event_id": SAMPLE_WORKFLOW_RUN_ID,
                "session_id": SAMPLE_WORKFLOW_SESSION_ID,
            },
            result={
                "status": "simulated_in_memory",
                "dispatch_id": SAMPLE_WORKFLOW_DISPATCH_ID,
                "thread_id": SAMPLE_WORKFLOW_THREAD_ID,
            },
            redacted_args={
                "prompt": "Launch the guided sample run.",
                "trigger_event_id": SAMPLE_WORKFLOW_RUN_ID,
                "session_id": SAMPLE_WORKFLOW_SESSION_ID,
            },
            redacted_result={
                "status": "simulated_in_memory",
                "dispatch_id": SAMPLE_WORKFLOW_DISPATCH_ID,
                "thread_id": SAMPLE_WORKFLOW_THREAD_ID,
            },
        )

        agent_link = WorkflowRunAgentLinkPayload(
            node_id=SAMPLE_AGENT_PROMPT_NODE_ID,
            node_type="effect:agent:prompt",
            session_id=SAMPLE_WORKFLOW_SESSION_ID,
            agent_id="sample:first-run:agent",
            backend="simulated_in_memory",
            agent_backend="simulated_in_memory",
            agent_available=False,
            agent_status="simulated_in_memory",
            agent_missing_reason="No real agent was launched for the in-memory sample.",
            target_name="sample-first-run",
            prompt="Launch the guided sample run.",
            prompt_summary="Simulated prompt for the sample workflow.",
            prompt_status="simulated_in_memory",
            dispatch_id=SAMPLE_WORKFLOW_DISPATCH_ID,
            thread_id=SAMPLE_WORKFLOW_THREAD_ID,
            execution_id=SAMPLE_WORKFLOW_EXECUTION_ID,
            artifact_ids=[SAMPLE_WORKFLOW_ARTIFACT_ID],
            status="simulated_in_memory",
            waiting=False,
            completed=True,
            session_status="session_missing",
            session_available=False,
            session_missing_reason="No real session was created for the in-memory sample.",
            log_status="session_missing",
            log_available=False,
            log_missing_reason="No log file exists for the in-memory sample.",
            result_summary="The in-memory agent acknowledged the prompt.",
            result_status="simulated_in_memory",
            result_empty=False,
        )

        trigger_node = WorkflowRunNodeDetailPayload(
            node_id=SAMPLE_TRIGGER_NODE_ID,
            node_type="trigger:event",
            display_name="Sample trigger",
            source_kind="trigger_event",
            event_id=SAMPLE_WORKFLOW_RUN_ID,
            trace_id=SAMPLE_WORKFLOW_TRACE_ID,
            status="delivered",
            started_at=trigger_started_at,
            finished_at=trigger_finished_at,
            duration_ms=1000,
            occurred_at=trigger_started_at,
            delivery_state="delivered",
            resolved_config={
                "event_type": "sample:first-run",
                "correlation_key": SAMPLE_WORKFLOW_TRACE_ID,
            },
            binding_trace={
                "consumed_output_keys": [],
                "entries": [],
            },
            input_payload={
                "event_type": "sample:first-run",
                "correlation_key": SAMPLE_WORKFLOW_TRACE_ID,
                "message": "Launch the guided in-memory sample.",
            },
            outputs={
                "event_type": "sample:first-run",
                "correlation_key": SAMPLE_WORKFLOW_TRACE_ID,
            },
            error=None,
            config_revision=SAMPLE_WORKFLOW_REVISION,
            missing_fields=[],
            records=[
                WorkflowRunNodeRecordPayload(
                    record_kind="trigger_event",
                    event_id=SAMPLE_WORKFLOW_RUN_ID,
                    trace_id=SAMPLE_WORKFLOW_TRACE_ID,
                    node_id=SAMPLE_TRIGGER_NODE_ID,
                    node_type="trigger:event",
                    status="delivered",
                    started_at=trigger_started_at,
                    finished_at=trigger_finished_at,
                    duration_ms=1000,
                    source_trigger_type="trigger:event",
                    occurred_at=trigger_started_at,
                    input_payload={
                        "event_type": "sample:first-run",
                        "correlation_key": SAMPLE_WORKFLOW_TRACE_ID,
                        "message": "Launch the guided in-memory sample.",
                    },
                    outputs={
                        "event_type": "sample:first-run",
                        "correlation_key": SAMPLE_WORKFLOW_TRACE_ID,
                    },
                    resolved_config={
                        "event_type": "sample:first-run",
                        "correlation_key": SAMPLE_WORKFLOW_TRACE_ID,
                    },
                    binding_trace={
                        "consumed_output_keys": [],
                        "entries": [],
                    },
                    config_revision=SAMPLE_WORKFLOW_REVISION,
                )
            ],
        )

        agent_node = WorkflowRunNodeDetailPayload(
            node_id=SAMPLE_AGENT_PROMPT_NODE_ID,
            node_type="effect:agent:prompt",
            display_name="Prompt simulated agent",
            source_kind="effect_execution",
            execution_id=SAMPLE_WORKFLOW_EXECUTION_ID,
            event_id=SAMPLE_WORKFLOW_RUN_ID,
            trace_id=SAMPLE_WORKFLOW_TRACE_ID,
            status="succeeded",
            started_at=agent_started_at,
            finished_at=agent_finished_at,
            duration_ms=2000,
            occurred_at=agent_started_at,
            resolved_config={
                "session_id": SAMPLE_WORKFLOW_SESSION_ID,
                "vm_name": "sample-in-memory",
                "prompt": "Launch the guided sample run.",
                "dispatch_id": SAMPLE_WORKFLOW_DISPATCH_ID,
                "thread_id": SAMPLE_WORKFLOW_THREAD_ID,
                "execution_id": SAMPLE_WORKFLOW_EXECUTION_ID,
                "artifact_ids": [SAMPLE_WORKFLOW_ARTIFACT_ID],
                "request_id": SAMPLE_WORKFLOW_APPROVAL_REQUEST_ID,
            },
            binding_trace={
                "consumed_output_keys": ["sample-trigger.event_type"],
                "entries": [
                    {
                        "config_path": "$.prompt",
                        "expression": "${{sample-trigger.event_type}}",
                        "reference": "sample-trigger.event_type",
                        "source_node_id": SAMPLE_TRIGGER_NODE_ID,
                        "output_path": "event_type",
                        "fallback_used": False,
                        "status": "resolved",
                        "resolved_value_preview": '"sample:first-run"',
                        "value_type": "str",
                        "error": None,
                    }
                ],
            },
            input_payload={
                "event_type": "sample:first-run",
                "correlation_key": SAMPLE_WORKFLOW_TRACE_ID,
                "request_id": SAMPLE_WORKFLOW_APPROVAL_REQUEST_ID,
            },
            outputs={
                "dispatch_id": SAMPLE_WORKFLOW_DISPATCH_ID,
                "thread_id": SAMPLE_WORKFLOW_THREAD_ID,
                "execution_id": SAMPLE_WORKFLOW_EXECUTION_ID,
                "prompt_summary": "The sample prompt was handled in memory.",
            },
            config_revision=SAMPLE_WORKFLOW_REVISION,
            approval_links=[],
            agent_links=[agent_link],
            communication_artifact_links=[artifact_link],
            tool_call_links=[tool_call_link],
            missing_fields=[],
            records=[
                WorkflowRunNodeRecordPayload(
                    record_kind="effect_execution",
                    execution_id=SAMPLE_WORKFLOW_EXECUTION_ID,
                    event_id=SAMPLE_WORKFLOW_RUN_ID,
                    trace_id=SAMPLE_WORKFLOW_TRACE_ID,
                    node_id=SAMPLE_AGENT_PROMPT_NODE_ID,
                    node_type="effect:agent:prompt",
                    status="succeeded",
                    started_at=agent_started_at,
                    finished_at=agent_finished_at,
                    duration_ms=2000,
                    source_node_id=SAMPLE_TRIGGER_NODE_ID,
                    source_trigger_type="trigger:event",
                    occurred_at=agent_started_at,
                    input_payload={
                        "event_type": "sample:first-run",
                        "correlation_key": SAMPLE_WORKFLOW_TRACE_ID,
                        "request_id": SAMPLE_WORKFLOW_APPROVAL_REQUEST_ID,
                    },
                    outputs={
                        "dispatch_id": SAMPLE_WORKFLOW_DISPATCH_ID,
                        "thread_id": SAMPLE_WORKFLOW_THREAD_ID,
                        "execution_id": SAMPLE_WORKFLOW_EXECUTION_ID,
                        "prompt_summary": "The sample prompt was handled in memory.",
                    },
                    resolved_config={
                        "session_id": SAMPLE_WORKFLOW_SESSION_ID,
                        "vm_name": "sample-in-memory",
                        "prompt": "Launch the guided sample run.",
                        "dispatch_id": SAMPLE_WORKFLOW_DISPATCH_ID,
                        "thread_id": SAMPLE_WORKFLOW_THREAD_ID,
                        "execution_id": SAMPLE_WORKFLOW_EXECUTION_ID,
                        "artifact_ids": [SAMPLE_WORKFLOW_ARTIFACT_ID],
                        "request_id": SAMPLE_WORKFLOW_APPROVAL_REQUEST_ID,
                    },
                    binding_trace={
                        "consumed_output_keys": ["sample-trigger.event_type"],
                        "entries": [
                            {
                                "config_path": "$.prompt",
                                "expression": "${{sample-trigger.event_type}}",
                                "reference": "sample-trigger.event_type",
                                "source_node_id": SAMPLE_TRIGGER_NODE_ID,
                                "output_path": "event_type",
                                "fallback_used": False,
                                "status": "resolved",
                                "resolved_value_preview": '"sample:first-run"',
                                "value_type": "str",
                                "error": None,
                            }
                        ],
                    },
                    config_revision=SAMPLE_WORKFLOW_REVISION,
                )
            ],
        )

        approval_node = WorkflowRunNodeDetailPayload(
            node_id=SAMPLE_APPROVAL_BOUNDARY_NODE_ID,
            node_type="gate:await-agent",
            display_name="Await approval",
            source_kind="gate_execution",
            execution_id="sample:first-run:gate-execution",
            event_id=SAMPLE_WORKFLOW_RUN_ID,
            trace_id=SAMPLE_WORKFLOW_TRACE_ID,
            status="waiting",
            started_at=approval_started_at,
            occurred_at=approval_started_at,
            resolved_config={
                "session_id": SAMPLE_WORKFLOW_SESSION_ID,
                "dispatch_id": SAMPLE_WORKFLOW_DISPATCH_ID,
                "prompt": "Launch the guided sample run.",
            },
            binding_trace={
                "consumed_output_keys": ["sample-agent-prompt.dispatch_id"],
                "entries": [
                    {
                        "config_path": "$.dispatch_id",
                        "expression": "${{sample-agent-prompt.dispatch_id}}",
                        "reference": "sample-agent-prompt.dispatch_id",
                        "source_node_id": SAMPLE_AGENT_PROMPT_NODE_ID,
                        "output_path": "dispatch_id",
                        "fallback_used": False,
                        "status": "resolved",
                        "resolved_value_preview": '"sample:first-run:dispatch"',
                        "value_type": "str",
                        "error": None,
                    }
                ],
            },
            input_payload={
                "session_id": SAMPLE_WORKFLOW_SESSION_ID,
                "dispatch_id": SAMPLE_WORKFLOW_DISPATCH_ID,
                "approval_request_id": SAMPLE_WORKFLOW_APPROVAL_REQUEST_ID,
            },
            outputs={
                "status": "pending",
                "approval_request_id": SAMPLE_WORKFLOW_APPROVAL_REQUEST_ID,
            },
            config_revision=SAMPLE_WORKFLOW_REVISION,
            approval_links=[approval_link],
            missing_fields=[],
            records=[
                WorkflowRunNodeRecordPayload(
                    record_kind="gate_execution",
                    execution_id="sample:first-run:gate-execution",
                    event_id=SAMPLE_WORKFLOW_RUN_ID,
                    trace_id=SAMPLE_WORKFLOW_TRACE_ID,
                    node_id=SAMPLE_APPROVAL_BOUNDARY_NODE_ID,
                    node_type="gate:await-agent",
                    status="waiting",
                    started_at=approval_started_at,
                    source_node_id=SAMPLE_AGENT_PROMPT_NODE_ID,
                    source_trigger_type="effect:agent:prompt",
                    occurred_at=approval_started_at,
                    input_payload={
                        "session_id": SAMPLE_WORKFLOW_SESSION_ID,
                        "dispatch_id": SAMPLE_WORKFLOW_DISPATCH_ID,
                        "approval_request_id": SAMPLE_WORKFLOW_APPROVAL_REQUEST_ID,
                    },
                    outputs={
                        "status": "pending",
                        "approval_request_id": SAMPLE_WORKFLOW_APPROVAL_REQUEST_ID,
                    },
                    resolved_config={
                        "session_id": SAMPLE_WORKFLOW_SESSION_ID,
                        "dispatch_id": SAMPLE_WORKFLOW_DISPATCH_ID,
                        "prompt": "Launch the guided sample run.",
                    },
                    binding_trace={
                        "consumed_output_keys": ["sample-agent-prompt.dispatch_id"],
                        "entries": [
                            {
                                "config_path": "$.dispatch_id",
                                "expression": "${{sample-agent-prompt.dispatch_id}}",
                                "reference": "sample-agent-prompt.dispatch_id",
                                "source_node_id": SAMPLE_AGENT_PROMPT_NODE_ID,
                                "output_path": "dispatch_id",
                                "fallback_used": False,
                                "status": "resolved",
                                "resolved_value_preview": '"sample:first-run:dispatch"',
                                "value_type": "str",
                                "error": None,
                            }
                        ],
                    },
                    config_revision=SAMPLE_WORKFLOW_REVISION,
                )
            ],
        )

        safe_stop_node = WorkflowRunNodeDetailPayload(
            node_id=SAMPLE_SAFE_STOP_NODE_ID,
            node_type="effect:notification:send",
            display_name="Safe stop notification",
            source_kind="effect_execution",
            execution_id="sample:first-run:safe-stop-execution",
            event_id=SAMPLE_WORKFLOW_RUN_ID,
            trace_id=SAMPLE_WORKFLOW_TRACE_ID,
            status="failed",
            started_at=safe_stop_started_at,
            finished_at=safe_stop_finished_at,
            duration_ms=1000,
            occurred_at=safe_stop_started_at,
            error="Simulated safe stop: the sample never touches runtime stores.",
            binding_trace={
                "consumed_output_keys": ["sample-approval-boundary.status"],
                "entries": [
                    {
                        "config_path": "$.status",
                        "expression": "${{sample-approval-boundary.status}}",
                        "reference": "sample-approval-boundary.status",
                        "source_node_id": SAMPLE_APPROVAL_BOUNDARY_NODE_ID,
                        "output_path": "status",
                        "fallback_used": False,
                        "status": "resolved",
                        "resolved_value_preview": '"pending"',
                        "value_type": "str",
                        "error": None,
                    }
                ],
            },
            input_payload={
                "reason": "pending approval boundary",
                "source_node_id": SAMPLE_APPROVAL_BOUNDARY_NODE_ID,
                "source_trigger_type": "gate:await-agent",
                "trace_id": SAMPLE_WORKFLOW_TRACE_ID,
            },
            outputs={
                "notification_status": "suppressed",
                "safe_stop": True,
                "result_summary": "The sample stopped safely in memory.",
            },
            resolved_config={
                "channel": "operator-console",
                "message": "Safe stop reached for the guided sample.",
                "trace_id": SAMPLE_WORKFLOW_TRACE_ID,
            },
            config_revision=SAMPLE_WORKFLOW_REVISION,
            retry_resume_eligible=True,
            retry_resume_reason="Failed node has a resumable input payload.",
            missing_fields=[],
            records=[
                WorkflowRunNodeRecordPayload(
                    record_kind="effect_execution",
                    execution_id="sample:first-run:safe-stop-execution",
                    event_id=SAMPLE_WORKFLOW_RUN_ID,
                    trace_id=SAMPLE_WORKFLOW_TRACE_ID,
                    node_id=SAMPLE_SAFE_STOP_NODE_ID,
                    node_type="effect:notification:send",
                    status="failed",
                    started_at=safe_stop_started_at,
                    finished_at=safe_stop_finished_at,
                    duration_ms=1000,
                    source_node_id=SAMPLE_APPROVAL_BOUNDARY_NODE_ID,
                    source_trigger_type="gate:await-agent",
                    occurred_at=safe_stop_started_at,
                    input_payload={
                        "reason": "pending approval boundary",
                        "source_node_id": SAMPLE_APPROVAL_BOUNDARY_NODE_ID,
                        "source_trigger_type": "gate:await-agent",
                        "trace_id": SAMPLE_WORKFLOW_TRACE_ID,
                    },
                    outputs={
                        "notification_status": "suppressed",
                        "safe_stop": True,
                        "result_summary": "The sample stopped safely in memory.",
                    },
                    resolved_config={
                        "channel": "operator-console",
                        "message": "Safe stop reached for the guided sample.",
                        "trace_id": SAMPLE_WORKFLOW_TRACE_ID,
                    },
                    binding_trace={
                        "consumed_output_keys": ["sample-approval-boundary.status"],
                        "entries": [
                            {
                                "config_path": "$.status",
                                "expression": "${{sample-approval-boundary.status}}",
                                "reference": "sample-approval-boundary.status",
                                "source_node_id": SAMPLE_APPROVAL_BOUNDARY_NODE_ID,
                                "output_path": "status",
                                "fallback_used": False,
                                "status": "resolved",
                                "resolved_value_preview": '"pending"',
                                "value_type": "str",
                                "error": None,
                            }
                        ],
                    },
                    error="Simulated safe stop: the sample never touches runtime stores.",
                    published_event_id="sample:first-run:safe-stop-notification",
                    completion_event_enqueued_at=safe_stop_finished_at,
                    config_revision=SAMPLE_WORKFLOW_REVISION,
                )
            ],
        )

        graph_snapshot = WorkflowRunGraphSnapshotPayload(
            revision=SAMPLE_WORKFLOW_REVISION,
            captured_at=launched_at,
            snapshot_type="simulated_in_memory_sample",
            source="admin.sample_workflow",
            node_count=4,
            edge_count=3,
            triggers=[
                {
                    "id": SAMPLE_TRIGGER_NODE_ID,
                    "type": "trigger:event",
                    "display_name": "Sample trigger",
                }
            ],
            gates=[
                {
                    "id": SAMPLE_APPROVAL_BOUNDARY_NODE_ID,
                    "type": "gate:await-agent",
                    "display_name": "Await approval",
                }
            ],
            effects=[
                {
                    "id": SAMPLE_AGENT_PROMPT_NODE_ID,
                    "type": "effect:agent:prompt",
                    "display_name": "Prompt simulated agent",
                },
                {
                    "id": SAMPLE_SAFE_STOP_NODE_ID,
                    "type": "effect:notification:send",
                    "display_name": "Safe stop notification",
                },
            ],
            queries=[],
            edges=[
                {
                    "id": "sample-edge-trigger-agent",
                    "source": SAMPLE_TRIGGER_NODE_ID,
                    "target": SAMPLE_AGENT_PROMPT_NODE_ID,
                },
                {
                    "id": "sample-edge-agent-approval",
                    "source": SAMPLE_AGENT_PROMPT_NODE_ID,
                    "target": SAMPLE_APPROVAL_BOUNDARY_NODE_ID,
                },
                {
                    "id": "sample-edge-approval-stop",
                    "source": SAMPLE_APPROVAL_BOUNDARY_NODE_ID,
                    "target": SAMPLE_SAFE_STOP_NODE_ID,
                },
            ],
            workflow_graphs=[
                {
                    "id": SAMPLE_WORKFLOW_GRAPH_ID,
                    "name": "Sample first run",
                    "description": "Deterministic in-memory guided sample workflow.",
                    "directory_paths": ["root"],
                    "user_ids": ["operator"],
                    "node_ids": [
                        SAMPLE_TRIGGER_NODE_ID,
                        SAMPLE_AGENT_PROMPT_NODE_ID,
                        SAMPLE_APPROVAL_BOUNDARY_NODE_ID,
                        SAMPLE_SAFE_STOP_NODE_ID,
                    ],
                }
            ],
        )

        summary = self._workflow_sample_run_summary(launched_at=launched_at)
        candidate_match = WorkflowRunCandidateMatchPayload(
            event_id=SAMPLE_WORKFLOW_RUN_ID,
            trace_id=SAMPLE_WORKFLOW_TRACE_ID,
            run_state=summary.run_state,
            latest_activity_at=summary.latest_activity_at,
            source_node_id=summary.source_node_id,
            source_trigger_type=summary.source_trigger_type,
            delivery_state=summary.delivery_state,
            matched_by=matched_by,
        )
        recovery = WorkflowRunRecoveryPayload(
            clear=WorkflowRunRecoveryActionPayload(
                eligible=True,
                reason="The guided sample can be cleared from in-memory state.",
                event_id=SAMPLE_WORKFLOW_RUN_ID,
                source_event_id=SAMPLE_WORKFLOW_RUN_ID,
            ),
            resume=WorkflowRunRecoveryActionPayload(
                eligible=True,
                reason="The failed safe-stop node has a resumable in-memory payload.",
                event_id=SAMPLE_WORKFLOW_RUN_ID,
                target_node_id=SAMPLE_SAFE_STOP_NODE_ID,
                source_node_id=SAMPLE_APPROVAL_BOUNDARY_NODE_ID,
                source_trigger_type="gate:await-agent",
                source_event_id=SAMPLE_WORKFLOW_RUN_ID,
            ),
        )

        return WorkflowRunDetailPayload(
            generated_at=_utc_now(),
            lookup=lookup,
            candidate_matches=[candidate_match],
            lookup_candidates=[candidate_match],
            matched_by=matched_by,
            event_id=SAMPLE_WORKFLOW_RUN_ID,
            trace_id=SAMPLE_WORKFLOW_TRACE_ID,
            source_node_id=SAMPLE_TRIGGER_NODE_ID,
            source_trigger_type="trigger:event",
            delivery_state="delivered",
            occurred_at=launched_at,
            latest_activity_at=summary.latest_activity_at,
            run_state=summary.run_state,
            active_nodes=summary.active_nodes,
            failed_node_id=summary.failed_node_id,
            failed_node_status=summary.failed_node_status,
            failed_at=summary.failed_at,
            error=summary.error,
            resumable=summary.resumable,
            recovery=recovery,
            trigger_event=trigger_event,
            graph=WorkflowRunGraphPayload(
                config_revision=SAMPLE_WORKFLOW_REVISION,
                config={
                    "sample": {
                        "enabled": True,
                        "mode": "guided_first_run",
                    }
                },
                snapshot=graph_snapshot,
            ),
            nodes=[trigger_node, agent_node, approval_node, safe_stop_node],
            tool_call_links=[tool_call_link],
        )

    def _workflow_sample_state_payload(
        self,
        *,
        active: bool,
        launched_at: datetime | None,
        lookup: WorkflowRunLookupPayload | None = None,
    ) -> WorkflowSampleStatePayload:
        tutorial_steps, current_step = self._workflow_sample_tutorial_steps(active=active)
        if not active or launched_at is None:
            return WorkflowSampleStatePayload(
                active=False,
                launched_at=None,
                run=None,
                detail=None,
                tutorial_steps=tutorial_steps,
                current_step=current_step,
            )
        sample_lookup = lookup or WorkflowRunLookupPayload(
            run_id=SAMPLE_WORKFLOW_RUN_ID,
            event_id=SAMPLE_WORKFLOW_RUN_ID,
            trace_id=SAMPLE_WORKFLOW_TRACE_ID,
        )
        detail = self._workflow_sample_detail(
            launched_at=launched_at,
            lookup=sample_lookup,
        )
        return WorkflowSampleStatePayload(
            active=True,
            launched_at=launched_at,
            run=self._workflow_sample_run_summary(launched_at=launched_at),
            detail=detail,
            tutorial_steps=tutorial_steps,
            current_step=current_step,
        )

    def _workflow_sample_detail_if_active(
        self, *, lookup: WorkflowRunLookupPayload
    ) -> WorkflowRunDetailPayload | None:
        with self._workflow_sample_lock:
            if not self._workflow_sample_active or self._workflow_sample_launched_at is None:
                return None
            launched_at = self._workflow_sample_launched_at
        if not self._workflow_sample_lookup_matches(lookup):
            return None
        return self._workflow_sample_detail(launched_at=launched_at, lookup=lookup)

    def _read_workflow_run_detail(
        self,
        *,
        run_id: str | None = None,
        event_id: str | None = None,
        trigger_event_id: str | None = None,
        trace_id: str | None = None,
        session_id: str | None = None,
        artifact_id: str | None = None,
    ) -> WorkflowRunDetailPayload:
        normalized_run_id = _coerce_optional_string(run_id)
        normalized_event_id = _coerce_optional_string(event_id)
        normalized_trigger_event_id = _coerce_optional_string(trigger_event_id)
        normalized_trace_id = _coerce_optional_string(trace_id)
        normalized_session_id = _coerce_optional_string(session_id)
        normalized_artifact_id = _coerce_optional_string(artifact_id)
        if normalized_artifact_id is not None:
            normalized_artifact_id = normalized_artifact_id.strip()
            if normalized_artifact_id:
                from agent_sandbox.communication_artifacts import validate_artifact_id

                normalized_artifact_id = validate_artifact_id(normalized_artifact_id)
        lookup = WorkflowRunLookupPayload(
            run_id=normalized_run_id,
            event_id=normalized_event_id,
            trigger_event_id=normalized_trigger_event_id,
            trace_id=normalized_trace_id,
            session_id=normalized_session_id,
            artifact_id=normalized_artifact_id,
        )
        if not any(
            value
            for value in (
                lookup.run_id,
                lookup.event_id,
                lookup.trigger_event_id,
                lookup.trace_id,
                lookup.session_id,
                lookup.artifact_id,
            )
        ):
            raise ValueError(
                "Workflow run detail lookup requires run_id, event_id, trigger_event_id, trace_id, session_id, or artifact_id."
            )

        sample_detail = self._workflow_sample_detail_if_active(lookup=lookup)
        if sample_detail is not None:
            return sample_detail

        stores = self._workflow_run_store_paths()
        _, effect_store, gate_store = self._workflow_run_store_bundle(stores)
        queue_records = self._workflow_run_event_records(stores)
        effect_records = effect_store.read_all()
        gate_records = gate_store.read_all()
        await_agent_payload = _read_merged_json_objects(
            stores["legacy_await_agent_store_path"],
            stores["await_agent_store_path"],
        )
        approval_requests = self.runtime.approvals.read_all()
        artifact_store = self._communication_artifact_store()

        def normalize_record_payload(record: object) -> dict[str, Any]:
            if isinstance(record, BaseModel):
                return record.model_dump(mode="json")
            if isinstance(record, dict):
                return dict(record)
            return {}

        def latest_of(*values: object) -> datetime | None:
            parsed = [
                parsed_value
                for parsed_value in (_coerce_optional_datetime(value) for value in values)
                if parsed_value is not None
            ]
            return max(parsed) if parsed else None

        def duration_ms(started_at: datetime | None, finished_at: datetime | None) -> int | None:
            if started_at is None or finished_at is None:
                return None
            delta = finished_at - started_at
            return max(0, int(delta.total_seconds() * 1000))

        def collect_keyed_values(payload: Any, *, key_suffix: str | None = None) -> list[str]:
            values: list[str] = []

            def walk(value: Any, key: str | None = None) -> None:
                if isinstance(value, dict):
                    for child_key, child_value in value.items():
                        walk(child_value, str(child_key))
                    return
                if isinstance(value, list):
                    for child_value in value:
                        walk(child_value, key)
                    return
                if isinstance(value, str):
                    normalized = value.strip()
                    if not normalized:
                        return
                    if key_suffix is not None:
                        if key is None or not key.lower().endswith(key_suffix):
                            return
                    values.append(normalized)

            walk(payload)
            seen: set[str] = set()
            deduped: list[str] = []
            for value in values:
                if value in seen:
                    continue
                seen.add(value)
                deduped.append(value)
            return deduped

        def collect_artifact_ids(payload: Any) -> list[str]:
            values: list[str] = []

            def walk(value: Any) -> None:
                if isinstance(value, dict):
                    for child_value in value.values():
                        walk(child_value)
                    return
                if isinstance(value, list):
                    for child_value in value:
                        walk(child_value)
                    return
                if isinstance(value, str):
                    normalized = value.strip()
                    if not normalized:
                        return
                    try:
                        from agent_sandbox.communication_artifacts import validate_artifact_id

                        values.append(validate_artifact_id(normalized))
                    except ValueError:
                        pass

            walk(payload)
            seen: set[str] = set()
            deduped: list[str] = []
            for value in values:
                if value in seen:
                    continue
                seen.add(value)
                deduped.append(value)
            return deduped

        def ensure_group(groups: dict[str, dict[str, Any]], event_key: str) -> dict[str, Any]:
            group = groups.get(event_key)
            if group is None:
                group = {
                    "event_id": event_key,
                    "trigger": None,
                    "effect_records": [],
                    "gate_records": [],
                    "await_agent_records": [],
                    "trace_ids": set(),
                    "session_ids": set(),
                    "artifact_ids": set(),
                    "request_ids": set(),
                    "latest_activity_at": None,
                    "occurred_at": None,
                    "config_revisions": [],
                }
                groups[event_key] = group
            return group

        def update_group_metadata(
            group: dict[str, Any],
            *,
            trace_id_value: str | None = None,
            session_ids: list[str] | None = None,
            artifact_ids: list[str] | None = None,
            request_ids: list[str] | None = None,
            occurred_at_value: datetime | None = None,
            latest_activity_at_value: datetime | None = None,
            config_revision_value: str | None = None,
        ) -> None:
            if trace_id_value:
                group["trace_ids"].add(trace_id_value)
            for value in session_ids or []:
                group["session_ids"].add(value)
            for value in artifact_ids or []:
                group["artifact_ids"].add(value)
            for value in request_ids or []:
                group["request_ids"].add(value)
            if occurred_at_value is not None and group.get("occurred_at") is None:
                group["occurred_at"] = occurred_at_value
            previous_latest = group.get("latest_activity_at")
            if latest_activity_at_value is not None and (
                not isinstance(previous_latest, datetime) or latest_activity_at_value > previous_latest
            ):
                group["latest_activity_at"] = latest_activity_at_value
            if config_revision_value:
                revisions = group["config_revisions"]
                if config_revision_value not in revisions:
                    revisions.append(config_revision_value)

        def register_trigger(
            groups: dict[str, dict[str, Any]], raw_record: dict[str, Any], *, event_key: str
        ) -> None:
            group = ensure_group(groups, event_key)
            group["trigger"] = raw_record
            payload = raw_record.get("payload")
            update_group_metadata(
                group,
                trace_id_value=_coerce_optional_string(raw_record.get("trace_id")),
                session_ids=collect_keyed_values(raw_record, key_suffix="session_id"),
                artifact_ids=collect_artifact_ids(payload),
                request_ids=collect_keyed_values(raw_record, key_suffix="request_id"),
                occurred_at_value=_coerce_optional_datetime(raw_record.get("occurred_at")),
                latest_activity_at_value=latest_of(
                    raw_record.get("delivered_at"),
                    raw_record.get("inflight_at"),
                    raw_record.get("occurred_at"),
                ),
            )

        def register_execution(
            groups: dict[str, dict[str, Any]],
            raw_record: dict[str, Any],
            *,
            event_key: str,
            kind: str,
        ) -> None:
            group = ensure_group(groups, event_key)
            group[f"{kind}_records"].append(raw_record)
            payload_sources = (
                raw_record.get("resolved_config"),
                raw_record.get("input_payload"),
                raw_record.get("outputs"),
            )
            update_group_metadata(
                group,
                trace_id_value=_coerce_optional_string(raw_record.get("trace_id")),
                session_ids=[
                    value
                    for payload_source in payload_sources
                    for value in collect_keyed_values(payload_source, key_suffix="session_id")
                ],
                artifact_ids=[
                    value
                    for payload_source in payload_sources
                    for value in collect_artifact_ids(payload_source)
                ],
                request_ids=[
                    value
                    for payload_source in payload_sources
                    for value in collect_keyed_values(payload_source, key_suffix="request_id")
                ],
                latest_activity_at_value=latest_of(
                    raw_record.get("finished_at"),
                    raw_record.get("started_at"),
                ),
                config_revision_value=_coerce_optional_string(raw_record.get("config_revision")),
            )

        groups: dict[str, dict[str, Any]] = {}
        for event_key, record in queue_records.items():
            raw_record = normalize_record_payload(record)
            if not raw_record:
                continue
            register_trigger(groups, raw_record, event_key=str(raw_record.get("event_id") or event_key))
        for execution_id, record in effect_records.items():
            raw_record = normalize_record_payload(record)
            if not raw_record:
                continue
            register_execution(
                groups,
                raw_record,
                event_key=str(raw_record.get("event_id") or execution_id),
                kind="effect",
            )
        for execution_id, record in gate_records.items():
            raw_record = normalize_record_payload(record)
            if not raw_record:
                continue
            register_execution(
                groups,
                raw_record,
                event_key=str(raw_record.get("event_id") or execution_id),
                kind="gate",
            )
        for record_key, raw_record in await_agent_payload.items():
            if not isinstance(raw_record, dict):
                continue
            event_key = str(raw_record.get("prompt_event_id") or record_key)
            group = ensure_group(groups, event_key)
            group["await_agent_records"].append(dict(raw_record))
            update_group_metadata(
                group,
                trace_id_value=_coerce_optional_string(raw_record.get("trace_id")),
                session_ids=collect_keyed_values(raw_record, key_suffix="session_id"),
                artifact_ids=collect_artifact_ids(raw_record),
                request_ids=collect_keyed_values(raw_record, key_suffix="request_id"),
                occurred_at_value=_coerce_optional_datetime(raw_record.get("occurred_at")),
                latest_activity_at_value=_coerce_optional_datetime(raw_record.get("occurred_at")),
            )

        if not groups:
            raise ValueError("Workflow run detail lookup did not match any stored run.")

        artifact_lookup_payload: dict[str, Any] | None = None
        lookup_session_candidates = {lookup.session_id} if lookup.session_id else set()
        if lookup.artifact_id is not None:
            try:
                artifact_lookup_payload = artifact_store.payload(lookup.artifact_id)
            except FileNotFoundError:
                artifact_lookup_payload = None
            else:
                artifact_session_id = _coerce_optional_string(
                    artifact_lookup_payload.get("session_id")
                )
                if artifact_session_id:
                    lookup_session_candidates.add(artifact_session_id)

        approval_requests_by_id = {
            request_id: request for request_id, request in approval_requests.items()
        }
        approval_requests_by_session: dict[str, list[ApprovalRequest]] = {}
        for request in approval_requests.values():
            approval_requests_by_session.setdefault(request.session_id, []).append(request)

        def score_group(group: dict[str, Any]) -> tuple[int, list[str]]:
            matched_by: list[str] = []
            score = 0
            event_keys = {
                value
                for value in (lookup.run_id, lookup.event_id, lookup.trigger_event_id)
                if value
            }
            if group["event_id"] in event_keys:
                score += 1000
                matched_by.append("event_id")
            group_trace_ids = set(str(value) for value in group["trace_ids"])
            if lookup.trace_id and lookup.trace_id in group_trace_ids:
                score += 900
                matched_by.append("trace_id")
            if lookup.artifact_id and lookup.artifact_id in group["artifact_ids"]:
                score += 850
                matched_by.append("artifact_id")
            for session_candidate in lookup_session_candidates:
                if session_candidate and session_candidate in group["session_ids"]:
                    score += 800
                    matched_by.append("session_id")
                    break
            for request_id in group["request_ids"]:
                request = approval_requests_by_id.get(request_id)
                if request is None:
                    continue
                if lookup.session_id and request.session_id == lookup.session_id:
                    score += 750
                    matched_by.append("approval_request_session")
                    break
            if lookup.session_id:
                for request in approval_requests_by_session.get(lookup.session_id, []):
                    if request.request_id in group["request_ids"]:
                        score += 700
                        matched_by.append("approval_request_id")
                        break
            if artifact_lookup_payload is not None:
                artifact_session_id = _coerce_optional_string(
                    artifact_lookup_payload.get("session_id")
                )
                if artifact_session_id and artifact_session_id in group["session_ids"]:
                    score += 650
                    matched_by.append("artifact_session_id")
            return score, matched_by

        candidate_matches: list[WorkflowRunCandidateMatchPayload] = []
        selected_group: dict[str, Any] | None = None
        selected_score = -1
        selected_matched_by: list[str] = []
        for group in sorted(
            groups.values(),
            key=lambda item: (
                item.get("latest_activity_at") or datetime.min.replace(tzinfo=UTC),
                str(item.get("event_id") or ""),
            ),
            reverse=True,
        ):
            score, matched_by = score_group(group)
            if score > 0:
                candidate_matches.append(
                    WorkflowRunCandidateMatchPayload(
                        event_id=str(group["event_id"]),
                        trace_id=next(iter(sorted(group["trace_ids"])), None),
                        run_state=None,
                        latest_activity_at=group.get("latest_activity_at"),
                        source_node_id=_coerce_optional_string(
                            (group["trigger"] or {}).get("trigger_id")
                            if isinstance(group.get("trigger"), dict)
                            else None
                        ),
                        source_trigger_type=_coerce_optional_string(
                            (group["trigger"] or {}).get("trigger_type")
                            if isinstance(group.get("trigger"), dict)
                            else None
                        ),
                        delivery_state=_coerce_optional_string(
                            (group["trigger"] or {}).get("delivery_state")
                            if isinstance(group.get("trigger"), dict)
                            else None
                        ),
                        matched_by=matched_by,
                    )
                )
            if score > 0 and score > selected_score:
                selected_group = group
                selected_score = score
                selected_matched_by = matched_by

        if selected_group is None:
            raise ValueError("Workflow run detail lookup did not match any stored run.")

        selected_event_id = str(selected_group["event_id"])
        trigger_record = (
            selected_group["trigger"]
            if isinstance(selected_group.get("trigger"), dict)
            else None
        )
        effect_records_for_group = [
            record for record in selected_group["effect_records"] if isinstance(record, dict)
        ]
        gate_records_for_group = [
            record for record in selected_group["gate_records"] if isinstance(record, dict)
        ]
        await_agent_records_for_group = [
            record for record in selected_group["await_agent_records"] if isinstance(record, dict)
        ]

        def _record_latest_activity(record: dict[str, Any]) -> datetime | None:
            return latest_of(
                record.get("finished_at"),
                record.get("started_at"),
                record.get("delivered_at"),
                record.get("inflight_at"),
                record.get("occurred_at"),
            )

        selected_revisions = [
            revision
            for revision in selected_group["config_revisions"]
            if isinstance(revision, str) and revision
        ]
        document = self._read_workflow_editor_document()
        document_config = document.config
        document_revision = document.revision or self._workflow_editor_revision(document_config)
        snapshot_repository = self._workflow_config_snapshot_repository(stores)
        config_revision = selected_revisions[0] if selected_revisions else document_revision
        graph_snapshot_payload = snapshot_repository.get_snapshot(config_revision)
        graph_snapshot = (
            WorkflowRunGraphSnapshotPayload.model_validate(graph_snapshot_payload)
            if graph_snapshot_payload is not None
            else None
        )
        graph_data_availability = WorkflowRunDataAvailabilityPayload(
            graph_snapshot="available" if graph_snapshot is not None else "missing_legacy_or_unrecorded",
            binding_trace="missing_legacy_or_unrecorded",
            approvals="missing_legacy_or_unrecorded",
            agent_links="missing_legacy_or_unrecorded",
            communication_artifacts="missing_legacy_or_unrecorded",
            tool_calls="missing_legacy_or_unrecorded",
            trigger_event="available" if trigger_record is not None else "missing_legacy_or_unrecorded",
        )

        selected_trace_id = next(
            (trace_id for trace_id in sorted(selected_group["trace_ids"]) if trace_id),
            None,
        )
        selected_session_ids = {
            str(value)
            for value in selected_group["session_ids"]
            if isinstance(value, str) and value.strip()
        }

        node_metadata_by_id: dict[str, dict[str, Any]] = {}

        def register_node_metadata(config: dict[str, Any]) -> None:
            for collection_name in ("triggers", "gates", "effects", "queries"):
                entries = config.get(collection_name)
                if not isinstance(entries, list):
                    continue
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    node_id = _coerce_optional_string(entry.get("id"))
                    if not node_id:
                        continue
                    node_metadata_by_id[node_id] = {
                        "node_type": _coerce_optional_string(entry.get("type")),
                        "display_name": _coerce_optional_string(entry.get("display_name")),
                    }

        register_node_metadata(document_config)
        if graph_snapshot is not None:
            register_node_metadata(
                {
                    "triggers": graph_snapshot.triggers,
                    "gates": graph_snapshot.gates,
                    "effects": graph_snapshot.effects,
                    "queries": graph_snapshot.queries,
                    "edges": graph_snapshot.edges,
                }
            )

        agent_records_by_session_id: dict[str, list[AgentRecord]] = {}
        agent_records_by_target_name: dict[str, list[AgentRecord]] = {}
        try:
            agent_records = self.list_agents(
                include_inactive=True,
                reconcile_containers=False,
                backend="all",
                all_directories=True,
            )
        except Exception:
            agent_records = []
        for agent_record in agent_records:
            session_key = _coerce_optional_string(agent_record.session_id)
            if session_key:
                agent_records_by_session_id.setdefault(session_key, []).append(agent_record)
            target_key = _coerce_optional_string(agent_record.target_name)
            if target_key:
                agent_records_by_target_name.setdefault(target_key, []).append(agent_record)

        tool_call_audit_entries: list[tuple[ToolCallAuditEntryPayload, dict[str, Any]]] = []
        audit_log_path = self.config.audit_log_path

        def tool_call_entry_matches_selected_run(entry: ToolCallAuditEntryPayload) -> bool:
            entry_run_id = _coerce_optional_string(entry.workflow_run_id)
            if entry_run_id is not None and entry_run_id in {
                selected_event_id,
                selected_trace_id,
            }:
                return True
            entry_trace_id = _coerce_optional_string(entry.trace_id)
            if entry_trace_id is not None:
                return selected_trace_id is not None and entry_trace_id == selected_trace_id
            if entry_run_id is not None:
                return False
            entry_session_id = _coerce_optional_string(entry.session_id)
            return entry_session_id is not None and entry_session_id in selected_session_ids

        if audit_log_path.exists():
            try:
                with audit_log_path.open(encoding="utf-8") as handle:
                    for raw_line in handle:
                        line = raw_line.strip()
                        if not line:
                            continue
                        try:
                            payload = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(payload, dict):
                            continue
                        if str(payload.get("event_type") or "").strip().casefold() != "tool_call":
                            continue
                        entry = _tool_call_audit_entry_from_payload(payload)
                        if entry is None:
                            continue
                        if not tool_call_entry_matches_selected_run(entry):
                            continue
                        tool_call_audit_entries.append((entry, payload))
            except Exception:
                tool_call_audit_entries = []

        def build_node_record_payload(
            *,
            record_kind: str,
            record: dict[str, Any],
            node_id: str | None,
            node_type: str | None,
        ) -> WorkflowRunNodeRecordPayload:
            started_at = _coerce_optional_datetime(record.get("started_at"))
            finished_at = _coerce_optional_datetime(record.get("finished_at"))
            occurred_at = _coerce_optional_datetime(record.get("occurred_at"))
            return WorkflowRunNodeRecordPayload(
                record_kind=record_kind,
                execution_id=_coerce_optional_string(record.get("execution_id")),
                event_id=_coerce_optional_string(record.get("event_id")),
                trace_id=_coerce_optional_string(record.get("trace_id")),
                node_id=node_id,
                node_type=node_type,
                status=_coerce_optional_string(record.get("status"))
                or _coerce_optional_string(record.get("delivery_state")),
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=duration_ms(started_at, finished_at),
                source_node_id=_coerce_optional_string(record.get("source_node_id")),
                source_trigger_type=_coerce_optional_string(record.get("source_trigger_type")),
                delivery_state=_coerce_optional_string(record.get("delivery_state")),
                occurred_at=occurred_at,
                input_payload=normalize_record_payload(record.get("input_payload")),
                outputs=normalize_record_payload(record.get("outputs")),
                resolved_config=normalize_record_payload(record.get("resolved_config")),
                binding_trace=normalize_record_payload(record.get("binding_trace"))
                or None,
                binding_error=_coerce_optional_string(record.get("binding_error")),
                error=_coerce_optional_string(record.get("error")),
                published_event_id=_coerce_optional_string(record.get("published_event_id")),
                completion_event_enqueued_at=_coerce_optional_datetime(
                    record.get("completion_event_enqueued_at")
                ),
                config_revision=_coerce_optional_string(record.get("config_revision")),
            )

        def build_node_activity_payload(
            *,
            source_kind: str,
            record: dict[str, Any],
            node_id: str | None,
        ) -> WorkflowRunNodeActivityPayload:
            started_at = _coerce_optional_datetime(record.get("started_at"))
            finished_at = _coerce_optional_datetime(record.get("finished_at"))
            status = _coerce_optional_string(record.get("status"))
            if status is None:
                status = _coerce_optional_string(record.get("delivery_state"))
            dispatch_id = _coerce_optional_string(
                normalize_record_payload(record.get("outputs")).get("dispatch_id")
                if isinstance(record.get("outputs"), (dict, BaseModel))
                else None
            )
            if dispatch_id is None:
                dispatch_id = _coerce_optional_string(
                    normalize_record_payload(record.get("input_payload")).get("dispatch_id")
                )
            return WorkflowRunNodeActivityPayload(
                node_id=node_id or "",
                status=status or "unknown",
                count=1,
                source=source_kind,
                started_at=started_at,
                finished_at=finished_at,
                dispatch_id=dispatch_id,
                error=_coerce_optional_string(record.get("error")),
            )

        node_groups: dict[str, list[tuple[str, dict[str, Any], str | None, str | None]]] = {}

        def add_node_record(
            *,
            source_kind: str,
            record: dict[str, Any],
            node_id: str | None,
            node_type: str | None,
        ) -> None:
            if not node_id:
                return
            node_groups.setdefault(node_id, []).append((source_kind, record, node_id, node_type))

        if trigger_record is not None:
            add_node_record(
                source_kind="trigger_event",
                record=trigger_record,
                node_id=_coerce_optional_string(trigger_record.get("trigger_id")),
                node_type=_coerce_optional_string(trigger_record.get("trigger_type")),
            )
        for record in effect_records_for_group:
            add_node_record(
                source_kind="effect_execution",
                record=record,
                node_id=_coerce_optional_string(record.get("effect_id")),
                node_type=_coerce_optional_string(record.get("effect_type")),
            )
        for record in gate_records_for_group:
            add_node_record(
                source_kind="gate_execution",
                record=record,
                node_id=_coerce_optional_string(record.get("gate_id")),
                node_type=_coerce_optional_string(record.get("gate_type")),
            )
        for record in await_agent_records_for_group:
            add_node_record(
                source_kind="await_agent",
                record=record,
                node_id=_coerce_optional_string(record.get("gate_id")),
                node_type="gate:await-agent",
            )

        approval_links_by_node: dict[str, list[WorkflowRunApprovalLinkPayload]] = {}
        agent_links_by_node: dict[str, list[WorkflowRunAgentLinkPayload]] = {}
        artifact_links_by_node: dict[str, list[WorkflowRunCommunicationArtifactLinkPayload]] = {}
        tool_call_links_by_node: dict[str, list[WorkflowRunToolCallLinkPayload]] = {}
        run_tool_call_links: list[WorkflowRunToolCallLinkPayload] = []
        node_request_ids_by_node: dict[str, set[str]] = {}
        node_session_ids_by_node: dict[str, set[str]] = {}

        def build_approval_link(request: ApprovalRequest) -> WorkflowRunApprovalLinkPayload:
            return WorkflowRunApprovalLinkPayload(
                request_id=request.request_id,
                session_id=request.session_id,
                action_type=request.action_type,
                tool_name=request.tool_name,
                directory_path=request.directory_path,
                payload_digest=request.payload_digest,
                status=request.status.value,
                approver=request.approver,
                decision_at=request.decision_at,
                decision_reason=request.decision_reason,
                expires_at=request.expires_at,
                human_summary=request.human_summary,
            )

        def build_artifact_link(
            artifact_payload: dict[str, Any], *, status: str | None = None
        ) -> WorkflowRunCommunicationArtifactLinkPayload:
            return WorkflowRunCommunicationArtifactLinkPayload(
                artifact_id=str(artifact_payload.get("artifact_id") or ""),
                session_id=_coerce_optional_string(artifact_payload.get("session_id")),
                provider=_coerce_optional_string(artifact_payload.get("provider")),
                source_message_id=_coerce_optional_string(
                    artifact_payload.get("source_message_id")
                ),
                source_attachment_id=_coerce_optional_string(
                    artifact_payload.get("source_attachment_id")
                ),
                source_channel=_coerce_optional_string(artifact_payload.get("source_channel")),
                source_mailbox=_coerce_optional_string(artifact_payload.get("source_mailbox")),
                original_filename=_coerce_optional_string(
                    artifact_payload.get("original_filename")
                ),
                filename=_coerce_optional_string(artifact_payload.get("filename")),
                mime_type=_coerce_optional_string(artifact_payload.get("mime_type")),
                byte_count=(
                    int(artifact_payload["byte_count"])
                    if artifact_payload.get("byte_count") is not None
                    else None
                ),
                sha256=_coerce_optional_string(artifact_payload.get("sha256")),
                created_at=_coerce_optional_datetime(artifact_payload.get("created_at")),
                expires_at=_coerce_optional_datetime(artifact_payload.get("expires_at")),
                staged_at=_coerce_optional_datetime(artifact_payload.get("staged_at")),
                status=status,
                metadata=normalize_record_payload(artifact_payload.get("metadata")),
            )

        def infer_agent_backend(
            session: SessionManifest | None,
            *,
            vm_name: str | None,
        ) -> str | None:
            if session is None:
                return "libvirt_vm" if vm_name else None
            metadata = dict(session.metadata) if isinstance(session.metadata, dict) else {}
            execution_backend = _coerce_optional_string(metadata.get("execution_backend"))
            if execution_backend == "container_worker_vm":
                return execution_backend
            if _coerce_optional_string(session.vm_provider) == "container_worker_vm":
                return "container_worker_vm"
            if _coerce_optional_string(session.vm_provider_id) or _coerce_optional_string(
                session.vm_id
            ):
                return "libvirt_vm"
            return "libvirt_vm" if vm_name else None

        def select_agent_record_for_link(
            *,
            session_id: str | None,
            vm_name: str | None,
            backend_hint: str | None,
        ) -> AgentRecord | None:
            candidates: list[AgentRecord] = []
            if session_id:
                candidates.extend(agent_records_by_session_id.get(session_id, []))
            if not candidates and vm_name:
                candidates.extend(agent_records_by_target_name.get(vm_name, []))
            if not candidates:
                return None
            if backend_hint is not None:
                for candidate in candidates:
                    if candidate.backend == backend_hint:
                        return candidate
            return candidates[0]

        def build_tool_call_href(entry: ToolCallAuditEntryPayload) -> str | None:
            for candidate in (
                entry.request_id,
                entry.correlation_id,
                entry.approval_request_id,
                entry.trace_id,
                entry.session_id,
            ):
                text = str(candidate or "").strip()
                if text:
                    return f"#tool-calls?q={quote(text, safe='')}"
            return None

        def compact_payload_summary(value: Any, *, max_length: int = 180) -> str | None:
            if value is None:
                return None
            if isinstance(value, str):
                text = value.strip()
            elif isinstance(value, (dict, list)):
                if not value:
                    return None
                text = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
            else:
                text = str(value).strip()
            if not text:
                return None
            if len(text) <= max_length:
                return text
            return f"{text[: max_length - 1].rstrip()}..."

        def build_tool_call_link(
            entry: ToolCallAuditEntryPayload,
            *,
            node_id: str | None = None,
        ) -> WorkflowRunToolCallLinkPayload:
            payload = entry.model_dump(mode="python", exclude={"related_links"})
            payload["node_id"] = node_id
            payload["href"] = build_tool_call_href(entry)
            filter_payload: dict[str, Any] = {}
            if entry.request_id:
                filter_payload["q"] = entry.request_id
            elif entry.correlation_id:
                filter_payload["q"] = entry.correlation_id
            elif entry.approval_request_id:
                filter_payload["q"] = entry.approval_request_id
            elif entry.trace_id:
                filter_payload["q"] = entry.trace_id
            elif entry.session_id:
                filter_payload["q"] = entry.session_id
            payload["filter"] = filter_payload or None
            payload["redacted_args"] = dict(entry.args)
            payload["redacted_result"] = dict(entry.result)
            payload["args_summary"] = compact_payload_summary(entry.args)
            payload["args_missing_reason"] = None if entry.args else "Args payload missing."
            payload["result_summary"] = compact_payload_summary(entry.result)
            payload["result_empty"] = not bool(entry.result)
            payload["result_status"] = "available" if entry.result else "recorded_empty"
            payload["result_missing_reason"] = (
                None if entry.result else "Tool call recorded an empty result."
            )
            return WorkflowRunToolCallLinkPayload.model_validate(payload)

        def build_agent_link(
            *,
            node_id: str,
            node_type: str | None,
            record: dict[str, Any],
            source_kind: str,
        ) -> WorkflowRunAgentLinkPayload | None:
            resolved_config = normalize_record_payload(record.get("resolved_config"))
            input_payload = normalize_record_payload(record.get("input_payload"))
            outputs = normalize_record_payload(record.get("outputs"))
            merged = {**resolved_config, **input_payload, **outputs}
            session_candidates = collect_keyed_values(merged, key_suffix="session_id")
            vm_name = _coerce_optional_string(merged.get("vm_name"))
            prompt = _coerce_optional_string(
                merged.get("prompt")
                or merged.get("content")
                or merged.get("message")
            )
            dispatch_id = _coerce_optional_string(
                merged.get("dispatch_id")
                or outputs.get("dispatch_id")
                or record.get("dispatch_id")
            )
            thread_id = _coerce_optional_string(
                merged.get("thread_id")
                or record.get("thread_id")
                or outputs.get("thread_id")
            )
            execution_id = _coerce_optional_string(
                merged.get("execution_id")
                or record.get("execution_id")
                or outputs.get("execution_id")
            )
            artifact_ids = collect_artifact_ids(merged)
            status = _coerce_optional_string(record.get("status"))
            waiting = status == "waiting"
            completed = status in {"succeeded", "failed", "completed", "dismissed"}
            if not any(
                (
                    session_candidates,
                    vm_name,
                    prompt,
                    dispatch_id,
                    thread_id,
                    artifact_ids,
                    waiting,
                )
            ):
                return None
            session_id = session_candidates[0] if session_candidates else None
            session = (
                self.runtime.session_repository.get_session(session_id)
                if session_id is not None
                else None
            )
            backend_hint = infer_agent_backend(session, vm_name=vm_name)
            selected_agent_record = select_agent_record_for_link(
                session_id=session_id,
                vm_name=vm_name,
                backend_hint=backend_hint,
            )
            agent_id = (
                _coerce_optional_string(selected_agent_record.agent_id)
                if selected_agent_record is not None
                else session_id
                or vm_name
            )
            backend = (
                _coerce_optional_string(selected_agent_record.backend)
                if selected_agent_record is not None
                else backend_hint
            )
            target_name = (
                _coerce_optional_string(selected_agent_record.target_name)
                if selected_agent_record is not None
                else _coerce_optional_string(session.vm_id if session is not None else None)
                or vm_name
            )
            session_status = "available" if session is not None else "session_missing"
            session_missing_reason = None if session is not None else "Session record missing."
            agent_available = selected_agent_record is not None
            agent_status = (
                _coerce_optional_string(selected_agent_record.status)
                if selected_agent_record is not None
                else "missing"
            )
            agent_missing_reason = (
                None
                if selected_agent_record is not None
                else "No agent record matched the session."
            )
            log_status = "log_unavailable"
            log_reference_href = None
            log_missing_reason = None
            if session is None:
                log_missing_reason = session_missing_reason
            elif selected_agent_record is None:
                log_missing_reason = agent_missing_reason
            elif selected_agent_record.capabilities.get("logs"):
                log_status = "log_available"
                log_reference_href = (
                    f"/api/admin/agents/{quote(selected_agent_record.agent_id, safe='')}/log"
                    f"?backend={quote(selected_agent_record.backend, safe='')}"
                )
            else:
                log_missing_reason = "Agent record does not advertise log support."
            if "outputs" not in record:
                result_status = "missing"
                result_summary = None
                result_missing_reason = "Result payload missing."
                result_empty = None
            elif outputs:
                result_status = "available"
                result_summary = compact_payload_summary(outputs)
                result_missing_reason = None
                result_empty = False
            else:
                result_status = "recorded_empty"
                result_summary = "Empty result"
                result_missing_reason = None
                result_empty = True
            prompt_summary = prompt
            prompt_missing_reason = None if prompt_summary else "Prompt summary missing."
            return WorkflowRunAgentLinkPayload(
                node_id=node_id,
                node_type=node_type,
                session_id=session_id,
                agent_id=agent_id,
                backend=backend,
                agent_backend=backend,
                agent_available=agent_available,
                agent_status=agent_status,
                agent_missing_reason=agent_missing_reason,
                target_name=target_name,
                vm_name=vm_name,
                prompt=prompt,
                prompt_summary=prompt_summary,
                prompt_status="available" if prompt_summary else "missing",
                prompt_missing_reason=prompt_missing_reason,
                dispatch_id=dispatch_id,
                thread_id=thread_id,
                execution_id=execution_id,
                artifact_ids=artifact_ids,
                status=status,
                waiting=waiting,
                completed=completed,
                session_status=session_status,
                session_available=session is not None,
                session_missing_reason=session_missing_reason,
                session_href=f"#sessions?session_id={quote(session_id, safe='')}"
                if session_id
                else None,
                log_status=log_status,
                log_available=log_status == "log_available",
                log_href=log_reference_href,
                log_reference_href=log_reference_href,
                log_missing_reason=log_missing_reason,
                log_reference={
                    "source_kind": source_kind,
                    "node_id": node_id,
                    "session_id": session_id,
                    "vm_name": vm_name,
                    "thread_id": thread_id,
                    "execution_id": execution_id,
                    "dispatch_id": dispatch_id,
                },
                result_summary=result_summary,
                result_status=result_status,
                result_missing_reason=result_missing_reason,
                result_empty=result_empty,
            )

        for node_id, record_entries in node_groups.items():
            for _source_kind, record, _node_id, node_type in record_entries:
                request_ids = collect_keyed_values(record, key_suffix="request_id")
                if request_ids:
                    node_request_ids_by_node.setdefault(node_id, set()).update(request_ids)
                for request_id in request_ids:
                    request = approval_requests_by_id.get(request_id)
                    if request is None:
                        continue
                    approval_links_by_node.setdefault(node_id, []).append(
                        build_approval_link(request)
                    )
                session_candidates = collect_keyed_values(record, key_suffix="session_id")
                if session_candidates:
                    node_session_ids_by_node.setdefault(node_id, set()).update(
                        session_candidates
                    )
                for session_candidate in session_candidates:
                    for request in approval_requests_by_session.get(session_candidate, []):
                        approval_links_by_node.setdefault(node_id, []).append(
                            build_approval_link(request)
                        )
                agent_link = build_agent_link(
                    node_id=node_id,
                    node_type=node_type,
                    record=record,
                    source_kind=_source_kind,
                )
                if agent_link is not None:
                    agent_links_by_node.setdefault(node_id, []).append(agent_link)
                artifact_ids = collect_artifact_ids(record)
                for artifact in artifact_ids:
                    try:
                        link_payload = build_artifact_link(artifact_store.payload(artifact))
                    except FileNotFoundError:
                        link_payload = WorkflowRunCommunicationArtifactLinkPayload(
                            artifact_id=artifact,
                            status="missing_legacy_or_unrecorded",
                        )
                    artifact_links_by_node.setdefault(node_id, []).append(link_payload)

        for entry, _raw_payload in sorted(
            tool_call_audit_entries,
            key=lambda item: (
                item[0].created_at,
                item[0].tool_name or "",
                item[0].request_id or "",
                item[0].correlation_id or "",
                item[0].approval_request_id or "",
            ),
            reverse=True,
        ):
            request_node_matches = [
                node_id
                for node_id in sorted(node_groups)
                if (
                    (
                        entry.request_id
                        and entry.request_id in node_request_ids_by_node.get(node_id, set())
                    )
                    or (
                        entry.approval_request_id
                        and entry.approval_request_id
                        in node_request_ids_by_node.get(node_id, set())
                    )
                )
            ]
            node_matches = request_node_matches
            entry_session_id = _coerce_optional_string(entry.session_id)
            if not node_matches and entry_session_id is not None:
                session_node_matches = [
                    node_id
                    for node_id in sorted(node_groups)
                    if entry_session_id in node_session_ids_by_node.get(node_id, set())
                ]
                if len(session_node_matches) == 1:
                    node_matches = session_node_matches
            link_payload = build_tool_call_link(
                entry,
                node_id=node_matches[0] if len(node_matches) == 1 else None,
            )
            if len(node_matches) == 1:
                tool_call_links_by_node.setdefault(node_matches[0], []).append(link_payload)
                continue
            run_tool_call_links.append(link_payload)

        def build_node_detail(
            node_id: str, record_entries: list[tuple[str, dict[str, Any], str | None, str | None]]
        ) -> WorkflowRunNodeDetailPayload:
            ordered_entries = sorted(
                record_entries,
                key=lambda item: (
                    _record_latest_activity(item[1]) or datetime.min.replace(tzinfo=UTC),
                    _coerce_optional_string(item[1].get("execution_id")) or "",
                ),
                reverse=True,
            )
            latest_source_kind, latest_record, _latest_node_id, latest_node_type = ordered_entries[0]
            node_metadata = node_metadata_by_id.get(node_id, {})
            latest_started_at = _coerce_optional_datetime(latest_record.get("started_at"))
            latest_finished_at = _coerce_optional_datetime(latest_record.get("finished_at"))
            latest_occurred_at = _coerce_optional_datetime(latest_record.get("occurred_at"))
            latest_status = _coerce_optional_string(latest_record.get("status"))
            if latest_status is None:
                latest_status = _coerce_optional_string(latest_record.get("delivery_state"))
            retry_resume_reason: str | None = None
            retry_resume_eligible = False
            if latest_status == "failed":
                resumable_record = self._latest_resumable_failed_workflow_record(
                    event_id=selected_event_id,
                    effect_payload={
                        key: value
                        for key, value in (
                            (
                                _coerce_optional_string(record.get("execution_id")) or node_id,
                                record,
                            )
                            for _source_kind, record, _record_node_id, _record_node_type in ordered_entries
                            if _source_kind == "effect_execution"
                        )
                    },
                    gate_payload={
                        key: value
                        for key, value in (
                            (
                                _coerce_optional_string(record.get("execution_id")) or node_id,
                                record,
                            )
                            for _source_kind, record, _record_node_id, _record_node_type in ordered_entries
                            if _source_kind == "gate_execution"
                        )
                    },
                )
                if resumable_record is not None:
                    retry_resume_eligible = resumable_record["node_id"] == node_id
                    if retry_resume_eligible:
                        retry_resume_reason = "Failed node has a resumable input payload."
                elif latest_status == "failed":
                    retry_resume_reason = (
                        "Failed node is missing source_node_id, source_trigger_type, trace_id, or input_payload."
                    )
            missing_fields: list[str] = []
            if latest_source_kind == "trigger_event":
                if not _coerce_optional_string(latest_record.get("delivery_state")):
                    missing_fields.append("delivery_state")
                if not latest_record.get("payload"):
                    missing_fields.append("payload")
                if latest_record.get("binding_trace") is None:
                    missing_fields.append("binding_trace")
            else:
                for field_name in ("resolved_config", "input_payload", "outputs"):
                    if not latest_record.get(field_name):
                        missing_fields.append(field_name)
                if latest_record.get("binding_trace") is None:
                    missing_fields.append("binding_trace")
            approval_links = approval_links_by_node.get(node_id, [])
            agent_links = agent_links_by_node.get(node_id, [])
            artifact_links = artifact_links_by_node.get(node_id, [])
            deduped_approval_links: list[WorkflowRunApprovalLinkPayload] = []
            seen_approval_ids: set[str] = set()
            for approval_link in approval_links:
                if approval_link.request_id in seen_approval_ids:
                    continue
                seen_approval_ids.add(approval_link.request_id)
                deduped_approval_links.append(approval_link)
            deduped_agent_links: list[WorkflowRunAgentLinkPayload] = []
            seen_agent_keys: set[tuple[str | None, str | None, str | None]] = set()
            for agent_link in agent_links:
                agent_key = (agent_link.session_id, agent_link.dispatch_id, agent_link.execution_id)
                if agent_key in seen_agent_keys:
                    continue
                seen_agent_keys.add(agent_key)
                deduped_agent_links.append(agent_link)
            deduped_artifact_links: list[WorkflowRunCommunicationArtifactLinkPayload] = []
            seen_artifact_ids: set[str] = set()
            for artifact_link in artifact_links:
                if artifact_link.artifact_id in seen_artifact_ids:
                    continue
                seen_artifact_ids.add(artifact_link.artifact_id)
                deduped_artifact_links.append(artifact_link)
            tool_call_links = tool_call_links_by_node.get(node_id, [])
            deduped_tool_call_links: list[WorkflowRunToolCallLinkPayload] = []
            seen_tool_call_keys: set[
                tuple[str | None, str | None, str | None, str | None, datetime]
            ] = set()
            for tool_call_link in tool_call_links:
                tool_call_key = (
                    tool_call_link.tool_name,
                    tool_call_link.request_id,
                    tool_call_link.correlation_id,
                    tool_call_link.approval_request_id,
                    tool_call_link.created_at,
                )
                if tool_call_key in seen_tool_call_keys:
                    continue
                seen_tool_call_keys.add(tool_call_key)
                deduped_tool_call_links.append(tool_call_link)
            return WorkflowRunNodeDetailPayload(
                node_id=node_id,
                node_type=_coerce_optional_string(latest_node_type)
                or _coerce_optional_string(node_metadata.get("node_type")),
                display_name=_coerce_optional_string(node_metadata.get("display_name")),
                source_kind=latest_source_kind,
                execution_id=_coerce_optional_string(latest_record.get("execution_id")),
                event_id=_coerce_optional_string(latest_record.get("event_id")),
                trace_id=_coerce_optional_string(latest_record.get("trace_id")),
                status=latest_status,
                started_at=latest_started_at,
                finished_at=latest_finished_at,
                duration_ms=duration_ms(latest_started_at, latest_finished_at),
                occurred_at=latest_occurred_at,
                delivery_state=_coerce_optional_string(latest_record.get("delivery_state")),
                resolved_config=normalize_record_payload(latest_record.get("resolved_config")),
                binding_trace=normalize_record_payload(latest_record.get("binding_trace")) or None,
                binding_error=_coerce_optional_string(latest_record.get("binding_error")),
                input_payload=normalize_record_payload(latest_record.get("input_payload")),
                outputs=normalize_record_payload(latest_record.get("outputs")),
                inputs_by_source=normalize_record_payload(latest_record.get("inputs_by_source")),
                input_event_ids_by_source=normalize_record_payload(
                    latest_record.get("input_event_ids_by_source")
                ),
                input_occurred_at_by_source={
                    key: _coerce_optional_datetime(value)
                    for key, value in normalize_record_payload(
                        latest_record.get("input_occurred_at_by_source")
                    ).items()
                    if _coerce_optional_datetime(value) is not None
                },
                error=_coerce_optional_string(latest_record.get("error")),
                published_event_id=_coerce_optional_string(
                    latest_record.get("published_event_id")
                ),
                completion_event_enqueued_at=_coerce_optional_datetime(
                    latest_record.get("completion_event_enqueued_at")
                ),
                config_revision=_coerce_optional_string(latest_record.get("config_revision")),
                retry_resume_eligible=retry_resume_eligible,
                retry_resume_reason=retry_resume_reason,
                missing_fields=sorted(dict.fromkeys(missing_fields)),
                approval_links=deduped_approval_links,
                agent_links=deduped_agent_links,
                communication_artifact_links=deduped_artifact_links,
                tool_call_links=deduped_tool_call_links,
                records=[
                    build_node_record_payload(
                        record_kind=source_kind,
                        record=record,
                        node_id=node_id,
                        node_type=node_type,
                    )
                    for source_kind, record, _node_id, node_type in ordered_entries
                ],
            )

        node_details = [
            build_node_detail(node_id, record_entries)
            for node_id, record_entries in sorted(node_groups.items())
        ]

        deduped_run_tool_call_links: list[WorkflowRunToolCallLinkPayload] = []
        seen_run_tool_call_keys: set[
            tuple[str | None, str | None, str | None, str | None, datetime]
        ] = set()
        for tool_call_link in sorted(
            run_tool_call_links,
            key=lambda item: (
                item.created_at,
                item.tool_name or "",
                item.request_id or "",
                item.correlation_id or "",
                item.approval_request_id or "",
            ),
            reverse=True,
        ):
            tool_call_key = (
                tool_call_link.tool_name,
                tool_call_link.request_id,
                tool_call_link.correlation_id,
                tool_call_link.approval_request_id,
                tool_call_link.created_at,
            )
            if tool_call_key in seen_run_tool_call_keys:
                continue
            seen_run_tool_call_keys.add(tool_call_key)
            deduped_run_tool_call_links.append(tool_call_link)
        if tool_call_audit_entries:
            graph_data_availability.tool_calls = "available"

        resumable_node = next(
            (node for node in node_details if node.retry_resume_eligible),
            None,
        )
        resume_action = WorkflowRunRecoveryActionPayload(
            eligible=resumable_node is not None,
            reason=(
                resumable_node.retry_resume_reason if resumable_node is not None else "No failed node with a resumable input payload was found."
            ),
            event_id=selected_event_id,
            target_node_id=resumable_node.node_id if resumable_node is not None else None,
            source_node_id=resumable_node.records[0].source_node_id if resumable_node and resumable_node.records else None,
            source_trigger_type=resumable_node.records[0].source_trigger_type if resumable_node and resumable_node.records else None,
            source_event_id=resumable_node.records[0].event_id if resumable_node and resumable_node.records else None,
        )
        clear_action = WorkflowRunRecoveryActionPayload(
            eligible=True,
            reason="Workflow run records can be cleared from the editor.",
            event_id=selected_event_id,
            source_event_id=selected_event_id,
        )

        trigger_payload = None
        if trigger_record is not None:
            trigger_payload = WorkflowRunTriggerEventPayload(
                event_id=_coerce_optional_string(trigger_record.get("event_id")) or selected_event_id,
                trigger_id=_coerce_optional_string(trigger_record.get("trigger_id")) or "",
                trigger_type=_coerce_optional_string(trigger_record.get("trigger_type")) or "",
                trace_id=_coerce_optional_string(trigger_record.get("trace_id")) or "",
                occurred_at=_coerce_optional_datetime(trigger_record.get("occurred_at")),
                delivery_state=_coerce_optional_string(trigger_record.get("delivery_state")),
                inflight_at=_coerce_optional_datetime(trigger_record.get("inflight_at")),
                delivered_at=_coerce_optional_datetime(trigger_record.get("delivered_at")),
                clear_reason=_coerce_optional_string(trigger_record.get("clear_reason")),
                error=_coerce_optional_string(trigger_record.get("error")),
                idempotency_key=_coerce_optional_string(trigger_record.get("idempotency_key")),
                payload=normalize_record_payload(trigger_record.get("payload")),
            )

        if approval_links_by_node:
            graph_data_availability.approvals = "available"
        if agent_links_by_node:
            graph_data_availability.agent_links = "available"
        if artifact_links_by_node:
            graph_data_availability.communication_artifacts = "available"
        if any(node.binding_trace is not None for node in node_details):
            graph_data_availability.binding_trace = "available"

        selected_summary = WorkflowRunSummaryPayload.model_validate(
            {
                "event_id": selected_event_id,
                "run_state": "errored"
                if any(node.status == "failed" for node in node_details)
                else "active",
                "trace_id": selected_trace_id,
                "source_node_id": _coerce_optional_string(
                    (trigger_record or {}).get("trigger_id") if trigger_record else None
                ),
                "source_trigger_type": _coerce_optional_string(
                    (trigger_record or {}).get("trigger_type") if trigger_record else None
                ),
                "delivery_state": _coerce_optional_string(
                    (trigger_record or {}).get("delivery_state") if trigger_record else None
                ),
                "occurred_at": _coerce_optional_datetime(
                    (trigger_record or {}).get("occurred_at") if trigger_record else None
                ),
                "latest_activity_at": selected_group.get("latest_activity_at"),
                "active_nodes": [
                    build_node_activity_payload(
                        source_kind=source_kind,
                        record=record,
                        node_id=node_id,
                    )
                    for node_id, record_entries in sorted(node_groups.items())
                    for source_kind, record, _record_node_id, node_type in record_entries
                ],
                "failed_node_id": next(
                    (node.node_id for node in node_details if node.status == "failed"),
                    None,
                ),
                "failed_node_status": "failed"
                if any(node.status == "failed" for node in node_details)
                else None,
                "failed_at": next(
                    (node.finished_at or node.started_at for node in node_details if node.status == "failed"),
                    None,
                ),
                "error": next((node.error for node in node_details if node.error), None),
                "resumable": resumable_node is not None,
            }
        )

        return WorkflowRunDetailPayload(
            generated_at=_utc_now(),
            lookup=lookup,
            candidate_matches=candidate_matches,
            lookup_candidates=candidate_matches,
            matched_by=selected_matched_by,
            event_id=selected_summary.event_id,
            trace_id=selected_summary.trace_id,
            source_node_id=selected_summary.source_node_id,
            source_trigger_type=selected_summary.source_trigger_type,
            delivery_state=selected_summary.delivery_state,
            occurred_at=selected_summary.occurred_at,
            latest_activity_at=selected_summary.latest_activity_at,
            run_state=selected_summary.run_state,
            active_nodes=selected_summary.active_nodes,
            failed_node_id=selected_summary.failed_node_id,
            failed_node_status=selected_summary.failed_node_status,
            failed_at=selected_summary.failed_at,
            error=selected_summary.error,
            resumable=selected_summary.resumable,
            recovery=WorkflowRunRecoveryPayload(clear=clear_action, resume=resume_action),
            trigger_event=trigger_payload,
            graph=WorkflowRunGraphPayload(
                config_revision=config_revision,
                config=normalize_record_payload(document_config),
                snapshot=graph_snapshot,
                data_availability=graph_data_availability,
            ),
            nodes=node_details,
            tool_call_links=deduped_run_tool_call_links,
            data_availability=graph_data_availability,
        )

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

    def _merge_system_managed_workflows(
        self, *, editable_config: dict[str, object], base_config: dict[str, object] | None
    ) -> dict[str, object]:
        del base_config
        return self._strip_system_managed_workflows(editable_config)

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

        with self.admin_config_mutation_lock():
            current = self.trigger_repository.read_document()
            config = dict(current.config)
            existing_triggers_raw = config.get("triggers", [])
            if not isinstance(existing_triggers_raw, list):
                raise ValueError(
                    "Invalid trigger-orchestrator config: triggers must be a JSON array."
                )
            existing_triggers = [
                entry for entry in existing_triggers_raw if isinstance(entry, dict)
            ]
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
            write_result = self._write_trigger_document_unlocked(config)
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
        with self.admin_config_mutation_lock():
            current = self.trigger_repository.read_document()
            config = dict(current.config)
            triggers_raw = config.get("triggers", [])
            if not isinstance(triggers_raw, list):
                raise ValueError(
                    "Invalid trigger-orchestrator config: triggers must be a JSON array."
                )
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
            write_result = self._write_trigger_document_unlocked(config)
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

        with self.admin_config_mutation_lock():
            current = self._read_worker_global_config_unlocked("trigger-orchestrator")
            config = dict(current.config)
            existing_effects_raw = config.get("effects", [])
            if not isinstance(existing_effects_raw, list):
                raise ValueError(
                    "Invalid trigger-orchestrator config: effects must be a JSON array."
                )
            existing_effects = [
                entry for entry in existing_effects_raw if isinstance(entry, dict)
            ]
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
            write_result = self._write_worker_global_config_unlocked(
                "trigger-orchestrator", config
            )
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
        with self.admin_config_mutation_lock():
            current = self._read_worker_global_config_unlocked("trigger-orchestrator")
            config = dict(current.config)
            effects_raw = config.get("effects", [])
            if not isinstance(effects_raw, list):
                raise ValueError(
                    "Invalid trigger-orchestrator config: effects must be a JSON array."
                )
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
            write_result = self._write_worker_global_config_unlocked(
                "trigger-orchestrator", config
            )
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
        tool_argument_policies: dict[str, list[Any]] | None = None,
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
        allow_existing_session: bool = False,
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
            tool_argument_policies=tool_argument_policies,
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
            allow_existing_session=allow_existing_session,
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


def _parse_tool_call_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _tool_call_directory_matches(
    record_directory_path: str | None,
    selected_directory_path: str | None,
    *,
    direct_only: bool,
) -> bool:
    if selected_directory_path is None:
        return True
    record_path = AdminService._agent_record_directory_path(record_directory_path)
    selected_path = AdminService._agent_record_directory_path(selected_directory_path)
    if direct_only:
        return record_path == selected_path
    return record_path == selected_path or record_path.startswith(f"{selected_path}/")


def _tool_call_normalize_status(status: Any) -> str | None:
    normalized = str(status or "").strip().casefold()
    if not normalized:
        return None
    if normalized in {"allowed", "approved", "success", "ok"}:
        return "success"
    if normalized in {"pending_approval"}:
        return "pending_approval"
    if normalized in {"denied", "blocked", "rejected"}:
        return "denied"
    if normalized in {"error", "failed", "failure"}:
        return "error"
    raise ValueError("status must be one of: success, pending_approval, denied, or error")


def _tool_call_failure_status(event: dict[str, Any]) -> str:
    decision = str(event.get("decision") or event.get("status") or "").strip().casefold()
    result = event.get("result") if isinstance(event.get("result"), dict) else {}
    result_status = str(result.get("status") or "").strip().casefold()
    if decision == "pending_approval":
        return "pending_approval"
    if decision in {"denied", "blocked", "rejected"}:
        return "denied"
    if decision in {"error", "failed", "failure"}:
        return "error"
    if decision in {"allowed", "approved", "success", "ok"}:
        return "success"
    if result_status == "pending_approval":
        return "pending_approval"
    if result_status in {"denied", "blocked", "rejected"}:
        return "denied"
    if result_status in {"error", "failed", "failure"}:
        return "error"
    if result_status in {"allowed", "approved", "success", "ok"}:
        return "success"
    if isinstance(result, dict) and any(
        key in result for key in ("error", "error_class", "exception", "traceback")
    ):
        return "error"
    return "success"


def _approval_audit_payload_matches_request(
    payload: dict[str, Any], request: ApprovalRequest
) -> bool:
    args_payload = payload.get("args") if isinstance(payload.get("args"), dict) else {}
    result_payload = (
        payload.get("result") if isinstance(payload.get("result"), dict) else {}
    )
    for candidate in (
        payload.get("approval_request_id"),
        args_payload.get("approval_request_id"),
        result_payload.get("approval_request_id"),
        args_payload.get("request_id"),
        result_payload.get("request_id"),
        payload.get("payload_digest"),
        args_payload.get("payload_digest"),
        result_payload.get("payload_digest"),
    ):
        text = str(candidate or "").strip()
        if not text:
            continue
        if text == request.request_id or text == request.payload_digest:
            return True
    return payload.get("session_id") == request.session_id


def _tool_call_error_summary(entry: dict[str, Any], status: str, result: dict[str, Any]) -> str | None:
    for candidate in (
        entry.get("reason"),
        result.get("error"),
        result.get("message"),
        result.get("details"),
        result.get("summary"),
    ):
        text = str(candidate or "").strip()
        if text:
            return text
    if status == "denied":
        return "Denied"
    if status == "error":
        return "Tool call failed."
    if status == "pending_approval":
        return "Approval required."
    return None


def _tool_call_safe_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return str(value)


def _tool_call_related_links(entry: ToolCallAuditEntryPayload) -> list[ToolCallAuditRelatedLinkPayload]:
    links: list[ToolCallAuditRelatedLinkPayload] = []
    if entry.session_id:
        links.append(
            ToolCallAuditRelatedLinkPayload(
                kind="session",
                label="Session",
                value=entry.session_id,
                href=f"/api/admin/sessions/{quote(entry.session_id, safe='')}",
            )
        )
    if entry.vm_id:
        links.append(
            ToolCallAuditRelatedLinkPayload(
                kind="vm",
                label="VM",
                value=entry.vm_id,
                href=f"/api/admin/vms/{quote(entry.vm_id, safe='')}",
            )
        )
    if entry.approval_request_id:
        links.append(
            ToolCallAuditRelatedLinkPayload(
                kind="approval_request",
                label="Approval request",
                value=entry.approval_request_id,
                href=f"/api/admin/approvals/{quote(entry.approval_request_id, safe='')}",
            )
        )
    if entry.workflow_run_id:
        links.append(
            ToolCallAuditRelatedLinkPayload(
                kind="workflow_run",
                label="Workflow run",
                value=entry.workflow_run_id,
                href=(
                    "/api/workflow-editor/run-detail?run_id="
                    f"{quote(entry.workflow_run_id, safe='')}"
                ),
            )
        )
    if entry.trace_id:
        links.append(
            ToolCallAuditRelatedLinkPayload(
                kind="workflow_trace",
                label="Workflow trace",
                value=entry.trace_id,
                href=(
                    "/api/workflow-editor/run-detail?trace_id="
                    f"{quote(entry.trace_id, safe='')}"
                ),
            )
        )
    return links


def _tool_call_audit_entry_from_payload(payload: dict[str, Any]) -> ToolCallAuditEntryPayload | None:
    created_at = _parse_tool_call_datetime(
        payload.get("created_at") or payload.get("timestamp") or payload.get("occurred_at")
    )
    if created_at is None:
        return None
    raw_args = payload.get("args") if isinstance(payload.get("args"), dict) else {}
    raw_result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    redacted_args = _redact_sensitive_payload(raw_args)
    redacted_result = _redact_sensitive_payload(raw_result)
    status = _tool_call_failure_status(payload)
    decision = str(payload.get("decision") or payload.get("status") or "").strip() or None
    request_id = None
    for candidate in (
        payload.get("request_id"),
        raw_args.get("request_id"),
        raw_result.get("request_id"),
    ):
        text = str(candidate or "").strip()
        if text:
            request_id = text
            break
    correlation_id = None
    for candidate in (
        payload.get("correlation_id"),
        raw_args.get("correlation_id"),
        raw_result.get("correlation_id"),
    ):
        text = str(candidate or "").strip()
        if text:
            correlation_id = text
            break
    approval_request_id = None
    for candidate in (
        payload.get("approval_request_id"),
        raw_args.get("approval_request_id"),
        raw_result.get("approval_request_id"),
    ):
        text = str(candidate or "").strip()
        if text:
            approval_request_id = text
            break
    mcp_tool_approval_lease_id = None
    for candidate in (
        payload.get("mcp_tool_approval_lease_id"),
        payload.get("tool_approval_lease_id"),
        raw_args.get("mcp_tool_approval_lease_id"),
        raw_args.get("tool_approval_lease_id"),
        raw_result.get("mcp_tool_approval_lease_id"),
        raw_result.get("tool_approval_lease_id"),
    ):
        text = str(candidate or "").strip()
        if text:
            mcp_tool_approval_lease_id = text
            break
    tool_name = None
    for candidate in (payload.get("tool_name"), payload.get("tool"), raw_args.get("tool_name")):
        text = str(candidate or "").strip()
        if text:
            tool_name = text
            break
    session_id = None
    for candidate in (
        payload.get("session_id"),
        raw_args.get("session_id"),
        raw_result.get("session_id"),
        payload.get("target_session_id"),
    ):
        text = str(candidate or "").strip()
        if text:
            session_id = text
            break
    vm_id = None
    for candidate in (
        payload.get("vm_id"),
        payload.get("vm_name"),
        raw_args.get("vm_id"),
        raw_args.get("vm_name"),
        raw_result.get("vm_id"),
        raw_result.get("vm_name"),
    ):
        text = str(candidate or "").strip()
        if text:
            vm_id = text
            break
    directory_path = AdminService._agent_record_directory_path(payload.get("directory_path"))
    agent_role = None
    for candidate in (payload.get("agent_role"), payload.get("role")):
        text = str(candidate or "").strip()
        if text:
            agent_role = text
            break
    actor_user_id = None
    for candidate in (payload.get("actor_user_id"), payload.get("user_id")):
        text = str(candidate or "").strip()
        if text:
            actor_user_id = text
            break
    workflow_run_id = None
    for candidate in (
        payload.get("workflow_run_id"),
        raw_args.get("workflow_run_id"),
        raw_result.get("workflow_run_id"),
    ):
        text = str(candidate or "").strip()
        if text:
            workflow_run_id = text
            break
    trace_id = None
    for candidate in (
        payload.get("trace_id"),
        payload.get("workflow_trace_id"),
        raw_args.get("trace_id"),
        raw_args.get("workflow_trace_id"),
        raw_result.get("trace_id"),
        raw_result.get("workflow_trace_id"),
    ):
        text = str(candidate or "").strip()
        if text:
            trace_id = text
            break
    duration_ms = payload.get("duration_ms")
    try:
        normalized_duration_ms = int(duration_ms) if duration_ms is not None else None
    except (TypeError, ValueError):
        normalized_duration_ms = None
    entry = ToolCallAuditEntryPayload(
        created_at=created_at,
        session_id=session_id,
        vm_id=vm_id,
        directory_path=directory_path,
        agent_role=agent_role,
        actor_user_id=actor_user_id,
        tool_name=tool_name,
        request_id=request_id,
        correlation_id=correlation_id,
        approval_request_id=approval_request_id,
        mcp_tool_approval_lease_id=mcp_tool_approval_lease_id,
        workflow_run_id=workflow_run_id,
        trace_id=trace_id,
        duration_ms=normalized_duration_ms,
        decision=decision,
        status=status,
        error_summary=_tool_call_error_summary(payload, status, raw_result),
        args=redacted_args,
        result=redacted_result,
    )
    entry.related_links = _tool_call_related_links(entry)
    return entry


def _tool_call_audit_tail_scan_supported(
    *,
    session_id: str | None,
    tool_name: str | None,
    status: str | None,
    created_after: datetime | None,
    created_before: datetime | None,
    q: str | None,
    limit: int,
) -> bool:
    if limit > 50:
        return False
    if created_after is not None or created_before is not None:
        return False
    return not any(
        str(value or "").strip() for value in (session_id, tool_name, status, q)
    )


def _iter_file_lines_reverse(path: Path, *, block_size: int = 64 * 1024) -> Iterator[str]:
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        position = handle.tell()
        buffer = b""
        while position > 0:
            read_size = min(block_size, position)
            position -= read_size
            handle.seek(position)
            chunk = handle.read(read_size)
            lines = (chunk + buffer).split(b"\n")
            buffer = lines[0]
            for raw_line in reversed(lines[1:]):
                if raw_line:
                    yield raw_line.decode("utf-8", errors="replace")
        if buffer:
            yield buffer.decode("utf-8", errors="replace")


def _tool_call_audit_entry_matches_filters(
    entry: ToolCallAuditEntryPayload,
    *,
    session_id: str | None,
    tool_name: str | None,
    status: str | None,
    directory_path: str | None,
    direct_only: bool,
    created_after: datetime | None,
    created_before: datetime | None,
    q: str | None,
) -> bool:
    if session_id is not None and entry.session_id != str(session_id).strip():
        return False
    if tool_name is not None and entry.tool_name != str(tool_name).strip():
        return False
    if status is not None and entry.status != str(status).strip().casefold():
        return False
    if not _tool_call_directory_matches(
        entry.directory_path,
        directory_path,
        direct_only=direct_only,
    ):
        return False
    if created_after is not None and entry.created_at < created_after:
        return False
    if created_before is not None and entry.created_at > created_before:
        return False
    normalized_query = str(q or "").strip().casefold()
    if normalized_query:
        search_haystack = " ".join(
            part
            for part in (
                _tool_call_safe_text(entry.session_id),
                _tool_call_safe_text(entry.vm_id),
                _tool_call_safe_text(entry.directory_path),
                _tool_call_safe_text(entry.agent_role),
                _tool_call_safe_text(entry.actor_user_id),
                _tool_call_safe_text(entry.tool_name),
                _tool_call_safe_text(entry.request_id),
                _tool_call_safe_text(entry.correlation_id),
                _tool_call_safe_text(entry.approval_request_id),
                _tool_call_safe_text(entry.mcp_tool_approval_lease_id),
                _tool_call_safe_text(entry.workflow_run_id),
                _tool_call_safe_text(entry.trace_id),
                _tool_call_safe_text(entry.decision),
                _tool_call_safe_text(entry.status),
                _tool_call_safe_text(entry.error_summary),
                _tool_call_safe_text(entry.args),
                _tool_call_safe_text(entry.result),
            )
            if part
        ).casefold()
        if normalized_query not in search_haystack:
            return False
    return True
