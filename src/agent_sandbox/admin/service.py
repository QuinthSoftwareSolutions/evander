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
from collections.abc import Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from pydantic import (
    BaseModel,
    Field,
    TypeAdapter,
    ValidationError,
    computed_field,
    field_validator,
    model_validator,
)
import yaml

# Preferred admin domain ownership boundaries: operations, issue/git, VM/agent,
# workflow config, skills, reporting, and PR preflight. Keep new domain logic
# in the narrower services under ``agent_sandbox.admin.services`` instead of
# expanding the legacy AdminService facade.

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
from agent_sandbox.contact_allowlists import validate_allowed_contact_ids_for_directory
from agent_sandbox.config import (
    AdoWorkerGlobalConfig,
    AssistantSettingsConfig,
    DiscordWakeWorkerGlobalConfig,
    DiscordIngestWorkerGlobalConfig,
    ProxyIssueSyncWorkerGlobalConfig,
    ScheduledWakeWorkerGlobalConfig,
    TeamsIngestWorkerGlobalConfig,
    TeamsMeetingIngestWorkerGlobalConfig,
    TriggerOrchestratorWorkerGlobalConfig,
)
from agent_sandbox.cron import cron_min_interval_seconds, validate_cron_expression
from agent_sandbox.admin.directory import DirectoryAdminService
from agent_sandbox.admin.release_metadata import read_release_metadata
from agent_sandbox.admin.services import (
    git_helpers,
    issue_helpers,
    issue_setup_helpers,
    process_helpers,
    project_helpers,
    session_helpers,
    vm_agent_service,
    vm_helpers,
)
from agent_sandbox.admin.services.vm_agent_service import (
    AgentCreateOperation,
    AgentCreateOperationStatus,
    VmAgentAdminService,
)
from agent_sandbox.admin.services.issue_git_service import GitImportConflictError
from agent_sandbox.admin.services.local_issue_identity import LocalIssueIdentityResolver
from agent_sandbox.admin.tool_visibility import ToolVisibilityPayload
from agent_sandbox.directory_config import (
    DirectoryConfigResolver,
    DirectoryConfigStore,
    get_domain_base_config,
    validate_effective_domain_payload,
)
from agent_sandbox.model_usage.models import (
    ModelInferenceUsageEvent,
    ModelUsageCorrelationContext,
)
from agent_sandbox.model_usage.runtime import register_model_usage_correlation_context
from agent_sandbox.operator_steering import (
    OPERATOR_STEERING_NOTE_MAX_LENGTH,
    OperatorSteeringNote,
    operator_steering_note_store,
)
from agent_sandbox.directories import DirectoryService, DirectoryStore
from agent_sandbox.approval_notification_targets import (
    normalize_approval_notification_target_ids,
    resolve_approval_notification_target_ids_for_directory,
)
from agent_sandbox.git_pr_notes import agent_pr_notes_ref_for_session
from agent_sandbox.host_config import DEFAULT_RUNTIME_DIR
from agent_sandbox.bootstrap import resolve_runtime_dir
from agent_sandbox.messaging.targets import (
    COMMUNICATION_TARGETS_DOMAIN,
    load_communication_targets_config,
)
from agent_sandbox.ownership import DirectoryOwnershipQueries
from agent_sandbox.policy_config import EffectivePolicyConfig
from agent_sandbox.provider_config import (
    MODEL_PROVIDER_MODE_METADATA_KEY,
    normalize_model_provider_mode_override,
    ProviderConfigResolver,
)
from agent_sandbox.repositories.chat_assistant_sessions import JsonChatAssistantSessionRepository
from agent_sandbox.repositories.session_manifests import as_session_manifest_repository
from agent_sandbox.communication_artifacts import CommunicationArtifactStore
from agent_sandbox.container_worker_images import resolve_container_worker_image_selector
from agent_sandbox.vm.templates import resolve_libvirt_vm_template_selector
from agent_sandbox.effects.registry import EffectProviderFactory
from agent_sandbox.events.provider_models import AgentEnqueuePromptEffectDefinition
from agent_sandbox.execution.container_worker_client import (
    ContainerWorkerClient,
    ContainerWorkerClientError,
)
from agent_sandbox.execution.models import (
    ExecutionSpec,
    ExecutionStatusKind,
)
from agent_sandbox.published_artifacts import (
    PublishedArtifact,
    PublishedArtifactMetadata,
    PublishedArtifactStore,
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
    ApprovalGrantSettings,
    ApprovalRequest,
    ApprovalStatus,
    AgentPromptQueueRecord,
    AgentPromptQueueSummary,
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
    McpToolName,
    ToolName,
    VmActionResult,
    VmSummary,
    WakeScheduledRequest,
    WakeScheduleDefinition,
    WorkerActionResult,
    WorkerSummary,
    normalize_tool_name,
    normalize_approval_tool_settings,
    tool_name_value,
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
    AGENT_PROMPT_ERROR_PORT,
    AGENT_PROMPT_STARTED_PORT,
    migrate_agent_prompt_lifecycle_ports,
)
from agent_sandbox.workflow.runtime_config import (
    _append_builtin_hidden_workflows,
    _compose_configured_workflow_ir,
    compile_worker_global_config_with_diagnostics,
)
from agent_sandbox.workflow.catalog import (
    WorkflowEditorCatalogPayload,
    WorkflowEditorFieldDefinition,
    WorkflowEditorFieldOption,
    WorkflowEditorNodeCatalogEntry,
    WorkflowEditorNodePortDefinition,
)
from agent_sandbox.workflow.packs import (
    compose_workflow_ir,
    load_workflow_pack,
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
    json_loads_object,
)
from agent_sandbox.repositories.audit_index import (
    ToolCallAuditIndex,
)
from agent_sandbox.messaging.teams_auth import DeviceCodeLoginSession
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
from agent_sandbox.orchestration.host_polling import notify_wake_socket_for_lock
from agent_sandbox.workflow.validation import (
    WorkflowValidationDiagnostic,
    derive_workflow_output_schemas,
    validate_workflow_binding_diagnostics,
)
from agent_sandbox.services import (
    normalize_teams_chat_or_target_refs,
    normalize_teams_channel_or_target_refs,
    normalize_teams_user_or_target_refs,
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
_AGENT_CREATE_OPERATION_MAX_WORKERS = vm_agent_service.AGENT_CREATE_OPERATION_MAX_WORKERS
_CONTAINER_SESSION_ID_LOG_PATTERN = vm_agent_service._CONTAINER_SESSION_ID_LOG_PATTERN
_CONTAINER_THREAD_FULL_GROUP_RECORD_LIMIT = (
    vm_agent_service._CONTAINER_THREAD_FULL_GROUP_RECORD_LIMIT
)
ISSUE_STATES = issue_helpers.ISSUE_STATES
_PROTECTED_MIRROR_HEAD_REFSPECS = git_helpers.PROTECTED_MIRROR_HEAD_REFSPECS


EffectiveIssueSetupConfig = issue_setup_helpers.EffectiveIssueSetupConfig
resolve_effective_issue_setup_config = issue_setup_helpers.resolve_effective_issue_setup_config
resolve_agent_model_for_manifest = issue_setup_helpers.resolve_agent_model_for_manifest


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
    directory_path: str = "root"
    user_ids: list[str] = Field(default_factory=list)
    node_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalize_legacy_directory_paths(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        if "directory_path" in payload:
            payload.pop("directory_paths", None)
            return payload

        legacy_directory_paths = payload.pop("directory_paths", None)
        if legacy_directory_paths is None:
            payload["directory_path"] = "root"
            return payload

        if isinstance(legacy_directory_paths, str):
            raw_directory_paths = [legacy_directory_paths]
        elif isinstance(legacy_directory_paths, list):
            raw_directory_paths = legacy_directory_paths
        else:
            raw_directory_paths = [legacy_directory_paths]

        normalized_directory_paths: list[str] = []
        seen_directory_paths: set[str] = set()
        for raw_directory_path in raw_directory_paths:
            directory_path = str(raw_directory_path or "").strip()
            if not directory_path:
                continue
            try:
                normalized_directory_path = DirectoryPath(segments=directory_path).as_posix()
            except ValueError as exc:
                raise ValueError(
                    f"Workflow graph has invalid directory path: {directory_path}"
                ) from exc
            if normalized_directory_path in seen_directory_paths:
                continue
            seen_directory_paths.add(normalized_directory_path)
            normalized_directory_paths.append(normalized_directory_path)

        if len(normalized_directory_paths) > 1:
            raise ValueError("Workflow graph directory_paths must contain at most one directory.")
        payload["directory_path"] = (
            normalized_directory_paths[0] if normalized_directory_paths else "root"
        )
        return payload

    @field_validator("user_ids", "node_ids", mode="before")
    @classmethod
    def _normalize_string_lists(cls, value: object) -> object:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return value

    @field_validator("directory_path", mode="before")
    @classmethod
    def _normalize_directory_path(cls, value: object) -> str:
        directory_path = str(value or "").strip() or "root"
        try:
            return DirectoryPath(segments=directory_path).as_posix()
        except ValueError as exc:
            raise ValueError(
                f"Workflow graph has invalid directory path: {directory_path}"
            ) from exc

    @computed_field(return_type=list[str])
    @property
    def directory_paths(self) -> list[str]:
        return [self.directory_path]


class WorkflowEditorWorkflowPackDescriptor(BaseModel):
    reference: str
    path: str
    layout_path: str
    exists: bool
    editable: bool = True
    display_name: str
    id: str | None = None
    version: str | None = None
    description: str | None = None
    graph_count: int = 0
    node_count: int = 0
    selected: bool = False


class WorkflowEditorDocumentPayload(BaseModel):
    worker_id: str = "trigger-orchestrator"
    config_path: str
    layout_path: str
    config_exists: bool
    layout_exists: bool
    document_scope: str = "inline"
    workflow_packs: list[WorkflowEditorWorkflowPackDescriptor] = Field(default_factory=list)
    selected_workflow_pack: WorkflowEditorWorkflowPackDescriptor | None = None
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
    document_scope: str = "inline"
    workflow_packs: list[WorkflowEditorWorkflowPackDescriptor] = Field(default_factory=list)
    selected_workflow_pack: WorkflowEditorWorkflowPackDescriptor | None = None
    config: dict[str, object]
    layout: dict[str, object]
    graphs: list[WorkflowEditorGraphMetadata] = Field(default_factory=list)
    output_schemas: dict[str, dict[str, dict[str, Any]]] = Field(default_factory=dict)
    validation_diagnostics: list[dict[str, object]] = Field(default_factory=list)
    saved_at: datetime | None = None
    revision: str | None = None


class WorkflowEditorGeneratedDraftPayload(BaseModel):
    worker_id: str = "trigger-orchestrator"
    model: str
    config: dict[str, object]
    layout: dict[str, object]
    graphs: list[WorkflowEditorGraphMetadata] = Field(default_factory=list)
    output_schemas: dict[str, dict[str, dict[str, Any]]] = Field(default_factory=dict)
    validation_diagnostics: list[dict[str, object]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class WorkflowRunNodeActivityPayload(BaseModel):
    node_id: str
    status: str
    count: int = 1
    source: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    dispatch_id: str | None = None
    error: str | None = None


class WorkflowRunRecentNodeActivityPayload(BaseModel):
    activity_key: str
    event_id: str
    node_id: str
    status: str
    source: str
    activity_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    trace_id: str | None = None
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
    recent_node_activity: list[WorkflowRunRecentNodeActivityPayload] = Field(default_factory=list)


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


WORKFLOW_RUN_DETAIL_AUDIT_SCAN_LIMIT = 20_000


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
    approval_diagnostic: dict[str, Any] | None = None
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


WorkflowFailureClusterState = Literal["active", "recent", "resolved", "historical"]
WorkflowFailureClusterSourceKind = Literal["trigger", "effect", "gate"]


class WorkflowFailureClusterExamplePayload(BaseModel):
    event_id: str | None = None
    trace_id: str | None = None
    session_id: str | None = None
    occurred_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_summary: str | None = None
    run_state: str | None = None
    run_detail_href: str | None = None
    api_run_detail_href: str | None = None
    artifact_ids: list[str] = Field(default_factory=list)


class WorkflowFailureClusterPayload(BaseModel):
    cluster_id: str
    graph_id: str | None = None
    graph_name: str | None = None
    graph_description: str | None = None
    directory_path: str | None = None
    node_id: str | None = None
    node_display_name: str | None = None
    node_type: str | None = None
    trigger_type: str | None = None
    effect_type: str | None = None
    gate_type: str | None = None
    source_kind: WorkflowFailureClusterSourceKind
    error_signature: str
    error_signature_key: str
    count: int
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    latest_error_summary: str | None = None
    state: WorkflowFailureClusterState
    event_ids: list[str] = Field(default_factory=list)
    trace_ids: list[str] = Field(default_factory=list)
    session_ids: list[str] = Field(default_factory=list)
    pr_ids: list[str] = Field(default_factory=list)
    ado_item_ids: list[str] = Field(default_factory=list)
    issue_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    examples: list[WorkflowFailureClusterExamplePayload] = Field(default_factory=list)


class WorkflowFailureClusterReportPayload(BaseModel):
    worker_id: str = "trigger-orchestrator"
    generated_at: datetime
    period_start: datetime
    period_end: datetime
    recent_threshold_seconds: int | None = None
    limit: int = 100
    has_more: bool = False
    clusters: list[WorkflowFailureClusterPayload] = Field(default_factory=list)


WORKFLOW_FAILURE_CLUSTER_LIMIT_MAX = 100
WORKFLOW_FAILURE_CLUSTER_EXAMPLE_LIMIT = 3
WORKFLOW_FAILURE_CLUSTER_DEFAULT_RECENT_THRESHOLD_SECONDS = 24 * 60 * 60
WORKFLOW_FAILURE_CLUSTER_STATE_PRIORITY: dict[str, int] = {
    "active": 0,
    "recent": 1,
    "resolved": 2,
    "historical": 3,
}


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


class ReleaseNewsBlockPayload(BaseModel):
    kind: Literal["paragraph", "heading", "list", "image"]
    text: str | None = None
    level: int | None = None
    items: list[str] = Field(default_factory=list)
    src: str | None = None
    dark_src: str | None = None
    alt: str | None = None
    title: str | None = None


class ReleaseNewsPayload(BaseModel):
    source: str | None = None
    path: str | None = None
    index: int = 0
    heading: str | None = None
    blocks: list[ReleaseNewsBlockPayload] = Field(default_factory=list)
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


_module_project_root = project_helpers.module_project_root


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


_guest_script_helper_b64 = project_helpers.guest_script_helper_b64
_guest_agent_pr_note_helper_b64 = project_helpers.guest_agent_pr_note_helper_b64
_guest_push_agent_helper_b64 = project_helpers.guest_push_agent_helper_b64
_guest_sync_base_helper_b64 = project_helpers.guest_sync_base_helper_b64


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


def _workflow_agent_create_diagnostics(
    config: dict[str, object],
) -> list[WorkflowValidationDiagnostic]:
    effects = config.get("effects")
    if not isinstance(effects, list):
        return []

    diagnostics: list[WorkflowValidationDiagnostic] = []
    for effect in effects:
        if not isinstance(effect, dict) or effect.get("type") != "effect:agent:create":
            continue
        effect_config = effect.get("config")
        execution_backend_value = effect.get("execution_backend")
        if not execution_backend_value and isinstance(effect_config, dict):
            execution_backend_value = effect_config.get("execution_backend")
        execution_backend = str(execution_backend_value or "").strip()
        if execution_backend:
            continue
        diagnostics.append(
            WorkflowValidationDiagnostic(
                code="agent_create_missing_execution_backend",
                severity="error",
                node_id=str(effect.get("id") or "") or None,
                node_type="effect:agent:create",
                field_path="execution_backend",
                message="effect:agent:create requires an execution backend.",
            )
        )
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


def _looks_like_declared_output_schema(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    return all(isinstance(entry, Mapping) for entry in value.values())


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
        str(capability).strip() for capability in raw_capabilities if str(capability).strip()
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
    if purpose == "read-message":
        metadata["required_capability"] = "read"
    elif purpose == "agent-session":
        metadata["supported_capabilities"] = ["read", "write", "reply"]
        if provider == "email":
            metadata["required_capabilities"] = ["read", "write"]
    elif purpose == "send-message":
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
    metadata: dict[str, Any] | None = None,
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
        metadata=metadata or {},
    )


def _pull_request_trigger_fields(
    *, include_agent_identity_fields: bool = False
) -> list[WorkflowEditorFieldDefinition]:
    fields = [
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
        _field(
            "repo",
            "Repo",
            "string",
            description=(
                "Set a repo to enable explicit Azure DevOps PR polling. Leave blank to use "
                "legacy pr-map compatibility."
            ),
        ),
        _field("directory_path", "Directory path", "string"),
        _field("organization_url", "Organization URL", "string"),
        _field("project", "Project", "string"),
        _field("status", "Status", "string"),
        _field("creator_id", "Creator ID", "string"),
        _field("reviewer_id", "Reviewer ID", "string"),
        _field("source_ref_name", "Source ref", "string"),
        _field("target_ref_name", "Target ref", "string"),
        _field("source_repository_id", "Source repository ID", "string"),
        _field("repository_id", "Repository ID", "string"),
        _field("min_time", "Min time", "string"),
        _field("max_time", "Max time", "string"),
        _field("query_time_range_type", "Time range type", "string"),
        _field("include_links", "Include links", "boolean"),
        _field("max_comment_length", "Max comment length", "number"),
        _field(
            "tags",
            "Tags",
            "string",
            description="Tag expression with and, or, not, and parentheses.",
            placeholder="(TagA or TagB) and not TagC",
        ),
        _field("top", "Top", "number"),
        _field("skip", "Skip", "number"),
        _field("cursor", "Cursor", "string"),
    ]
    if include_agent_identity_fields:
        fields.extend(
            [
                _field(
                    "agent_author_names",
                    "Agent author names",
                    "string_list",
                    description=(
                        "Feedback author display names that should mark a PR feedback event "
                        "as agent-directed."
                    ),
                ),
                _field(
                    "agent_mention_names",
                    "Agent mention names",
                    "string_list",
                    description=(
                        "Mention display names that should mark a PR feedback event as agent-directed."
                    ),
                ),
                _field(
                    "agent_author_ids",
                    "Agent author IDs",
                    "string_list",
                    description=(
                        "Feedback author identity IDs that should mark same-thread PR feedback "
                        "as agent-directed."
                    ),
                ),
                _field(
                    "agent_mention_ids",
                    "Agent mention IDs",
                    "string_list",
                    description=(
                        "Mention identity IDs that should mark a PR feedback event as agent-directed."
                    ),
                ),
                _field(
                    "suppress_agent_authored_feedback",
                    "Suppress agent-authored feedback",
                    "boolean",
                    description=(
                        "When enabled, PR feedback authored by the configured agent identity is "
                        "stored but not published as a workflow event."
                    ),
                ),
            ]
        )
    return fields


_WORKFLOW_EDITOR_NODE_DESCRIPTIONS: dict[str, str] = {
    "trigger:cron": "Start a workflow on a recurring cron schedule.",
    "trigger:event": "Start when an internal workflow event is emitted.",
    "trigger:http:webhook": "Start from a signed HTTP webhook delivered to a public endpoint.",
    "trigger:ticket:created": "Start when a tracked ticket is created.",
    "trigger:ticket:changed": "Start when a tracked ticket changes state or metadata.",
    "trigger:issue:comment-created": "Start when a tracked issue receives a new comment.",
    "trigger:ado:work-item-matching": (
        "Start when an Azure DevOps work item first matches or advances its changed date."
    ),
    "trigger:ado:work-item-comment-created": (
        "Start when a matching Azure DevOps work item receives a new matching comment."
    ),
    "trigger:script-run-request": "Start from an approved run_script request submitted by a session.",
    "trigger:discord:new-message": (
        "Start from new Discord messages that match the configured filters."
    ),
    "trigger:communication:new-message": (
        "Start from new Teams, Discord, or email messages on declared communication targets."
    ),
    "trigger:teams:new-message": (
        "Start from new Microsoft Teams messages that match the configured targets."
    ),
    "trigger:teams:direct-message": (
        "Start from allowlisted one-on-one Microsoft Teams direct messages."
    ),
    "trigger:email:new-message": ("Start from new messages in configured email mailboxes."),
    "trigger:pull-request:new-feedback": (
        "Start when new pull-request feedback arrives from the configured provider."
    ),
    "trigger:pull-request:completed": (
        "Start when a tracked pull request reaches a terminal state."
    ),
    "trigger:ci:run:completed": (
        "Start when a CI run completes on GitHub Actions or Azure DevOps."
    ),
    "trigger:review:new-request": "Start when a review request is queued for orchestration.",
    "trigger:approval:pending-created": "Start when a pending approval request is created.",
    "trigger:git:push": "Start from Git push events captured by the host-side bare-repo hook.",
    "trigger:agent:run:completed": "Start when an agent run completion event is recorded.",
    "gate:filter": "Allow only matching events to continue downstream.",
    "gate:switch": "Route each activation to the first matching branch or a default route.",
    "gate:debounce": "Wait for a quiet period, then forward the latest activation.",
    "gate:buffer": "Collect activations by key and release them as a batch by count or timeout.",
    "gate:map": "Project upstream outputs into a new payload shape.",
    "gate:aggregate": "Aggregate numeric fields from an array into flat summary outputs.",
    "gate:deserialize": (
        "Parse a JSON or YAML string into object properties for downstream bindings."
    ),
    "gate:serialize": "Serialize a bindable value as JSON or YAML text for downstream nodes.",
    "gate:dedupe": "Suppress duplicate activations for the same key until a TTL expires.",
    "gate:merge": "Merge compatible inbound event streams into one downstream path.",
    "gate:await-agent": "Pause the workflow until a selected agent session completes.",
    "gate:for": "Iterate over an array and run the body branch for each item.",
    "gate:while": "Repeat a body branch while a bounded predicate remains true.",
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
    "effect:state:update": "Set, patch, or delete graph-local workflow state.",
    "effect:http:request": (
        "Perform a host-side raw HTTP GET, POST, or PUT request with URL allowlist control."
    ),
    "effect:runtime:diagnostics-snapshot": (
        "Capture a bounded, redacted host runtime diagnostics snapshot from allowlisted sources."
    ),
    "effect:vm:exec": "Run a shell command inside a VM-backed agent target.",
    "effect:vm:copy-files": "Copy rendered file content into a VM-backed agent target.",
    "effect:vm:delete-files": "Delete files from a VM-backed agent target.",
    "effect:issue:update-state": "Update the workflow state for a tracked issue.",
    "effect:issue:update-metadata": "Patch metadata on a tracked issue.",
    "effect:issue:upsert-ado-proxy": (
        "Create or refresh local proxy tickets from Azure DevOps work items."
    ),
    "effect:ado:pipeline-run": (
        "Queue an Azure DevOps pipeline run and wait for its terminal result."
    ),
    "effect:ado:update-work-item": (
        "Update an Azure DevOps work item with an optional expected-state guard."
    ),
    "effect:ado:add-work-item-tag": (
        "Add a tag to an Azure DevOps work item when it is not already present."
    ),
    "effect:ado:remove-work-item-tag": (
        "Remove a tag from an Azure DevOps work item when it is present."
    ),
    "effect:ado:post-work-item-comment": (
        "Post an HTML-formatted comment to an Azure DevOps work item."
    ),
    "effect:pull-request:update": (
        "Update pull request metadata or tags with an optional status guard."
    ),
    "effect:pull-request:add-tag": "Add a tag to a pull request when it is not already present.",
    "effect:pull-request:remove-tag": "Remove a tag from a pull request when it is present.",
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
    "effect:session:revoke-mcp-tool-approval-lease": (
        "Revoke a workflow-run-scoped MCP tool approval lease for one agent session."
    ),
    "effect:session:renew-if-expired": ("Renew an expired or soon-expiring session."),
    "effect:artifact:stage": "Copy a file or directory from /home/agent into the session artifacts folder.",
    "effect:artifact:publish-artifacts": (
        "Compress and publish the session artifacts folder to the durable artifact store."
    ),
    "effect:artifact:import-published": (
        "Import a published artifact archive into a session artifacts folder."
    ),
    "effect:git:promote-agent-branch": ("Promote an agent branch into a trusted host checkout."),
    "effect:git:push-branch": "Push a trusted host checkout branch to a remote branch.",
    "effect:pr:create": "Create a pull request from a host-side checkout.",
    "effect:event:schedule": "Schedule a one-shot internal workflow event for later delivery.",
    "query:vm:run-state": "Read the current run state for a VM-backed agent target.",
    "query:git:pr-note": "Read pull-request note context from a host checkout.",
    "query:pull-request:list": "List provider pull requests with optional status, branch, time, or tag filters.",
    "query:pull-request:feedback": "Fetch provider pull-request feedback without owning cursor state.",
    "query:communication:messages": (
        "Read messages from a read-capable Teams or Discord communication target."
    ),
    "query:pull-request:get": "Get a provider pull request by id without owning cursor state.",
    "query:session-context": "Read session context and metadata.",
    "query:state:get": "Read graph-local workflow state for the current persisted workflow graph.",
    "query:session:list": "List sessions by generic manifest metadata.",
    "query:model-usage:list": (
        "List persisted model usage events with optional query-time OpenAI cost estimates."
    ),
    "query:agent:artifact-text": "Read a UTF-8 text file from an agent session artifacts folder.",
    "query:issue:get": "Read a local issue record by id.",
    "query:issue:list": "List tracked local and proxy issues from the local issue store.",
    "query:ado:work-items": "Query Azure DevOps work items.",
    "query:semantic:store-status": "Read Qdrant health and semantic collection status.",
    "query:semantic:search": "Search a semantic collection with an embedded text query.",
    "query:semantic:get": "Retrieve semantic points by explicit point IDs.",
}

_VM_ARTIFACT_TEXT_READ_COMMAND = vm_agent_service._VM_ARTIFACT_TEXT_READ_COMMAND


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
        type="trigger:http:webhook",
        label="Webhook",
        fields=[
            _field(
                "slug",
                "Slug",
                "string",
                required=True,
                description="Public webhook path segment. Must match [A-Za-z0-9_.-]+.",
                placeholder="build-notify",
            ),
            _field(
                "secret_env",
                "Secret env",
                "string",
                required=True,
                description="Environment variable that stores the shared webhook HMAC secret.",
                placeholder="WEBHOOK_SECRET",
            ),
            _field(
                "event_type",
                "Event type",
                "string",
                description="Defaults to webhook.<slug> when omitted.",
                placeholder="webhook.build-notify",
            ),
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
        type="trigger:ado:work-item-comment-created",
        label="ADO work item comment",
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
                default_value=False,
                description="Resolve the team's current iteration before querying.",
            ),
            _field("team", "Team", "string"),
            _field("iteration", "Iteration path", "string"),
            _field("area_path", "Area path", "string"),
            _field("states", "States", "string_list", default_value=[]),
            _field("types", "Types", "string_list", default_value=[]),
            _field("tags_any", "Tags any", "string_list", default_value=[]),
            _field("text_search", "Text search", "string"),
            _field("top", "Top", "number", default_value=50),
            _field(
                "comment_contains",
                "Comment contains",
                "string",
                description="Only emit comments whose body contains this text.",
            ),
            _field(
                "comment_contains_any",
                "Comment contains any",
                "string_list",
                default_value=[],
                description="Only emit comments whose body contains any listed text.",
            ),
            _field(
                "comment_author_exclude_names",
                "Comment author exclude names",
                "string_list",
                default_value=[],
                description="Comment author display names to suppress.",
            ),
            _field(
                "comment_author_exclude_ids",
                "Comment author exclude IDs",
                "string_list",
                default_value=[],
                description="Comment author identity IDs to suppress.",
            ),
            _field(
                "comment_case_sensitive",
                "Comment case sensitive",
                "boolean",
                default_value=False,
                description="Apply comment text filters with case-sensitive matching.",
            ),
            _field(
                "include_existing",
                "Include existing",
                "boolean",
                default_value=False,
                description="Emit existing comments on the first poll instead of baselining them.",
            ),
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
        type="trigger:communication:new-message",
        label="Communication message",
        fields=[
            _field(
                "communication_target_ids",
                "Communication targets",
                "string_list",
                required=True,
                description=(
                    "Directory-scoped Teams channel/chat or Discord channel targets with "
                    "read capability. The trigger listens only to explicitly selected targets."
                ),
            ),
            _field(
                "directory_path",
                "Directory path",
                "string",
                placeholder="root",
                default_value="root",
            ),
            _field(
                "author_ids",
                "Author IDs",
                "string_list",
                description="Optional provider-native author/user IDs to allow.",
            ),
            _field(
                "content_contains",
                "Content contains",
                "string",
                description="Optional substring filter applied to message content.",
            ),
            _field(
                "case_sensitive",
                "Case sensitive",
                "boolean",
                default_value=False,
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="trigger",
        type="trigger:teams:direct-message",
        label="Teams direct message",
        fields=[
            _field(
                "sender_user_ids",
                "Sender user IDs or emails",
                "string_list",
                required=True,
                description=(
                    "Microsoft Graph user IDs, UPNs, or mail addresses allowed to wake this "
                    "workflow from one-on-one Teams chats."
                ),
            ),
            _field(
                "message_filter",
                "Message filter",
                "json",
                description=(
                    "Optional wake filter, for example "
                    '{"content":{"contains":"literal text","case_sensitive":false}}.'
                ),
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="trigger",
        type="trigger:email:new-message",
        label="Email message",
        fields=[
            _field("mailbox_ids", "Mailbox IDs", "string_list", required=True),
            _field("folder", "Folder", "string"),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="trigger",
        type="trigger:pull-request:new-feedback",
        label="PR feedback",
        fields=_pull_request_trigger_fields(include_agent_identity_fields=True),
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="trigger",
        type="trigger:pull-request:completed",
        label="PR completed",
        fields=_pull_request_trigger_fields(),
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="trigger",
        type="trigger:ci:run:completed",
        label="CI run completed",
        fields=[
            _field(
                "provider",
                "Provider",
                "enum",
                required=True,
                options=[
                    _field_option("github_actions", "GitHub Actions"),
                    _field_option("ado_pipelines", "Azure DevOps Pipelines"),
                ],
                description="Select the CI provider that publishes completed runs.",
            ),
            _field(
                "repo",
                "Repo",
                "string",
                description=(
                    "Repository owner/name for GitHub Actions. Optional for Azure DevOps when "
                    "the directory provider config can resolve it."
                ),
            ),
            _field(
                "workflow",
                "Workflow",
                "string",
                description="Optional GitHub workflow name, file name, or workflow id.",
            ),
            _field(
                "organization_url",
                "Organization URL",
                "string",
                description=(
                    "Optional Azure DevOps organization URL. Directory provider config can "
                    "supply it when omitted."
                ),
            ),
            _field(
                "project",
                "Project",
                "string",
                description=(
                    "Optional Azure DevOps project. Directory provider config can supply it "
                    "when omitted."
                ),
            ),
            _field(
                "pipeline_id",
                "Pipeline ID",
                "number",
                description="Optional Azure DevOps pipeline definition id.",
            ),
            _field(
                "pipeline_name",
                "Pipeline name",
                "string",
                description="Optional Azure DevOps pipeline definition name.",
            ),
            _field(
                "branches",
                "Branches",
                "string_list",
                description="Branch names to include. Leave empty to match any branch.",
            ),
            _field(
                "conclusions",
                "Conclusions",
                "string_list",
                description=(
                    "Terminal outcomes to include. GitHub and Azure DevOps spellings are "
                    "normalized to success, failure, cancelled, timed_out, and skipped."
                ),
            ),
            _field(
                "top",
                "Top",
                "number",
                description="Maximum number of completed runs to fetch per poll.",
            ),
            _field(
                "include_existing",
                "Include existing",
                "boolean",
                description="Emit historical runs on the first poll instead of suppressing them.",
            ),
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
        type="trigger:approval:pending-created",
        label="Pending approval",
        fields=[
            _field(
                "directory_path",
                "Directory path",
                "string",
                placeholder="root",
                default_value="root",
            ),
            _field(
                "action_types",
                "Action types",
                "string_list",
                default_value=[],
                description=(
                    "Optional action type filter. Leave blank to include all pending approvals."
                ),
            ),
            _field(
                "tool_names",
                "Tool names",
                "string_list",
                default_value=[],
                description=(
                    "Optional tool name filter. Leave blank to include all pending approvals."
                ),
            ),
        ],
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
        type="gate:switch",
        label="Switch",
        fields=[
            _field(
                "cases",
                "Cases",
                "object_list",
                required=True,
                description=(
                    "Ordered routes. The first matching case wins; duplicate ports are allowed."
                ),
                item_fields=[
                    _field("port", "Port", "string", required=True),
                    _field("label", "Label", "string"),
                    _field(
                        "when",
                        "When",
                        "json",
                        bindable=True,
                        description='Structured predicate using bindings, for example {"all":[{"left":"${{effect-1.success}}","equals":true}]} or {"left":"${{query-1.count}}","greater_than":8}. Mutually exclusive with Match.',
                    ),
                    _field(
                        "match",
                        "Match",
                        "json",
                        bindable=True,
                        description="Recursive subset match against the immediate incoming payload. Mutually exclusive with When.",
                    ),
                ],
            ),
            _field(
                "default_port",
                "Default port",
                "string",
                description="Fallback route when no case matches.",
            ),
            _field(
                "outputs",
                "Outputs",
                "json",
                read_only=True,
                description="Derived from the single upstream node and passed through unchanged on the selected route.",
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
        type="gate:buffer",
        label="Buffer",
        fields=[
            _field(
                "key",
                "Key",
                "string",
                required=True,
                bindable=True,
                description="Groups independent buffers. Supports workflow bindings.",
                placeholder="buffer:${{system.execution.trace_id}}",
            ),
            _field(
                "count",
                "Count",
                "number",
                required=True,
                bindable=True,
                description="Release when this many activations have accumulated.",
                default_value=10,
                placeholder="10",
            ),
            _field(
                "timeout",
                "Timeout",
                "string",
                bindable=True,
                description=(
                    "Optional ISO-8601 duration from the first buffered activation, "
                    "for example PT10M. Omit for count-only buffering."
                ),
                default_value="PT10M",
                placeholder="PT10M",
            ),
            _field(
                "outputs",
                "Outputs",
                "json",
                read_only=True,
                default_value={
                    "items": {"type": "array"},
                    "count": {"type": "integer"},
                    "release_reason": {"type": "string"},
                },
                description="Emits buffered items, released count, and release reason.",
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
        type="gate:aggregate",
        label="Aggregate",
        fields=[
            _field(
                "items",
                "Items",
                "json",
                required=True,
                bindable=True,
                description="Array to aggregate. Use an exact binding to pass through an upstream array.",
                json_value_type="array",
                default_value=[],
                placeholder="${{query-model-usage.items}}",
            ),
            _field(
                "metrics",
                "Metrics",
                "json",
                required=True,
                description=(
                    "Object keyed by output name. Each value defines operation "
                    "sum, min, max, or avg and a dotted item path."
                ),
                json_value_type="object",
                default_value={"total": {"operation": "sum", "path": "value"}},
                placeholder='{"total_tokens":{"operation":"sum","path":"total_tokens"}}',
            ),
            _field(
                "outputs",
                "Outputs",
                "json",
                read_only=True,
                description="Emits count plus one flat output for each configured metric.",
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
        kind="gate",
        type="gate:while",
        label="While",
        fields=[
            _field(
                "when",
                "When",
                "json",
                required=True,
                bindable=True,
                description='Structured predicate using bindings, for example {"left":"${{parse-tests.failure_count}}","greater_than":0}.',
            ),
            _field(
                "max_iterations",
                "Max iterations",
                "number",
                required=True,
                bindable=True,
                description="Positive integer cap for body iterations before routing to Exhausted.",
                default_value=3,
            ),
            _field(
                "outputs",
                "Outputs",
                "json",
                read_only=True,
                description="Derived from while-loop runtime outputs.",
            ),
        ],
        output_ports=[
            _port("body", "Body", description="Loop body path while the predicate is true."),
            _port("done", "Done", description="Exit path when the predicate is false."),
            _port(
                "exhausted",
                "Exhausted",
                description="Exit path when Max iterations is reached while the predicate is still true.",
            ),
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
            _field("metadata", "Metadata", "json", bindable=True),
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
                "execution_backend",
                "Execution backend",
                "enum",
                required=True,
                description="Runtime backend that will host the created agent session.",
                options=[
                    _field_option("libvirt_vm", "Libvirt VM"),
                    _field_option("container_worker_vm", "Container worker VM"),
                ],
            ),
            _field(
                "agent_provider",
                "Agent provider",
                "enum",
                bindable=True,
                description=(
                    "Optional agent harness override. Leave blank to use the directory default."
                ),
                options=[
                    _field_option("codex", "Codex"),
                    _field_option("claude", "Claude"),
                    _field_option(
                        "copilot",
                        "Copilot",
                        metadata={"execution_backends": ["libvirt_vm"]},
                    ),
                ],
            ),
            _field(
                "provider",
                "VM provider",
                "string",
                bindable=True,
                description=(
                    "Optional libvirt VM infrastructure provider override. "
                    "Leave blank to use the directory VM default."
                ),
                metadata={"execution_backends": ["libvirt_vm"]},
            ),
            _field(
                "vm_name",
                "VM name",
                "string",
                bindable=True,
                description="Optional VM name for libvirt-backed agents. Container-backed agents usually leave this blank.",
                metadata={"execution_backends": ["libvirt_vm"]},
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
                "model_provider_mode",
                "Model relay",
                "enum",
                bindable=True,
                description=(
                    "Optional OpenAI-compatible relay mode override. Leave blank to use "
                    "the directory provider default; choose Claude as the agent provider "
                    "for Anthropic routing."
                ),
                options=[
                    _field_option("openai_public", "Public OpenAI"),
                    _field_option("local_openai", "Local OpenAI"),
                    _field_option("azure_openai", "Azure OpenAI"),
                ],
            ),
            _field(
                "npm_registry_url",
                "NPM registry URL",
                "string",
                bindable=True,
                description="Optional npm registry override for container-backed agent sessions.",
                metadata={"execution_backends": ["container_worker_vm"]},
            ),
            _field(
                "vm_user",
                "VM user",
                "string",
                bindable=True,
                description="Guest user for libvirt-backed sessions. Defaults to agent.",
                metadata={"execution_backends": ["libvirt_vm"]},
            ),
            _field(
                "vm_image",
                "VM image",
                "string",
                bindable=True,
                description=(
                    "Optional approved libvirt VM template alias from the directory vm.json "
                    "approved VM template/base-image catalog. This is the libvirt-only "
                    "selector and applies only to execution_backend=libvirt_vm; use "
                    "container_image for container-worker agents."
                ),
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
                "communication_target_ids",
                "Communication targets",
                "string_list",
                description=(
                    "Known Teams, Discord, or mailbox-backed email communication targets "
                    "to allowlist for the created session. Select from the directory "
                    "communication target catalogue."
                ),
                metadata={"selection_only": True},
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
                "web_search_enabled",
                "Web search",
                "boolean",
                bindable=True,
                description="Allow the created session to use OpenAI-hosted web_search tools through the OpenAI relay.",
            ),
            _field(
                "dotnet",
                "Dotnet",
                "boolean",
                bindable=True,
                description="Enable .NET toolchain setup for the created agent when supported by the selected backend.",
            ),
            _field(
                "nuget",
                "NuGet",
                "boolean",
                bindable=True,
                description="Enable NuGet relay and package-cache setup for the created agent.",
            ),
            _field(
                "mssql",
                "MSSQL",
                "boolean",
                bindable=True,
                description="Bootstrap SQL Server tooling for libvirt-backed agents.",
                metadata={"execution_backends": ["libvirt_vm"]},
            ),
            _field(
                "mssql_init_root",
                "MSSQL init root",
                "string",
                bindable=True,
                description="Optional host init-script root for MSSQL provisioning.",
                metadata={"execution_backends": ["libvirt_vm"]},
            ),
            _field(
                "playwright",
                "Playwright",
                "boolean",
                bindable=True,
                description="Enable Playwright browser automation setup for the created agent.",
            ),
            _field(
                "codex_skills",
                "Codex skills",
                "string_list",
                description="Managed Codex skills to install into the created session.",
            ),
            _field(
                "container_image",
                "Container image",
                "string",
                bindable=True,
                description="Optional approved container image alias or the configured default image.",
                metadata={"execution_backends": ["container_worker_vm"]},
            ),
            _field(
                "directory_path",
                "Directory path",
                "string",
                bindable=True,
                description="Directory scope that owns the created session and resolves inherited configuration.",
            ),
            _field(
                "metadata",
                "Metadata",
                "json",
                bindable=True,
                description="Session metadata patch to merge into the created agent session.",
                json_value_type="object",
                default_value={},
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
            _field("canonical_origin_url", "Canonical origin URL", "string", bindable=True),
            _field("bare_repo_name", "Bare repo name", "string", bindable=True),
            _field("bare_repo_path", "Bare repo path", "string", bindable=True),
            _field("clone_branch", "Clone branch", "string", bindable=True),
            _field("vm_user", "VM user", "string", bindable=True),
            _field("directory_path", "Directory path", "string", bindable=True),
            _field(
                "git_author_name",
                "Git author name",
                "string",
                bindable=True,
                description=(
                    "Optional author.name for cloned repositories. "
                    "Set together with git_author_email."
                ),
            ),
            _field(
                "git_author_email",
                "Git author email",
                "string",
                bindable=True,
                description=(
                    "Optional author.email for cloned repositories. "
                    "Set together with git_author_name."
                ),
            ),
            _field(
                "git_committer_name",
                "Git committer name",
                "string",
                bindable=True,
                description=(
                    "Optional committer.name for cloned repositories. "
                    "Set together with git_committer_email."
                ),
            ),
            _field(
                "git_committer_email",
                "Git committer email",
                "string",
                bindable=True,
                description=(
                    "Optional committer.email for cloned repositories. "
                    "Set together with git_committer_name."
                ),
            ),
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
                    _field(
                        "merge_strategy",
                        "Merge strategy",
                        "enum",
                        bindable=True,
                        description=(
                            "Optional write strategy. managed_section replaces only the marked "
                            "section identified by Section ID and preserves the rest of the file."
                        ),
                        options=[_field_option("managed_section", "Managed section")],
                    ),
                    _field(
                        "section_id",
                        "Section ID",
                        "string",
                        bindable=True,
                        description=(
                            "Stable identifier for managed_section writes. Required when Merge "
                            "strategy is managed_section."
                        ),
                    ),
                    _field(
                        "remove_section_ids",
                        "Remove section IDs",
                        "string_list",
                        bindable=True,
                        description=(
                            "Optional managed section IDs to remove from the destination before "
                            "writing Section ID."
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
            _field(
                "source_paths",
                "Source paths",
                "string_list",
                bindable=True,
                description=(
                    "Optional relative paths under /artifacts to publish. "
                    "Leave empty to publish the full artifacts folder."
                ),
            ),
            _field("vm_user", "VM user", "string", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:artifact:import-published",
        label="Import published artifact",
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
                description="Immutable key of the published tar.gz archive to import.",
                placeholder="manuals/true-app/reviewed/latest",
            ),
            _field(
                "destination_path",
                "Destination path",
                "string",
                bindable=True,
                description=(
                    "Optional relative prefix under /artifacts. Defaults to the archive paths."
                ),
                placeholder="input",
            ),
            _field(
                "overwrite",
                "Overwrite",
                "boolean",
                bindable=True,
                description="When enabled, replace existing artifact files during import.",
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
            _port(
                AGENT_PROMPT_ERROR_PORT,
                "Error",
                description=(
                    "Emits when prompt dispatch fails and the node is handled by an error route."
                ),
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
            _field("dispatch_id", "Dispatch ID", "string", bindable=True),
            _field("resume_thread_id", "Resume thread ID", "string", bindable=True),
            _field(
                "resume_mode",
                "Resume mode",
                "string",
                bindable=True,
                placeholder="same_context",
            ),
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
            _port(
                AGENT_PROMPT_ERROR_PORT,
                "Error",
                description=(
                    "Emits when prompt dispatch fails and the node is handled by an error route."
                ),
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
            _field("command", "Command", "string", required=True, bindable=True, multiline=True),
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
        type="effect:state:update",
        label="State update",
        fields=[
            _field(
                "namespace",
                "Namespace",
                "string",
                required=True,
                bindable=True,
                description="Workflow state namespace. Use one namespace per logical state family.",
                placeholder="workflow",
            ),
            _field(
                "key",
                "Key",
                "string",
                required=True,
                bindable=True,
                description="Workflow state key within the selected namespace.",
                placeholder="cursor",
            ),
            _field(
                "operation",
                "Operation",
                "enum",
                required=True,
                bindable=True,
                description="Choose whether to set, patch, or delete the state record.",
                options=[
                    _field_option("set", "Set"),
                    _field_option("patch", "Patch"),
                    _field_option("delete", "Delete"),
                ],
            ),
            _field(
                "value",
                "Value",
                "json",
                bindable=True,
                description="JSON value to store when operation is set.",
                json_value_type="object",
                default_value={},
            ),
            _field(
                "patch",
                "Patch",
                "json",
                bindable=True,
                description="JSON Merge Patch object to apply when operation is patch.",
                json_value_type="object",
                default_value={},
            ),
            _field(
                "expected_version",
                "Expected version",
                "number",
                bindable=True,
                description="Optional optimistic concurrency guard. Missing records compare as version 0.",
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:http:request",
        label="HTTP request",
        fields=[
            _field(
                "method",
                "Method",
                "enum",
                bindable=True,
                default_value="GET",
                options=[
                    _field_option("GET", "GET"),
                    _field_option("POST", "POST"),
                    _field_option("PUT", "PUT"),
                ],
            ),
            _field(
                "url",
                "URL",
                "string",
                required=True,
                bindable=True,
                placeholder="https://api.example.test/items",
            ),
            _field(
                "allowed_url_patterns",
                "Allowed URL patterns",
                "string_list",
                bindable=True,
                default_value=["*"],
                placeholder="https://api.example.test/*",
                description=(
                    "Shell-style URL or host patterns allowed for this request. "
                    "Use '*' to allow any HTTP or HTTPS target."
                ),
            ),
            _field(
                "payload",
                "Payload",
                "json",
                bindable=True,
                json_value_type="object",
                description="Optional JSON request body sent for POST or PUT requests.",
            ),
            _field(
                "headers",
                "Headers",
                "json",
                bindable=True,
                json_value_type="object",
                description="Optional request headers as a JSON object.",
            ),
            _field("timeout_seconds", "Timeout seconds", "number", bindable=True),
            _field(
                "check",
                "Check status",
                "boolean",
                bindable=True,
                default_value=False,
                description=(
                    "When enabled, non-2xx responses raise after response details are captured."
                ),
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:runtime:diagnostics-snapshot",
        label="Runtime diagnostics snapshot",
        fields=[
            _field(
                "since",
                "Since",
                "string",
                bindable=True,
                default_value="24 hours ago",
                placeholder="24 hours ago",
                description="journalctl-compatible time window for service warnings.",
            ),
            _field(
                "units",
                "Units",
                "string_list",
                bindable=True,
                description="Optional subset of the built-in agent-sandbox service allowlist.",
            ),
            _field(
                "include_failed_units",
                "Include failed units",
                "boolean",
                bindable=True,
                default_value=True,
            ),
            _field(
                "include_journal",
                "Include journal warnings",
                "boolean",
                bindable=True,
                default_value=True,
            ),
            _field(
                "include_runtime_files",
                "Include runtime failure files",
                "boolean",
                bindable=True,
                default_value=True,
            ),
            _field(
                "max_journal_lines_per_unit",
                "Max journal lines per unit",
                "number",
                bindable=True,
                default_value=200,
            ),
            _field(
                "max_runtime_file_bytes",
                "Max runtime file bytes",
                "number",
                bindable=True,
                default_value=120000,
            ),
            _field(
                "command_timeout_seconds",
                "Command timeout seconds",
                "number",
                bindable=True,
                default_value=10,
            ),
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
                    _field(
                        "merge_strategy",
                        "Merge strategy",
                        "enum",
                        bindable=True,
                        options=[_field_option("managed_section", "Managed section")],
                    ),
                    _field("section_id", "Section ID", "string", bindable=True),
                    _field(
                        "remove_section_ids",
                        "Remove section IDs",
                        "string_list",
                        bindable=True,
                    ),
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
        type="effect:ado:add-work-item-tag",
        label="ADO add work item tag",
        fields=[
            _field("directory_path", "Directory path", "string", required=True, bindable=True),
            _field("project", "Project", "string", required=True, bindable=True),
            _field("work_item_id", "Work item ID", "string", required=True, bindable=True),
            _field("tag", "Tag", "string", required=True, bindable=True),
            _field("sync_proxy_cache", "Sync proxy cache", "boolean", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:ado:remove-work-item-tag",
        label="ADO remove work item tag",
        fields=[
            _field("directory_path", "Directory path", "string", required=True, bindable=True),
            _field("project", "Project", "string", required=True, bindable=True),
            _field("work_item_id", "Work item ID", "string", required=True, bindable=True),
            _field("tag", "Tag", "string", required=True, bindable=True),
            _field("sync_proxy_cache", "Sync proxy cache", "boolean", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:ado:post-work-item-comment",
        label="ADO post work item comment",
        fields=[
            _field("directory_path", "Directory path", "string", required=True, bindable=True),
            _field("project", "Project", "string", required=True, bindable=True),
            _field("work_item_id", "Work item ID", "string", required=True, bindable=True),
            _field("comment", "Comment", "string", required=True, bindable=True, multiline=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:ado:pipeline-run",
        label="ADO pipeline run",
        fields=[
            _field("directory_path", "Directory path", "string", required=True, bindable=True),
            _field("pipeline_id", "Pipeline ID", "number", required=True, bindable=True),
            _field("project", "Project", "string", bindable=True),
            _field("branch", "Branch", "string", bindable=True),
            _field(
                "variables",
                "Variables",
                "json",
                bindable=True,
                json_value_type="object",
                default_value={},
            ),
            _field(
                "template_parameters",
                "Template parameters",
                "json",
                bindable=True,
                json_value_type="object",
                default_value={},
            ),
            _field("timeout_seconds", "Timeout seconds", "number", bindable=True),
            _field("poll_interval_seconds", "Poll interval seconds", "number", bindable=True),
            _field("check", "Check result", "boolean", bindable=True),
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
        type="effect:event:schedule",
        label="Schedule internal event",
        fields=[
            _field("event_type", "Event type", "string", required=True, bindable=True),
            _field("correlation_key", "Correlation key", "string", bindable=True),
            _field(
                "payload",
                "Payload",
                "json",
                required=True,
                bindable=True,
                json_value_type="object",
            ),
            _field("run_at", "Run at", "string", bindable=True),
            _field("delay_seconds", "Delay seconds", "number", bindable=True),
            _field("idempotency_key", "Idempotency key", "string", bindable=True),
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
                description=("MCP tools to approve only for this workflow run and session."),
                options=_tool_field_options(),
            ),
            _field(
                "approval_notification_target_ids",
                "Approval notification target IDs",
                "string_list",
                bindable=True,
                description=(
                    "Optional host-side communication target IDs that should receive "
                    "approval notifications for this run-scoped lease."
                ),
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
        type="effect:session:revoke-mcp-tool-approval-lease",
        label="Revoke MCP tool approval lease",
        fields=[
            _field(
                "mcp_tool_approval_lease_id",
                "MCP tool approval lease ID",
                "string",
                required=True,
                bindable=True,
                description=(
                    "Preferred lease ID from Approve MCP tools for run. Revokes that lease."
                ),
            ),
            _field(
                "lease_id",
                "Lease ID",
                "string",
                bindable=True,
                description="Alias for MCP tool approval lease ID.",
            ),
            _field(
                "session_id",
                "Session ID",
                "string",
                bindable=True,
                description="Optional guard that must match the stored lease.",
            ),
            _field(
                "workflow_run_id",
                "Workflow run ID",
                "string",
                bindable=True,
                description="Optional guard that must match the stored lease.",
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
                description="Duration from now for the renewed session, such as 8h, 2d, or PT8H.",
            ),
            _field(
                "renew_if_expires_within",
                "Renew if expires within",
                "string",
                bindable=True,
                description=(
                    "Optional pre-expiry window. Renew active sessions that expire within "
                    "this duration, such as 30m, 4h, or PT30M."
                ),
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
            _field("target_branch", "Target branch", "string", bindable=True),
            _field("expected_commit_sha", "Expected commit SHA", "string", bindable=True),
            _field("remote_name", "Remote name", "string", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:git:push-branch",
        label="Push git branch",
        fields=[
            _field("host_repo_path", "Host repo path", "string", required=True, bindable=True),
            _field("local_branch", "Local branch", "string", required=True, bindable=True),
            _field("origin_branch", "Origin branch", "string", required=True, bindable=True),
            _field("canonical_origin_url", "Canonical origin URL", "string", bindable=True),
            _field("provider", "Provider", "string", bindable=True),
            _field("base_branch", "Base branch", "string", bindable=True),
            _field("session_id", "Session ID", "string", bindable=True),
            _field("work_item_id", "Work item ID", "string", bindable=True),
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
            _field("fallback_title", "Fallback title", "string", bindable=True),
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
        kind="effect",
        type="effect:pull-request:add-tag",
        label="Pull request add tag",
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
            _field("tag", "Tag", "string", required=True, bindable=True),
            _field("directory_path", "Directory path", "string", bindable=True),
            _field("organization_url", "Organization URL", "string", bindable=True),
            _field("project", "Project", "string", bindable=True),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type="effect:pull-request:remove-tag",
        label="Pull request remove tag",
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
            _field("tag", "Tag", "string", required=True, bindable=True),
            _field("directory_path", "Directory path", "string", bindable=True),
            _field("organization_url", "Organization URL", "string", bindable=True),
            _field("project", "Project", "string", bindable=True),
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
        type="query:state:get",
        label="State get",
        fields=[
            _field(
                "namespace",
                "Namespace",
                "string",
                required=True,
                bindable=True,
                description="Workflow state namespace to read from.",
                placeholder="workflow",
            ),
            _field(
                "key",
                "Key",
                "string",
                required=True,
                bindable=True,
                description="Workflow state key to read within the namespace.",
                placeholder="cursor",
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="query",
        type="query:session:list",
        label="Session list",
        fields=[
            _field(
                "directory_path",
                "Directory path",
                "string",
                bindable=True,
                description=(
                    "Optional directory path such as root or root/team. When set, matches "
                    "sessions in that directory and its descendants."
                ),
            ),
            _field(
                "metadata_equals",
                "Metadata equals",
                "json",
                bindable=True,
                description=(
                    "Value: JSON object whose keys are metadata names or dot-path selectors "
                    "and whose values must match exactly."
                ),
            ),
            _field(
                "metadata_exists",
                "Metadata exists",
                "json",
                bindable=True,
                description=(
                    "Value: metadata selector string, selector array, or JSON object. Object "
                    "values of true require presence; false requires absence."
                ),
            ),
            _field(
                "metadata_prefix",
                "Metadata prefix",
                "json",
                bindable=True,
                description=(
                    "Value: JSON object whose keys are metadata names or dot-path selectors "
                    "and whose values are required string prefixes. Arrays match when any "
                    "item starts with the prefix."
                ),
            ),
            _field(
                "state",
                "State",
                "enum",
                bindable=True,
                options=[
                    _field_option("active", "Active"),
                    _field_option("expired", "Expired"),
                    _field_option("inactive", "Inactive"),
                    _field_option("all", "All"),
                    _field_option("any", "Any"),
                ],
                description=(
                    "Value: active, expired, inactive, all, or any. Empty behaves like all."
                ),
            ),
            _field(
                "active_only",
                "Active only",
                "boolean",
                bindable=True,
                description=(
                    "Value: boolean. Set true to return only sessions whose expires_at is "
                    "still in the future."
                ),
            ),
            _field(
                "limit",
                "Limit",
                "number",
                bindable=True,
                description="Value: positive integer maximum number of matched sessions to return.",
            ),
        ],
    ),
    WorkflowEditorNodeCatalogEntry(
        kind="query",
        type="query:model-usage:list",
        label="Model usage list",
        fields=[
            _field(
                "session_id",
                "Session ID",
                "string",
                bindable=True,
                description=(
                    "Optional session filter. Leave blank to include usage from every session "
                    "that matches the other filters."
                ),
            ),
            _field(
                "workflow_run_id",
                "Workflow run ID",
                "string",
                bindable=True,
                description=(
                    "Optional workflow run filter. To scope the query to the current run, bind "
                    "this to ${{system.execution.trace_id}}. Leave blank to include every run "
                    "that matches the other filters."
                ),
            ),
            _field(
                "effect_id",
                "Effect ID",
                "string",
                bindable=True,
                description=(
                    "Optional effect node ID filter, for example effect-agent-prompt-1. Leave "
                    "blank to include usage from all effects in the selected run, session, or "
                    "directory scope."
                ),
            ),
            _field(
                "dispatch_id",
                "Dispatch ID",
                "string",
                bindable=True,
                description=(
                    "Optional prompt dispatch ID filter. Use this only when narrowing to one "
                    "agent prompt dispatch; leave blank for all dispatches in scope."
                ),
            ),
            _field(
                "directory_path",
                "Directory path",
                "string",
                bindable=True,
                description=(
                    "Optional directory filter such as root or root/team. Leave blank to include "
                    "all directories. When set, matches only that directory unless matching "
                    "descendants is enabled."
                ),
            ),
            _field(
                "include_directory_descendants",
                "Include directory descendants",
                "boolean",
                bindable=True,
                default_value=False,
                description=(
                    "Only applies when Directory path is set. When enabled, a filter such as "
                    "root/team also includes usage from root/team/subteam."
                ),
            ),
            _field(
                "provider",
                "Provider",
                "string",
                bindable=True,
                description=(
                    "Optional provider filter. The built-in relay currently records OpenAI "
                    "usage as openai; leave blank for all providers."
                ),
                options=[_field_option("openai", "openai")],
            ),
            _field(
                "model",
                "Model",
                "string",
                bindable=True,
                description=(
                    "Optional exact model filter using the provider response or correlated "
                    "session model value. Leave blank for all models."
                ),
            ),
            _field(
                "agent_harness",
                "Agent harness",
                "string",
                bindable=True,
                description=(
                    "Optional harness filter. Prompt effects currently record Codex requests as "
                    "codex; leave blank for every harness."
                ),
                options=[_field_option("codex", "codex")],
            ),
            _field(
                "execution_backend",
                "Execution backend",
                "string",
                bindable=True,
                description=(
                    "Optional execution backend filter. Use libvirt_vm for VM-backed agents or "
                    "container_worker_vm for container worker sessions; leave blank for both."
                ),
                options=[
                    _field_option("libvirt_vm", "libvirt_vm"),
                    _field_option("container_worker_vm", "container_worker_vm"),
                ],
            ),
            _field(
                "normalized_usage_status",
                "Normalized usage status",
                "enum",
                bindable=True,
                description=(
                    "Optional usage parsing filter. available means token usage was parsed from "
                    "the provider response; usage_unavailable means the request was recorded "
                    "without token usage."
                ),
                options=[
                    _field_option("available", "available"),
                    _field_option("usage_unavailable", "usage_unavailable"),
                ],
            ),
            _field(
                "request_status",
                "Request status",
                "enum",
                bindable=True,
                description=(
                    "Optional request outcome filter recorded by the relay. Leave blank to "
                    "include every request status."
                ),
                options=[
                    _field_option("success", "success"),
                    _field_option("failed", "failed"),
                    _field_option("error", "error"),
                    _field_option("cancelled", "cancelled"),
                ],
            ),
            _field(
                "occurred_after",
                "Occurred after",
                "string",
                bindable=True,
                description=(
                    "Optional lower bound for occurred_at, expressed as an ISO-8601 timestamp. "
                    "Leave blank for no start time filter."
                ),
                placeholder="2026-05-24T12:00:00Z",
            ),
            _field(
                "occurred_before",
                "Occurred before",
                "string",
                bindable=True,
                description=(
                    "Optional upper bound for occurred_at, expressed as an ISO-8601 timestamp. "
                    "Leave blank for no end time filter."
                ),
                placeholder="2026-05-24T13:00:00Z",
            ),
            _field(
                "limit",
                "Limit",
                "number",
                bindable=True,
                description=(
                    "Optional maximum number of events to return. Must be a positive integer; "
                    "results are ordered from oldest to newest."
                ),
            ),
            _field(
                "include_costs",
                "Include costs",
                "boolean",
                bindable=True,
                default_value=True,
                description=(
                    "Value: boolean. When enabled, adds query-time OpenAI cost estimates using "
                    "the configured local pricing catalog."
                ),
            ),
            _field(
                "cost_currency",
                "Cost currency",
                "enum",
                bindable=True,
                default_value="usd",
                description=(
                    "Currency for cost estimates. The built-in pricing catalog supports usd."
                ),
                options=[_field_option("usd", "usd")],
            ),
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
        type="query:issue:list",
        label="Issue list",
        fields=[
            _field("directory_path", "Directory path", "string", bindable=True),
            _field(
                "direct_only",
                "Direct only",
                "boolean",
                bindable=True,
            ),
            _field("states", "States", "string_list", bindable=True),
            _field("exclude_states", "Exclude states", "string_list", bindable=True),
            _field("source", "Source", "string", bindable=True),
            _field("source_type", "Source type", "string", bindable=True),
            _field("tags_any", "Tags any", "string_list", bindable=True),
            _field("tags_all", "Tags all", "string_list", bindable=True),
            _field("metadata_equals", "Metadata equals", "json", bindable=True),
            _field("metadata_exists", "Metadata exists", "json", bindable=True),
            _field("metadata_prefix", "Metadata prefix", "json", bindable=True),
            _field("limit", "Limit", "number", bindable=True),
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
        type="query:pull-request:get",
        label="Pull request get",
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
            _field("include_reviewers", "Include reviewers", "boolean", bindable=True),
            _field("include_commits", "Include commits", "boolean", bindable=True),
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
        type="query:communication:messages",
        label="Communication messages",
        fields=[
            _field(
                "communication_target_id",
                "Communication target",
                "enum",
                required=True,
                description=("Select a read-capable Teams or Discord communication target."),
            ),
            _field(
                "limit",
                "Limit",
                "number",
                bindable=True,
                description="Default 50. Maximum 200.",
                default_value=50,
            ),
            _field("cursor", "Cursor", "string", bindable=True),
            _field("before_cursor", "Before cursor", "string", bindable=True),
            _field(
                "thread_id",
                "Thread ID",
                "string",
                bindable=True,
                description=(
                    "Filters Teams channel-thread replies; Discord targets reject thread_id."
                ),
            ),
            _field(
                "directory_path",
                "Directory path",
                "string",
                bindable=True,
                description="Directory path used to resolve the communication target.",
            ),
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


_utc_now = session_helpers.utc_now


_coerce_optional_string = vm_agent_service._coerce_optional_string


_coerce_optional_datetime = vm_agent_service._coerce_optional_datetime


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


_WORKFLOW_FAILURE_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_WORKFLOW_FAILURE_ISO_TIMESTAMP_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?\b"
)
_WORKFLOW_FAILURE_LONG_HEX_RE = re.compile(r"\b[0-9a-fA-F]{12,}\b")
_WORKFLOW_FAILURE_URL_RE = re.compile(r"(?P<url>(?:https?|ssh|git)://[^\s'\"`<>]+)")
_WORKFLOW_FAILURE_TEMP_PATH_RE = re.compile(
    r"(?P<path>(?:/tmp|/var/tmp|/private/tmp|/run/user/\d+)/[^\s'\"`<>]+)"
)
_WORKFLOW_FAILURE_AGENT_REF_RE = re.compile(r"\b(?:refs/(?:heads|remotes)/)?agents/[^\s'\"`<>]+")
_WORKFLOW_FAILURE_LABELLED_ID_RE = re.compile(
    r"(?i)\b(?P<label>trace|session|thread|execution|event|request|correlation|"
    r"approval_request|dispatch|artifact|pr|issue|ado_item)(?:_id)?\b(?P<sep>\s*[:=]\s*)"
    r"(?P<value>[^\s,;)\]}]+)"
)
_WORKFLOW_FAILURE_WS_RE = re.compile(r"\s+")
_WORKFLOW_FAILURE_ID_PLACEHOLDERS: dict[str, str] = {
    "trace": "<trace_id>",
    "session": "<session_id>",
    "thread": "<thread_id>",
    "execution": "<execution_id>",
    "event": "<event_id>",
    "request": "<request_id>",
    "correlation": "<correlation_id>",
    "approval_request": "<approval_request_id>",
    "dispatch": "<dispatch_id>",
    "artifact": "<artifact_id>",
    "pr": "<pr_id>",
    "issue": "<issue_id>",
    "ado_item": "<ado_item_id>",
}


def _workflow_failure_replace_url(match: re.Match[str]) -> str:
    url = match.group("url")
    try:
        split = urlsplit(url)
    except ValueError:
        return "<url>"
    return urlunsplit((split.scheme, split.netloc, split.path, "", ""))


def _workflow_failure_replace_labelled_id(match: re.Match[str]) -> str:
    label = match.group("label").lower()
    placeholder = _WORKFLOW_FAILURE_ID_PLACEHOLDERS.get(label, f"<{label}_id>")
    return f"{label}_id={placeholder}"


def _workflow_failure_fallback_text(outputs: Mapping[str, object]) -> str:
    error_class = _coerce_optional_string(outputs.get("error_class"))
    source_kind = (
        _coerce_optional_string(outputs.get("source_kind"))
        or _coerce_optional_string(outputs.get("node_type"))
        or _coerce_optional_string(outputs.get("effect_type"))
        or _coerce_optional_string(outputs.get("gate_type"))
        or _coerce_optional_string(outputs.get("trigger_type"))
    )
    if error_class and source_kind:
        return f"{error_class} {source_kind}"
    if error_class:
        return error_class
    if source_kind:
        return source_kind
    return "workflow failure"


def _normalize_workflow_failure_signature(
    error_text: object | None, outputs: object | None = None
) -> tuple[str, str]:
    output_payload = outputs if isinstance(outputs, Mapping) else {}
    summary = _coerce_optional_string(error_text)
    if summary is None:
        summary = _coerce_optional_string(output_payload.get("error_message"))
    if summary is None:
        summary = _coerce_optional_string(output_payload.get("message"))
    if summary is None:
        summary = _workflow_failure_fallback_text(output_payload)

    normalized = summary
    normalized = _WORKFLOW_FAILURE_URL_RE.sub(_workflow_failure_replace_url, normalized)
    normalized = _WORKFLOW_FAILURE_TEMP_PATH_RE.sub("<temp_path>", normalized)
    normalized = _WORKFLOW_FAILURE_AGENT_REF_RE.sub("<agent_ref>", normalized)
    normalized = _WORKFLOW_FAILURE_LABELLED_ID_RE.sub(
        _workflow_failure_replace_labelled_id, normalized
    )
    normalized = _WORKFLOW_FAILURE_UUID_RE.sub("<uuid>", normalized)
    normalized = _WORKFLOW_FAILURE_ISO_TIMESTAMP_RE.sub("<timestamp>", normalized)
    normalized = _WORKFLOW_FAILURE_LONG_HEX_RE.sub("<hex>", normalized)
    normalized = _WORKFLOW_FAILURE_WS_RE.sub(" ", normalized).strip()
    if not normalized:
        normalized = _workflow_failure_fallback_text(output_payload)
    key = _WORKFLOW_FAILURE_WS_RE.sub(" ", normalized).strip().casefold()
    return normalized, key


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


_resolve_path_setting = process_helpers.resolve_path_setting


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
    return issue_helpers.normalize_local_issue_state(state, allow_empty=allow_empty)


def _normalize_issue_priority(priority: str | None) -> str | None:
    return issue_helpers.normalize_issue_priority(priority)


def _local_issue_fields(issue: LocalIssueRecord) -> dict[str, object]:
    return issue_helpers.local_issue_fields(issue)


def _issue_priority_sort_key(issue: LocalIssueRecord) -> tuple[int, datetime, datetime, str]:
    return issue_helpers.issue_priority_sort_key(issue)


def _issue_number_sort_key(issue: LocalIssueRecord) -> tuple[int, int | str, str]:
    return issue_helpers.issue_number_sort_key(issue)


def _approval_sort_key(request: ApprovalRequest) -> tuple[int, datetime, str]:
    return issue_helpers.approval_sort_key(request)


def _directory_path_matches_scope(
    request_directory_path: str | None,
    selected_directory_path: str | None,
    *,
    direct_only: bool,
) -> bool:
    return issue_helpers.directory_path_matches_scope(
        request_directory_path, selected_directory_path, direct_only=direct_only
    )


def _redact_sensitive_payload(value: Any, *, key_name: str | None = None) -> Any:
    from agent_sandbox.admin.services.approval_audit_service import redact_sensitive_payload

    return redact_sensitive_payload(value, key_name=key_name)


_extract_issue_number = session_helpers.extract_issue_number
_read_issue_number_from_yaml = session_helpers.read_issue_number_from_yaml


def _extract_issue_number_from_payload(payload: dict[str, Any]) -> str | None:
    return issue_helpers.extract_issue_number_from_payload(payload)


_resolve_issue_number = session_helpers.resolve_issue_number
_resolve_vm_name = session_helpers.resolve_vm_name
_resolve_vm_name_from_target = session_helpers.resolve_vm_name_from_target
_issue_session_id_prefix = session_helpers.issue_session_id_prefix


def _scoped_issue_id(
    issue_number: str,
    *,
    directory_path: str,
    provider: str,
    organization: str | None = None,
    project: str | None = None,
) -> str:
    return issue_helpers.scoped_issue_id(
        issue_number,
        directory_path=directory_path,
        provider=provider,
        organization=organization,
        project=project,
    )


_parse_session_extension = session_helpers.parse_session_extension


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


_resolve_git_root = git_helpers.resolve_git_root


_matching_issue_session_ids = session_helpers.matching_issue_session_ids
_session_sort_key = session_helpers.session_sort_key
_resolve_session_target = session_helpers.resolve_session_target


_default_base_branch_for_repo = git_helpers.default_base_branch_for_repo
_default_base_branch_for_repo_remote = git_helpers.default_base_branch_for_repo_remote
_resolve_canonical_origin_url_for_repo = git_helpers.resolve_canonical_origin_url_for_repo
_is_remote_git_source = git_helpers.is_remote_git_source
_mirror_remote_name_for_repo = git_helpers.mirror_remote_name_for_repo


_prune_pr_map = git_helpers.prune_pr_map
_prune_pr_feedback_store = git_helpers.prune_pr_feedback_store
_prune_approvals_for_sessions = session_helpers.prune_approvals_for_sessions
_remove_issue_bare_repo = git_helpers.remove_issue_bare_repo


_caller_home = vm_helpers.caller_home
_resolve_private_key_path = vm_helpers.resolve_private_key_path
_known_hosts_path = vm_helpers.known_hosts_path
_run_subprocess = process_helpers.run_subprocess
_combined_process_message = process_helpers.combined_process_message
_python_has_pip = process_helpers.python_has_pip
_ensure_python_has_pip = process_helpers.ensure_python_has_pip
_resolve_host_python_with_pip = process_helpers.resolve_host_python_with_pip


_run_virsh = vm_helpers.run_virsh


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


_virsh_lines = vm_helpers.virsh_lines
_dominfo_field = vm_helpers.dominfo_field
_resolve_vm_ip = vm_helpers.resolve_vm_ip
_resolve_vm_ip_with_pkexec = vm_helpers.resolve_vm_ip_with_pkexec
_wait_for_vm_ip = vm_helpers.wait_for_vm_ip
_wait_for_vm_ssh = vm_helpers.wait_for_vm_ssh
_ssh_options = vm_helpers.ssh_options
_resolve_vm_ssh_access = vm_helpers.resolve_vm_ssh_access
_remote_shell_script = vm_helpers.remote_shell_script
_probe_agent_state = vm_helpers.probe_agent_state


_default_issue_session_id = session_helpers.default_issue_session_id
_default_vm_session_id = session_helpers.default_vm_session_id
_copilot_host_auth_enabled = session_helpers.copilot_host_auth_enabled
_build_issue_session_manifest = session_helpers.build_issue_session_manifest
_matching_vm_session_ids = session_helpers.matching_vm_session_ids
_normalize_session_labels = session_helpers.normalize_session_labels


_append_unique_string_values = vm_agent_service._append_unique_string_values


_email_mailbox_id_from_auth_profile_ref = vm_agent_service._email_mailbox_id_from_auth_profile_ref


_admin_agent_communication_allowlists = vm_agent_service._admin_agent_communication_allowlists


_normalize_allowed_tools = session_helpers.normalize_allowed_tools
_session_tool_argument_policies_from_payload = (
    session_helpers.session_tool_argument_policies_from_payload
)
_normalize_allowed_script_names = session_helpers.normalize_allowed_script_names
_normalize_plain_string_list = session_helpers.normalize_plain_string_list
_normalize_teams_channel_allowlist = session_helpers.normalize_teams_channel_allowlist
_normalize_teams_user_allowlist = session_helpers.normalize_teams_user_allowlist
_validate_allowed_contact_ids_for_directory = (
    session_helpers.validate_allowed_contact_ids_for_runtime_directory
)


COMMUNICATION_FILE_SESSION_POLICY_FIELDS = session_helpers.COMMUNICATION_FILE_SESSION_POLICY_FIELDS
SESSION_BOOL_POLICY_FIELDS = session_helpers.SESSION_BOOL_POLICY_FIELDS
_normalize_session_policy_bool = session_helpers.normalize_session_policy_bool
_normalize_session_policy_non_negative_int_or_none = (
    session_helpers.normalize_session_policy_non_negative_int_or_none
)
_communication_file_session_policy_update = session_helpers.communication_file_session_policy_update
_session_policy_update_from_payload = session_helpers.session_policy_update_from_payload


_resolve_session_approval_notification_target_ids = (
    vm_agent_service._resolve_session_approval_notification_target_ids
)


_sync_added_discord_session_cursors = session_helpers.sync_added_discord_session_cursors
_wake_eligible_discord_channel_ids = session_helpers.wake_eligible_discord_channel_ids


_deep_merge_mapping = session_helpers.deep_merge_mapping
_pkexec_env_command = process_helpers.pkexec_env_command
_sudo_env_command = process_helpers.sudo_env_command
_can_prompt_for_privileges = process_helpers.can_prompt_for_privileges


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
    """Legacy/stable integration facade for host admin surfaces.

    ``AdminService`` remains the compatibility boundary used by CLI, gateway,
    and Admin Web integrations. New domain logic should live in narrower
    services under ``src/agent_sandbox/admin/services/`` and be exposed here
    only when a stable facade method is needed for existing integrations.
    """

    def __init__(
        self,
        runtime_dir: str | Path = DEFAULT_RUNTIME_DIR,
        *,
        legacy_config_path: str | Path | None = None,
        config_path: str | Path | None = None,
    ) -> None:
        from agent_sandbox.admin.composition import build_admin_service_components

        components = build_admin_service_components(
            self,
            runtime_dir=runtime_dir,
            legacy_config_path=legacy_config_path,
            config_path=config_path,
        )
        self.runtime_dir = components.runtime_dir
        self.legacy_config_path = components.legacy_config_path
        self.project_dir = components.project_dir
        self.config = components.config
        self.runtime = components.runtime
        self.tool_call_audit_index = components.tool_call_audit_index
        self.skill_library = components.skill_library
        self.agent_control_store = components.agent_control_store
        self.local_issue_store = components.local_issue_store
        self.local_issue_identity_resolver = components.local_issue_identity_resolver
        self.local_issue_counter_path = components.local_issue_counter_path
        self.local_issue_create_lock_path = components.local_issue_create_lock_path
        self.admin_config_mutation_lock_path = components.admin_config_mutation_lock_path
        self.local_issue_yaml_dir = components.local_issue_yaml_dir
        self.workflow_generation_transport = components.workflow_generation_transport
        self.use_privileged_helper = components.use_privileged_helper
        self._privileged_helper = components.privileged_helper
        self.directory_admin = components.directory_admin
        self.directory_ownership = components.directory_ownership
        self.issue_service = components.issue_service
        self.session_admin = components.session_admin
        self.teams_admin = components.teams_admin
        self.worker_admin = components.worker_admin
        self.workflow_config_admin = components.workflow_config_admin
        self.skill_admin = components.skill_admin
        self.capacity_health_admin = components.capacity_health_admin
        self.model_usage_admin = components.model_usage_admin
        self.operator_steering_admin = components.operator_steering_admin
        self.workflow_generation_admin = components.workflow_generation_admin
        self.workflow_reports_admin = components.workflow_reports_admin
        self.approval_audit_admin = components.approval_audit_admin
        self.artifact_admin = components.artifact_admin
        self.issue_git_support = components.issue_git_support
        self.vm_agent_support = components.vm_agent_support
        self.issue_git_admin = components.issue_git_admin
        self.vm_agent_admin = components.vm_agent_admin
        self.pr_integration_preflight_admin = components.pr_integration_preflight_admin
        self.operations_admin = components.operations_admin
        self.trigger_repository = components.trigger_repository
        self.trigger_event_repository = components.trigger_event_repository
        self.agent_create_operations = components.agent_create_operations
        self._agent_create_operation_owner_id = components.agent_create_operation_owner_id
        self._agent_create_operation_payloads = components.agent_create_operation_payloads
        self._agent_create_operation_payloads_lock = components.agent_create_operation_payloads_lock
        self._agent_create_executor = components.agent_create_executor
        self._execution_backend_instance = components.execution_backend_instance
        self.vm_agent_admin.configure_admin_collaborators(
            operations_admin=self.operations_admin,
            skill_admin=self.skill_admin,
            workflow_config_admin=self.workflow_config_admin,
            directory_admin=self.directory_admin,
            skill_library=self.skill_library,
            agent_create_operations=self.agent_create_operations,
            agent_create_operation_owner_id=self._agent_create_operation_owner_id,
            agent_create_operation_payloads=self._agent_create_operation_payloads,
            agent_create_operation_payloads_lock=self._agent_create_operation_payloads_lock,
            agent_create_executor=self._agent_create_executor,
            project_dir_provider=lambda: self.project_dir,
        )

    @property
    def tool_call_audit_index(self) -> Any | None:
        return _admin_service_tool_call_audit_index(self)

    @tool_call_audit_index.setter
    def tool_call_audit_index(self, value: Any | None) -> None:
        _set_admin_service_tool_call_audit_index(self, value)

    @property
    def directory_scoped_config_enabled(self) -> bool:
        return self.config.enable_directory_scoped_config

    def effective_execution_backend(self, directory_path: str | None = None) -> str:
        return self.vm_agent_admin.effective_execution_backend(directory_path)

    def read_capacity_health(self) -> CapacityHealthPayload:
        return self.capacity_health_admin.read_capacity_health()

    def list_model_usage_events(
        self,
        *,
        session_id: str | None = None,
        workflow_run_id: str | None = None,
        effect_id: str | None = None,
        dispatch_id: str | None = None,
        directory_path: str | None = None,
        include_directory_descendants: bool = False,
        provider: str | None = None,
        model: str | None = None,
        agent_harness: str | None = None,
        execution_backend: str | None = None,
        normalized_usage_status: str | None = None,
        request_status: str | None = None,
        occurred_after: datetime | None = None,
        occurred_before: datetime | None = None,
        limit: int | None = None,
    ) -> list[ModelInferenceUsageEvent]:
        return self.model_usage_admin.list_model_usage_events(
            session_id=session_id,
            workflow_run_id=workflow_run_id,
            effect_id=effect_id,
            dispatch_id=dispatch_id,
            directory_path=directory_path,
            include_directory_descendants=include_directory_descendants,
            provider=provider,
            model=model,
            agent_harness=agent_harness,
            execution_backend=execution_backend,
            normalized_usage_status=normalized_usage_status,
            request_status=request_status,
            occurred_after=occurred_after,
            occurred_before=occurred_before,
            limit=limit,
        )

    def operator_steering_directory_path(
        self, *, session_id: str, dispatch_id: str | None = None
    ) -> str:
        return self.model_usage_admin.operator_steering_directory_path(
            session_id=session_id, dispatch_id=dispatch_id
        )

    def active_operator_steering_context(
        self, *, session_id: str
    ) -> ModelUsageCorrelationContext | None:
        return self.model_usage_admin.active_operator_steering_context(session_id=session_id)

    def create_operator_steering_note(
        self,
        *,
        session_id: str,
        dispatch_id: str,
        message: str,
        worker_execution_id: str | None = None,
        created_by: str | None = None,
    ) -> OperatorSteeringNote:
        return self.model_usage_admin.create_operator_steering_note(
            session_id=session_id,
            dispatch_id=dispatch_id,
            message=message,
            worker_execution_id=worker_execution_id,
            created_by=created_by,
        )

    def submit_operator_steering_note(
        self,
        *,
        session_id: str,
        dispatch_id: str,
        message: str,
        worker_execution_id: str | None = None,
        created_by: str | None = None,
    ) -> dict[str, Any]:
        return self.model_usage_admin.submit_operator_steering_note(
            session_id=session_id,
            dispatch_id=dispatch_id,
            message=message,
            worker_execution_id=worker_execution_id,
            created_by=created_by,
        )

    def read_deployment_status(self) -> DeploymentStatusPayload:
        return self.operations_admin.read_deployment_status()

    def read_release_info(self, *, news_index: int = 0) -> ReleaseInfoPayload:
        return self.operations_admin.read_release_info(news_index=news_index)

    def release_changelog_file(self) -> Path:
        return self.operations_admin.release_changelog_file()

    def _execution_backend(self):
        return self.vm_agent_admin.execution_backend()

    def _normalize_directory_reference(self, reference: str | None) -> str:
        return self.teams_admin.normalize_directory_reference(reference)

    def _teams_auth_provider_for_directory(
        self,
        directory_path: str | None,
    ) -> tuple[str, Any, str, Any]:
        return self.teams_admin._teams_auth_provider_for_directory(directory_path)

    def _teams_auth_payload(
        self,
        *,
        directory_path: str,
        config: Any,
        identity_id: str,
        credentials: Any | None,
    ) -> dict[str, object]:
        return self.teams_admin._teams_auth_payload(
            directory_path=directory_path,
            config=config,
            identity_id=identity_id,
            credentials=credentials,
        )

    # Deprecated compatibility wrappers: new internal callers should depend on
    # operations_admin for Teams auth and privileged-operation methods.
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
        config: Any,
        provider: Any,
        *,
        team_id: str,
    ) -> list[dict[str, Any]]:
        return self.teams_admin._teams_graph_list_channels(config, provider, team_id=team_id)

    def unlock_privileged_operations(self) -> str:
        return self.operations_admin.unlock_privileged_operations()

    def lock_privileged_operations(self) -> None:
        return self.operations_admin.lock_privileged_operations()

    def privileged_operations_unlocked(self) -> bool:
        return self.operations_admin.privileged_operations_unlocked()

    def privileged_helper_required(self) -> bool:
        return self.operations_admin.privileged_helper_required()

    def _session_ids_by_vm(self) -> dict[str, list[str]]:
        return self.vm_agent_admin.session_ids_by_vm()

    def _directory_paths_by_vm(self) -> dict[str, list[str]]:
        return self.vm_agent_admin.directory_paths_by_vm()

    def _labels_by_vm(self) -> dict[str, dict[str, str]]:
        return self.vm_agent_admin.labels_by_vm()

    def _normalize_directory_path(self, directory_path: str | None) -> str:
        return self.vm_agent_admin.normalize_directory_path(directory_path)

    def _resolve_vm_defaults(self, directory_path: str) -> EffectiveVmDefaultsConfig:
        return self.vm_agent_admin.resolve_vm_defaults(directory_path)

    def _resolve_issue_setup(
        self, directory_path: str, *, root_dir: Path
    ) -> EffectiveIssueSetupConfig:
        return self.vm_agent_admin.resolve_issue_setup(directory_path, root_dir=root_dir)

    def _resolve_agent_model_for_session(self, session_or_id: SessionManifest | str) -> str:
        return self.vm_agent_admin.resolve_agent_model_for_session(session_or_id)

    def resolve_agent_model_for_target(self, target: str) -> str:
        return self.vm_agent_admin.resolve_agent_model_for_target(target)

    def _resolve_repo_source(self, repo_source: str) -> tuple[str, str]:
        return self.vm_agent_admin.resolve_repo_source(repo_source)

    def _direct_owned_entity_ids(self, directory_path: str, *, kind: str) -> set[str]:
        return self.issue_git_support.direct_owned_entity_ids(directory_path, kind=kind)

    def list_sessions(
        self,
        *,
        include_expired: bool = False,
        expired_only: bool = False,
        directory_path: str | None = None,
        direct_only: bool = False,
    ) -> list[SessionSummary]:
        return self.vm_agent_admin.list_sessions(
            include_expired=include_expired,
            expired_only=expired_only,
            directory_path=directory_path,
            direct_only=direct_only,
        )

    def _interrupt_container_operator_steering_note(
        self,
        *,
        session: SessionManifest,
        context: ModelUsageCorrelationContext,
        message: str,
        worker_execution_id: str | None,
        created_by: str | None,
    ) -> dict[str, Any]:
        return self.operator_steering_admin.interrupt_container_operator_steering_note(
            session=session,
            context=context,
            message=message,
            worker_execution_id=worker_execution_id,
            created_by=created_by,
        )

    def _operator_steering_execution_store(self):
        return self.operator_steering_admin._operator_steering_execution_store()

    def list_operator_steering_notes(
        self,
        *,
        session_id: str,
        dispatch_id: str | None = None,
        limit: int = 50,
    ) -> list[OperatorSteeringNote]:
        return self.operator_steering_admin.list_operator_steering_notes(
            session_id=session_id,
            dispatch_id=dispatch_id,
            limit=limit,
        )

    def get_operator_steering_note(self, note_id: str) -> OperatorSteeringNote:
        return self.operator_steering_admin.get_operator_steering_note(note_id)

    def cancel_operator_steering_note(self, note_id: str) -> OperatorSteeringNote:
        return self.operator_steering_admin.cancel_operator_steering_note(note_id)

    def read_model_pricing_catalog_status(self) -> dict[str, Any]:
        return self.model_usage_admin.read_model_pricing_catalog_status()

    def _read_model_pricing_catalog_status_unlocked(self) -> dict[str, Any]:
        return self.model_usage_admin._read_model_pricing_catalog_status_unlocked()

    def write_model_pricing_catalog(
        self, payload: dict[str, Any], *, expected_revision: str
    ) -> dict[str, Any]:
        return self.model_usage_admin.write_model_pricing_catalog(
            payload, expected_revision=expected_revision
        )

    def _model_pricing_error_revision(self, path: Path) -> str:
        return self.model_usage_admin._model_pricing_error_revision(path)

    def _model_pricing_rule_summary(self, rule: Any) -> dict[str, Any]:
        return self.model_usage_admin._model_pricing_rule_summary(rule)

    # Deprecated compatibility wrappers: new internal callers should depend on
    # operations_admin for session, wake, directory, and worker operations.
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

    @staticmethod
    def _normalize_wake_ledger_statuses(statuses: list[str] | None) -> list[str] | None:
        return _admin_service_normalize_wake_ledger_statuses(statuses)

    def _wake_ledger_entry_for_request(self, request: WakeScheduledRequest) -> dict[str, Any]:
        return self.session_admin._wake_ledger_entry_for_request(request)

    def list_scheduled_wake_ledger(
        self,
        *,
        statuses: list[str] | None = None,
        session_id: str | None = None,
        limit: int = 100,
        include_completed: bool = True,
    ) -> list[dict[str, Any]]:
        return self.operations_admin.list_scheduled_wake_ledger(
            statuses=statuses,
            session_id=session_id,
            limit=limit,
            include_completed=include_completed,
        )

    def retry_scheduled_wake(
        self,
        request_id: str,
        *,
        resume_at: datetime | None = None,
        run_now: bool = True,
    ) -> dict[str, Any]:
        return self.operations_admin.retry_scheduled_wake(
            request_id,
            resume_at=resume_at,
            run_now=run_now,
        )

    def _active_wake_requests(
        self, *, exclude_request_id: str | None = None
    ) -> list[WakeScheduledRequest]:
        return self.session_admin._active_wake_requests(exclude_request_id=exclude_request_id)

    def _enforce_wake_schedule_guardrails(
        self,
        *,
        session_id: str,
        schedule: WakeScheduleDefinition,
        enabled: bool,
        exclude_request_id: str | None = None,
    ) -> None:
        self.session_admin._enforce_wake_schedule_guardrails(
            session_id=session_id,
            schedule=schedule,
            enabled=enabled,
            exclude_request_id=exclude_request_id,
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
        return self.vm_agent_admin.delete_session(target)

    def cleanup_container_session(self, session_id: str) -> SessionActionResult:
        return self.vm_agent_admin.cleanup_container_session(session_id)

    def set_session_allowed_tools(
        self, target: str, allowed_tools: list[str | ToolName]
    ) -> SessionActionResult:
        return self.operations_admin.set_session_allowed_tools(target, allowed_tools)

    def set_session_allowed_script_names(
        self, target: str, allowed_script_names: list[str]
    ) -> SessionActionResult:
        return self.operations_admin.set_session_allowed_script_names(target, allowed_script_names)

    def update_session_policy(self, target: str, payload: dict[str, object]) -> SessionActionResult:
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

    @staticmethod
    def _agent_record_directory_path(directory_path: str | None) -> str:
        return VmAgentAdminService.agent_record_directory_path(directory_path)

    @classmethod
    def _agent_record_matches_directory(
        cls, record_directory_path: str | None, selected_directory_path: str, *, direct_only: bool
    ) -> bool:
        return VmAgentAdminService.agent_record_matches_directory(
            record_directory_path, selected_directory_path, direct_only=direct_only
        )

    @staticmethod
    def _container_agent_status(last_status: str | None) -> str:
        return VmAgentAdminService.container_agent_status(last_status)

    @staticmethod
    def _container_codex_outcome(record: Any) -> Mapping[str, Any] | None:
        return VmAgentAdminService.container_codex_outcome(record)

    @classmethod
    def _container_execution_status(cls, record: Any) -> str:
        return VmAgentAdminService.container_execution_status(record)

    @staticmethod
    def _agent_status_is_active(status: str | None) -> bool:
        return VmAgentAdminService.agent_status_is_active(status)

    @staticmethod
    def _container_execution_sort_key(record: Any) -> tuple[datetime, str]:
        return VmAgentAdminService.container_execution_sort_key(record)

    @classmethod
    def _container_session_status(cls, records: list[Any]) -> str:
        return VmAgentAdminService.container_session_status(records)

    @staticmethod
    def _normalize_agent_backend(backend: str | None) -> str:
        return VmAgentAdminService.normalize_agent_backend(backend)

    @classmethod
    def _normalize_agent_log_view(cls, view: str | None) -> str:
        return VmAgentAdminService.normalize_agent_log_view(view)

    @staticmethod
    def _container_log_thread_id(text: str) -> str | None:
        return VmAgentAdminService.container_log_thread_id(text)

    @staticmethod
    def _container_log_without_session_id_lines(text: str) -> str:
        return VmAgentAdminService.container_log_without_session_id_lines(text)

    @staticmethod
    def _container_execution_stored_thread_id(execution: Any) -> str | None:
        return VmAgentAdminService.container_execution_stored_thread_id(execution)

    @classmethod
    def _normalize_agent_backend_filter(cls, backend: str | None) -> str:
        return VmAgentAdminService.normalize_agent_backend_filter(backend)

    @staticmethod
    def _agent_optional_string(payload: Mapping[str, Any], key: str) -> str | None:
        return VmAgentAdminService.agent_optional_string(payload, key)

    @staticmethod
    def _agent_string_list(payload: Mapping[str, Any], key: str) -> list[str] | None:
        return VmAgentAdminService.agent_string_list(payload, key)

    @staticmethod
    def _agent_int_or_none(payload: Mapping[str, Any], key: str) -> int | None:
        return VmAgentAdminService.agent_int_or_none(payload, key)

    @staticmethod
    def _container_session_target_name(session: SessionManifest) -> str:
        return VmAgentAdminService.container_session_target_name(session)

    @staticmethod
    def _is_container_session(session: SessionManifest) -> bool:
        return VmAgentAdminService.is_container_session(session)

    @staticmethod
    def _container_lifecycle_action(
        *, supported: bool, kind: str | None, label: str, reason: str | None = None
    ) -> ContainerSessionLifecycleAction:
        return VmAgentAdminService.container_lifecycle_action(
            supported=supported, kind=kind, label=label, reason=reason
        )

    @staticmethod
    def _container_session_lifecycle_metadata(session: SessionManifest) -> dict[str, Any]:
        return VmAgentAdminService.container_session_lifecycle_metadata(session)

    @staticmethod
    def _container_record_lifecycle_metadata(record: Any) -> dict[str, Any]:
        return VmAgentAdminService.container_record_lifecycle_metadata(record)

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
        return VmAgentAdminService.container_session_lifecycle_tombstone(
            session,
            state=state,
            cleanup_status=cleanup_status,
            attempted_at=attempted_at,
            completed_at=completed_at,
            cleanup_error=cleanup_error,
        )

    @staticmethod
    def _container_execution_lifecycle_status(record: Any) -> str:
        return VmAgentAdminService.container_execution_lifecycle_status(record)

    def _session_summary_with_container_lifecycle(self, summary: SessionSummary) -> SessionSummary:
        return self.vm_agent_admin.session_summary_with_container_lifecycle(summary)

    def _container_session_lifecycle(
        self, session: SessionManifest, *, executions: list[Any] | None = None
    ) -> ContainerSessionLifecycle | None:
        return self.vm_agent_admin.container_session_lifecycle(session, executions=executions)

    def _agent_session_policy_payload(
        self, session: SessionManifest | None
    ) -> dict[str, Any] | None:
        return self.vm_agent_admin.agent_session_policy_payload(session)

    def _approval_notification_target_options(
        self, session: SessionManifest | None
    ) -> list[WorkflowEditorFieldOption]:
        return self.vm_agent_admin.approval_notification_target_options(session)

    def effective_tool_policy_payload(
        self, session: SessionManifest | None
    ) -> dict[str, Any] | None:
        return self.vm_agent_admin.effective_tool_policy_payload(session)

    def tool_visibility_payload(
        self,
        *,
        target: str,
        mcp_transport_session_id: str | None = None,
        workflow_run_id: str | None = None,
        mcp_tool_approval_lease_id: str | None = None,
    ) -> ToolVisibilityPayload:
        return self.approval_audit_admin.tool_visibility_payload(
            target=target,
            mcp_transport_session_id=mcp_transport_session_id,
            workflow_run_id=workflow_run_id,
            mcp_tool_approval_lease_id=mcp_tool_approval_lease_id,
        )

    def _read_agent_prompt_queue_records(self) -> tuple[dict[str, Any], str | None]:
        return self.vm_agent_admin.read_agent_prompt_queue_records()

    @staticmethod
    def _agent_prompt_queue_candidates(agent: AgentRecord) -> set[str]:
        return VmAgentAdminService.agent_prompt_queue_candidates(agent)

    @staticmethod
    def _agent_prompt_queue_record_matches(record: Any, *, candidates: set[str]) -> bool:
        return VmAgentAdminService.agent_prompt_queue_record_matches(record, candidates=candidates)

    @staticmethod
    def _agent_prompt_queue_preview(prompt: str) -> tuple[str | None, int]:
        return VmAgentAdminService.agent_prompt_queue_preview(prompt)

    def _agent_prompt_queue_record_payload(self, record: Any) -> AgentPromptQueueRecord:
        return self.vm_agent_admin.agent_prompt_queue_record_payload(record)

    def _agent_prompt_queue_summary(
        self,
        agent: AgentRecord,
        queued_records: Mapping[str, Any],
        *,
        read_error: str | None,
        record_limit: int = 20,
    ) -> AgentPromptQueueSummary:
        return self.vm_agent_admin.agent_prompt_queue_summary(
            agent, queued_records, read_error=read_error, record_limit=record_limit
        )

    def _agent_records_with_prompt_queue(self, records: list[AgentRecord]) -> list[AgentRecord]:
        return self.vm_agent_admin.agent_records_with_prompt_queue(records)

    @staticmethod
    def _runtime_image_ref_display_details(image_ref: str) -> tuple[str, bool]:
        return VmAgentAdminService.runtime_image_ref_display_details(image_ref)

    def session_runtime_image_payload(
        self, session: SessionManifest | SessionSummary
    ) -> dict[str, Any]:
        return self.vm_agent_admin.session_runtime_image_payload(session)

    def list_agents(
        self,
        *,
        directory_path: str | None = None,
        direct_only: bool = False,
        include_inactive: bool = True,
        reconcile_containers: bool = True,
        omit_vm_state: bool = False,
        summary_only: bool = False,
        backend: str | None = None,
        all_directories: bool = False,
        include_prompt_queue: bool = False,
    ) -> list[AgentRecord]:
        return self.vm_agent_admin.list_agents(
            directory_path=directory_path,
            direct_only=direct_only,
            include_inactive=include_inactive,
            reconcile_containers=reconcile_containers,
            omit_vm_state=omit_vm_state,
            summary_only=summary_only,
            backend=backend,
            all_directories=all_directories,
            include_prompt_queue=include_prompt_queue,
        )

    def _agent_create_operation_details(
        self, payload: Mapping[str, Any]
    ) -> tuple[str, str, str | None, str]:
        return self.vm_agent_admin.agent_create_operation_details(payload)

    @staticmethod
    def _agent_create_operation_worker_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
        return VmAgentAdminService.agent_create_operation_worker_payload(payload)

    def submit_admin_agent_create(self, payload: Mapping[str, Any]) -> AgentCreateOperation:
        return self.vm_agent_admin.submit_admin_agent_create(payload)

    def list_admin_agent_create_operations(
        self, *, limit: int = 50, directory_path: str | None = None
    ) -> list[AgentCreateOperation]:
        return self.vm_agent_admin.list_admin_agent_create_operations(
            limit=limit, directory_path=directory_path
        )

    def get_admin_agent_create_operation(self, operation_id: str) -> AgentCreateOperation:
        return self.vm_agent_admin.get_admin_agent_create_operation(operation_id)

    def _update_agent_create_operation(
        self, operation_id: str, updater: Any
    ) -> AgentCreateOperation | None:
        return self.vm_agent_admin.update_agent_create_operation(operation_id, updater)

    def _run_agent_create_operation(self, operation_id: str) -> None:
        return self.vm_agent_admin.run_agent_create_operation(operation_id)

    @staticmethod
    def _agent_create_operation_owner_is_alive(owner_pid: int | None) -> bool:
        return VmAgentAdminService.agent_create_operation_owner_is_alive(owner_pid)

    def mark_abandoned_agent_create_operations_failed(self) -> None:
        return self.vm_agent_admin.mark_abandoned_agent_create_operations_failed()

    def sync_added_discord_session_cursors(
        self, *, previous_session: SessionManifest | None, updated_session: SessionManifest
    ) -> dict[str, str]:
        return self.vm_agent_admin.sync_added_discord_session_cursors(
            previous_session=previous_session, updated_session=updated_session
        )

    def create_admin_agent(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self.vm_agent_admin.create_admin_agent(payload)

    def _resolve_container_agent_session(
        self, agent_id: str, *, require_session: bool
    ) -> tuple[SessionManifest | None, Any | None]:
        return self.vm_agent_admin.resolve_container_agent_session(
            agent_id, require_session=require_session
        )

    def _container_execution_records_for_session(self, session_id: str) -> list[Any]:
        return self.vm_agent_admin.container_execution_records_for_session_compat(session_id)

    def _session_associated_agent_description(
        self, session_id: str, session: SessionManifest
    ) -> str | None:
        return self.vm_agent_admin.session_associated_agent_description(session_id, session)

    def _delete_chat_assistant_session_mappings(self, session_ids: set[str]) -> int:
        return self.vm_agent_admin.delete_chat_assistant_session_mappings(session_ids)

    def _delete_detached_worker_session_state(self, session_id: str) -> tuple[bool, int]:
        return self.vm_agent_admin.delete_detached_worker_session_state(session_id)

    @staticmethod
    def _worker_delete_session_removed(result: Any) -> bool:
        return VmAgentAdminService.worker_delete_session_removed(result)

    def _resolve_vm_agent_session(self, agent_id: str) -> SessionManifest:
        return self.vm_agent_admin.resolve_vm_agent_session(agent_id)

    def _record_installed_codex_skills(self, session_id: str, skill_names: list[str]) -> None:
        return self.vm_agent_admin.record_installed_codex_skills(session_id, skill_names)

    @staticmethod
    def _skill_install_files_for_codex_home(
        entries: list[SkillLibraryEntry], *, codex_home: str
    ) -> list[dict[str, Any]]:
        return VmAgentAdminService.skill_install_files_for_codex_home(
            entries, codex_home=codex_home
        )

    def _install_codex_skills_into_vm_agent(
        self, agent_id: str, *, skill_names: list[str], vm_user: str
    ) -> dict[str, Any]:
        return self.vm_agent_admin.install_codex_skills_into_vm_agent(
            agent_id, skill_names=skill_names, vm_user=vm_user
        )

    def _install_codex_skills_into_container_agent(
        self, agent_id: str, *, skill_names: list[str]
    ) -> dict[str, Any]:
        return self.vm_agent_admin.install_codex_skills_into_container_agent(
            agent_id, skill_names=skill_names
        )

    def install_admin_agent_skills(
        self,
        agent_id: str,
        *,
        backend: str | None = None,
        skill_names: list[str] | tuple[str, ...] | None = None,
        vm_user: str = "agent",
    ) -> dict[str, Any]:
        return self.vm_agent_admin.install_admin_agent_skills(
            agent_id, backend=backend, skill_names=skill_names, vm_user=vm_user
        )

    def update_admin_agent(
        self, agent_id: str, *, backend: str | None = None, payload: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        return self.vm_agent_admin.update_admin_agent(agent_id, backend=backend, payload=payload)

    def delete_admin_agent(self, agent_id: str, *, backend: str | None = None) -> dict[str, Any]:
        return self.vm_agent_admin.delete_admin_agent(agent_id, backend=backend)

    def get_admin_agent_log_snapshot(
        self,
        agent_id: str,
        *,
        backend: str | None = None,
        view: str | None = None,
        lines: int = 50,
        repo_name: str | None = None,
        vm_user: str = "agent",
    ) -> str:
        return self.vm_agent_admin.get_admin_agent_log_snapshot(
            agent_id, backend=backend, view=view, lines=lines, repo_name=repo_name, vm_user=vm_user
        )

    def _container_execution_log_chunk(
        self, worker_backend: Any, execution: Any, *, lines: int, mode: str
    ) -> str:
        return self.vm_agent_admin.container_execution_log_chunk(
            worker_backend, execution, lines=lines, mode=mode
        )

    @staticmethod
    def _join_container_log_chunks(chunks: list[str]) -> str:
        return VmAgentAdminService.join_container_log_chunks(chunks)

    def _container_thread_execution_fallback_log(
        self, worker_backend: Any, records: list[Any], *, lines: int
    ) -> str:
        return self.vm_agent_admin.container_thread_execution_fallback_log(
            worker_backend, records, lines=lines
        )

    def _container_thread_transcript(
        self, worker_backend: Any, records: list[Any], *, lines: int
    ) -> str:
        return self.vm_agent_admin.container_thread_transcript(
            worker_backend, records, lines=lines
        )

    def _container_execution_thread_probe_text(self, worker_backend: Any, execution: Any) -> str:
        return self.vm_agent_admin.container_execution_thread_probe_text(worker_backend, execution)

    def _container_thread_log_chunks(
        self, worker_backend: Any, execution_records: list[Any], *, lines: int
    ) -> list[str]:
        return self.vm_agent_admin.container_thread_log_chunks(
            worker_backend, execution_records, lines=lines
        )

    def _container_thread_groups(
        self, worker_backend: Any, execution_records: list[Any]
    ) -> list[tuple[tuple[datetime, str], str | None, list[Any], str | None]]:
        return self.vm_agent_admin.container_thread_groups(worker_backend, execution_records)

    def _container_thread_window_chunks(
        self, worker_backend: Any, execution_records: list[Any], *, target_lines: int
    ) -> tuple[list[str], bool]:
        return self.vm_agent_admin.container_thread_window_chunks(
            worker_backend, execution_records, target_lines=target_lines
        )

    def _container_thread_group_chunk(
        self,
        worker_backend: Any,
        *,
        thread_id: str | None,
        records: list[Any],
        raw_text: str | None,
    ) -> str:
        return self.vm_agent_admin.container_thread_group_chunk(
            worker_backend, thread_id=thread_id, records=records, raw_text=raw_text
        )

    def _container_thread_log_page(
        self,
        *,
        session_id: str,
        execution_records: list[Any],
        worker_backend: Any,
        limit: int,
        offset: int,
        query: str | None,
    ) -> dict[str, object]:
        return self.vm_agent_admin.container_thread_log_page(
            session_id=session_id,
            execution_records=execution_records,
            worker_backend=worker_backend,
            limit=limit,
            offset=offset,
            query=query,
        )

    def _container_execution_log_page(
        self,
        *,
        session_id: str,
        execution_records: list[Any],
        worker_backend: Any,
        limit: int,
        offset: int,
        query: str | None,
    ) -> dict[str, object]:
        return self.vm_agent_admin.container_execution_log_page(
            session_id=session_id,
            execution_records=execution_records,
            worker_backend=worker_backend,
            limit=limit,
            offset=offset,
            query=query,
        )

    def get_admin_agent_log_page(
        self,
        agent_id: str,
        *,
        backend: str | None = None,
        view: str | None = None,
        limit: int = 200,
        offset: int = 0,
        query: str | None = None,
        repo_name: str | None = None,
        vm_user: str = "agent",
    ) -> dict[str, object]:
        return self.vm_agent_admin.get_admin_agent_log_page(
            agent_id,
            backend=backend,
            view=view,
            limit=limit,
            offset=offset,
            query=query,
            repo_name=repo_name,
            vm_user=vm_user,
        )

    def run_admin_agent_action(
        self,
        agent_id: str,
        action: str,
        *,
        backend: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.vm_agent_admin.run_admin_agent_action(
            agent_id, action, backend=backend, payload=payload
        )

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
        return self.vm_agent_admin.queued_admin_prompt_result_after_dispatch_error(
            queue_key=queue_key,
            target_name=target_name,
            session_id=session_id,
            backend=backend,
            attempted_at=attempted_at,
            error=error,
        )

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
        return self.vm_agent_admin.record_direct_container_admin_prompt_execution(
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

    def _resolve_optional_vm_agent_session(self, agent_id: str) -> SessionManifest | None:
        return self.vm_agent_admin.resolve_optional_vm_agent_session(agent_id)

    # Deprecated compatibility wrappers: new internal callers should depend on
    # operations_admin for background-worker operations.
    def list_background_workers(self) -> list[WorkerSummary]:
        return self.operations_admin.list_background_workers()

    def get_background_worker_log_snapshot(self, worker_id: str, *, lines: int = 200) -> str:
        return self.operations_admin.get_background_worker_log_snapshot(worker_id, lines=lines)

    def start_background_worker(self, worker_id: str) -> WorkerActionResult:
        return self.operations_admin.start_background_worker(worker_id)

    def stop_background_worker(self, worker_id: str) -> WorkerActionResult:
        return self.operations_admin.stop_background_worker(worker_id)

    def get_agent_log_snapshot(
        self, target: str, *, lines: int = 50, repo_name: str | None = None, vm_user: str = "agent"
    ) -> str:
        return self.vm_agent_admin.get_agent_log_snapshot(
            target, lines=lines, repo_name=repo_name, vm_user=vm_user
        )

    def start_vm(self, target: str) -> VmActionResult:
        return self.vm_agent_admin.start_vm(target)

    def shutdown_vm(self, target: str, *, timeout_seconds: int = 60) -> VmActionResult:
        return self.vm_agent_admin.shutdown_vm(target, timeout_seconds=timeout_seconds)

    def _run_script(
        self, script_name: str, args: list[str], *, env: dict[str, str] | None = None
    ) -> VmActionResult:
        return self.vm_agent_admin.run_script(script_name, args, env=env)

    # Deprecated compatibility wrappers: new internal callers should depend on
    # vm_agent_admin for VM and agent-control operations.
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
        return self.vm_agent_admin.spawn_agent_completion_watcher(
            vm_name=vm_name,
            dispatch_id=dispatch_id,
            vm_user=vm_user,
            session_id=session_id,
            directory_path=directory_path,
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
        self, target: str, *, files: list[dict[str, Any]], vm_user: str = "agent"
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
            target, paths=paths, vm_user=vm_user, recursive=recursive, missing_ok=missing_ok
        )

    # Deprecated compatibility wrappers: new internal callers should depend on
    # issue_git_admin for issue, approval, and Git/PR operations.
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

    def run_pr_integration_preflight(
        self,
        *,
        provider: str,
        repo: str,
        pr_ids: list[str],
        mode: str,
        directory_path: str,
        organization_url: str | None = None,
        project: str | None = None,
        repo_source: str | None = None,
    ):
        return self.pr_integration_preflight_admin.run_pr_integration_preflight(
            provider=provider,
            repo=repo,
            pr_ids=pr_ids,
            mode=mode,
            directory_path=directory_path,
            organization_url=organization_url,
            project=project,
            repo_source=repo_source,
        )

    def push_branch(
        self,
        *,
        host_repo_path: str,
        local_branch: str,
        origin_branch: str,
        canonical_origin_url: str | None = None,
        provider: str | None = None,
        base_branch: str | None = None,
        session_id: str | None = None,
        work_item_id: str | None = None,
    ) -> dict[str, Any]:
        return self.issue_git_admin.push_branch(
            host_repo_path=host_repo_path,
            local_branch=local_branch,
            origin_branch=origin_branch,
            canonical_origin_url=canonical_origin_url,
            provider=provider,
            base_branch=base_branch,
            session_id=session_id,
            work_item_id=work_item_id,
        )

    def promote_agent_branch(
        self,
        *,
        host_repo_path: str,
        bare_repo_path: str | None = None,
        bare_repo_name: str | None = None,
        agent_branch: str,
        review_branch: str | None = None,
        target_branch: str | None = None,
        expected_commit_sha: str | None = None,
        remote_name: str | None = None,
    ) -> dict[str, Any]:
        return self.issue_git_admin.promote_agent_branch(
            host_repo_path=host_repo_path,
            bare_repo_path=bare_repo_path,
            bare_repo_name=bare_repo_name,
            agent_branch=agent_branch,
            review_branch=review_branch,
            target_branch=target_branch,
            expected_commit_sha=expected_commit_sha,
            remote_name=remote_name,
        )

    def import_git_repo(
        self,
        *,
        repo_source: str,
        bare_repo_name: str | None = None,
        directory_path: str = "root",
    ) -> dict[str, Any]:
        return self.issue_git_admin.import_git_repo(
            repo_source=repo_source,
            bare_repo_name=bare_repo_name,
            directory_path=directory_path,
        )

    @staticmethod
    def _derive_review_branch(agent_branch: str) -> str:
        return _admin_service_derive_review_branch(agent_branch)

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
        return self.vm_agent_admin.read_session_artifact_text(
            session_id=session_id,
            path=path,
            vm_user=vm_user,
        )

    def list_published_artifacts(
        self,
        *,
        key: str | None = None,
        published_after: datetime | None = None,
        published_before: datetime | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> PublishedArtifactListPayload:
        return self.artifact_admin.list_published_artifacts(
            key=key,
            published_after=published_after,
            published_before=published_before,
            limit=limit,
            cursor=cursor,
        )

    def get_published_artifact(self, key: str) -> PublishedArtifactMetadata:
        return self.artifact_admin.get_published_artifact(key)

    def published_artifact_file(self, key: str) -> PublishedArtifact:
        return self.artifact_admin.published_artifact_file(key)

    def upload_published_artifact(
        self,
        *,
        key: str,
        filename: str,
        content: bytes,
        artifact_path: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> PublishedArtifactMetadata:
        return self.artifact_admin.upload_published_artifact(
            key=key,
            filename=filename,
            content=content,
            artifact_path=artifact_path,
            metadata=metadata,
        )

    def list_artifact_spaces(
        self,
        *,
        path: str | None = None,
        recursive: bool = True,
        subject: str | None = None,
        include_versions: bool = False,
    ) -> dict[str, Any]:
        return self.artifact_admin.list_artifact_spaces(
            path=path,
            recursive=recursive,
            subject=subject,
            include_versions=include_versions,
        )

    def create_artifact_space(
        self,
        *,
        path: str,
        display_name: str | None = None,
        metadata: dict[str, Any] | None = None,
        grants: dict[str, Any] | None = None,
        subject: str | None = None,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        return self.artifact_admin.create_artifact_space(
            path=path,
            display_name=display_name,
            metadata=metadata,
            grants=grants,
            subject=subject,
            expected_revision=expected_revision,
        )

    def publish_artifact_space_version(
        self,
        *,
        space_path: str,
        artifact_id: str,
        artifact_key: str,
        subject: str | None = None,
        metadata: dict[str, Any] | None = None,
        expected_revision: int | None = None,
        require_existing_artifact: bool = True,
    ) -> dict[str, Any]:
        return self.artifact_admin.publish_artifact_space_version(
            space_path=space_path,
            artifact_id=artifact_id,
            artifact_key=artifact_key,
            subject=subject,
            metadata=metadata,
            expected_revision=expected_revision,
            require_existing_artifact=require_existing_artifact,
        )

    def latest_artifact_space_versions(
        self,
        *,
        space_path: str,
        artifact_id: str | None = None,
        subject: str | None = None,
    ) -> dict[str, Any]:
        return self.artifact_admin.latest_artifact_space_versions(
            space_path=space_path,
            artifact_id=artifact_id,
            subject=subject,
        )

    @staticmethod
    def _derive_local_pr_notes_ref(remote_url: str) -> str:
        return _admin_service_derive_local_pr_notes_ref(remote_url)

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
        return self.issue_git_support.extract_local_issue_id_candidate(value)

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
        return self.approval_audit_admin._refresh_approval_request(request)

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
        return self.approval_audit_admin._approval_matches_filters(
            request,
            statuses=statuses,
            directory_path=directory_path,
            direct_only=direct_only,
            session_id=session_id,
            vm_id=vm_id,
            tool_name=tool_name,
            min_age_seconds=min_age_seconds,
        )

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
        return self.approval_audit_admin.list_approval_requests(
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
        return self.approval_audit_admin.approval_queue_health(
            statuses=statuses,
            directory_path=directory_path,
            direct_only=direct_only,
            session_id=session_id,
            vm_id=vm_id,
            tool_name=tool_name,
            min_age_seconds=min_age_seconds,
        )

    def get_approval_request(self, request_id: str) -> ApprovalRequest:
        return self.approval_audit_admin.get_approval_request(request_id)

    def _approval_audit_context(
        self, request: ApprovalRequest, *, limit: int = 8
    ) -> list[dict[str, Any]]:
        return self.approval_audit_admin._approval_audit_context(request, limit=limit)

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
        return self.approval_audit_admin.list_tool_call_audit_entries(
            session_id=session_id,
            tool_name=tool_name,
            status=status,
            directory_path=directory_path,
            direct_only=direct_only,
            created_after=created_after,
            created_before=created_before,
            q=q,
            limit=limit,
            recent_only=recent_only,
        )

    def _list_tool_call_audit_entries_from_index(self, *args: Any, **kwargs: Any):
        return self.approval_audit_admin._list_tool_call_audit_entries_from_index(*args, **kwargs)

    def _list_recent_tool_call_audit_entries_from_tail(self, *args: Any, **kwargs: Any):
        return self.approval_audit_admin._list_recent_tool_call_audit_entries_from_tail(
            *args, **kwargs
        )

    def get_approval_review(self, request_id: str) -> dict[str, Any]:
        return self.approval_audit_admin.get_approval_review(request_id)

    def decide_approval_request(
        self,
        request_id: str,
        *,
        approver: str,
        approve: bool,
        reason: str,
    ) -> ApprovalRequest:
        return self.approval_audit_admin.decide_approval_request(
            request_id,
            approver=approver,
            approve=approve,
            reason=reason,
        )

    def revoke_approval_request(
        self,
        request_id: str,
        *,
        approver: str,
        reason: str,
    ) -> ApprovalRequest:
        return self.approval_audit_admin.revoke_approval_request(
            request_id,
            approver=approver,
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

    # Deprecated compatibility wrappers: new internal callers should depend on
    # operations_admin for directory configuration operations.
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

    # Deprecated compatibility wrappers: new internal callers should depend on
    # workflow_config_admin for workflow config and run operations.
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

    def read_workflow_editor_document(
        self, workflow_pack: str | None = None
    ) -> WorkflowEditorDocumentPayload:
        return self.workflow_config_admin.read_workflow_editor_document(workflow_pack=workflow_pack)

    def read_composed_workflow_editor_document(self) -> WorkflowEditorDocumentPayload:
        return self.workflow_config_admin.read_composed_workflow_editor_document()

    def read_workflow_editor_catalog(
        self, directory_path: str | None = None
    ) -> WorkflowEditorCatalogPayload:
        return self.workflow_config_admin.read_workflow_editor_catalog(
            directory_path=directory_path
        )

    def list_skills(self) -> list[SkillLibraryMetadata]:
        return self.skill_admin.list_skills()

    def create_skill(self, payload: Mapping[str, Any]) -> SkillLibraryMetadata:
        return self.skill_admin.create_skill(payload)

    def get_skill(self, name: str) -> SkillLibraryEntry:
        return self.skill_admin.get_skill(name)

    def delete_skill(self, name: str) -> SkillLibraryMetadata:
        return self.skill_admin.delete_skill(name)

    def validate_managed_codex_skills(
        self, skill_names: list[str] | tuple[str, ...] | None
    ) -> list[str]:
        return self.skill_admin.validate_managed_codex_skills(skill_names)

    def resolve_container_codex_skill_files(
        self, skill_names: list[str] | tuple[str, ...] | None
    ) -> list[dict[str, Any]]:
        return self.skill_admin.resolve_container_codex_skill_files(skill_names)

    def validate_workflow_editor_document(
        self,
        *,
        config: dict[str, object],
        layout: dict[str, object] | None = None,
        graphs: list[dict[str, object]] | None = None,
        workflow_pack: str | None = None,
    ) -> WorkflowEditorValidationPayload:
        return self.workflow_config_admin.validate_workflow_editor_document(
            config=config,
            layout=layout,
            graphs=graphs,
            workflow_pack=workflow_pack,
        )

    async def generate_workflow_editor_draft(
        self,
        *,
        description: str,
        directory_path: str | None = None,
        include_current_packs: bool = True,
    ) -> WorkflowEditorGeneratedDraftPayload:
        return await self.workflow_generation_admin.generate_workflow_editor_draft(
            description=description,
            directory_path=directory_path,
            include_current_packs=include_current_packs,
        )

    def save_workflow_editor_document(
        self,
        *,
        config: dict[str, object],
        layout: dict[str, object] | None = None,
        graphs: list[dict[str, object]] | None = None,
        expected_revision: str | None = None,
        workflow_pack: str | None = None,
    ) -> WorkflowEditorSavePayload:
        return self.workflow_config_admin.save_workflow_editor_document(
            config=config,
            layout=layout,
            graphs=graphs,
            expected_revision=expected_revision,
            workflow_pack=workflow_pack,
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
        return self.workflow_reports_admin.read_workflow_graph_activity_report(
            period_start=period_start, period_end=period_end, limit=limit
        )

    def read_workflow_failure_clusters_report(
        self,
        *,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
        limit: int | None = None,
        recent_threshold_seconds: int | None = None,
    ) -> WorkflowFailureClusterReportPayload:
        return self.workflow_reports_admin.read_workflow_failure_clusters_report(
            period_start=period_start,
            period_end=period_end,
            limit=limit,
            recent_threshold_seconds=recent_threshold_seconds,
        )

    def read_conformance_reports(self) -> ConformanceReportsPayload:
        return self.workflow_reports_admin.read_conformance_reports()

    def run_conformance_report(self, kind: str) -> ConformanceReportRunPayload:
        return self.workflow_reports_admin.run_conformance_report(kind)

    def _read_workflow_run_snapshot(self) -> WorkflowRunSnapshotPayload:
        return self.workflow_reports_admin._read_workflow_run_snapshot()

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
        return self.workflow_reports_admin._read_workflow_run_detail(
            run_id=run_id,
            event_id=event_id,
            trigger_event_id=trigger_event_id,
            trace_id=trace_id,
            session_id=session_id,
            artifact_id=artifact_id,
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
        return self.workflow_reports_admin._read_workflow_run_search(
            node_id=node_id,
            graph_id=graph_id,
            without_session=without_session,
            include_completed=include_completed,
            limit=limit,
        )

    def _read_workflow_graph_activity_report(
        self,
        *,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
        limit: int | None = None,
    ) -> WorkflowGraphActivityReportPayload:
        return self.workflow_reports_admin._read_workflow_graph_activity_report(
            period_start=period_start, period_end=period_end, limit=limit
        )

    def _read_workflow_failure_clusters_report(
        self,
        *,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
        limit: int | None = None,
        recent_threshold_seconds: int | None = None,
    ) -> WorkflowFailureClusterReportPayload:
        return self.workflow_reports_admin._read_workflow_failure_clusters_report(
            period_start=period_start,
            period_end=period_end,
            limit=limit,
            recent_threshold_seconds=recent_threshold_seconds,
        )

    def _read_conformance_reports(self) -> ConformanceReportsPayload:
        return self.workflow_reports_admin._read_conformance_reports()

    def _run_conformance_report(self, kind: str) -> ConformanceReportRunPayload:
        return self.workflow_reports_admin._run_conformance_report(kind)

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

    def admin_config_mutation_lock(self):
        return self.operations_admin.admin_config_mutation_lock()

    def _worker_global_config_path(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._worker_global_config_path(*args, **kwargs)

    def _stable_json_revision(self, payload: object) -> str:
        return self.workflow_config_admin._stable_json_revision(payload)

    def _worker_global_config_revision(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._worker_global_config_revision(*args, **kwargs)

    def _read_worker_global_config_unlocked(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._read_worker_global_config_unlocked(*args, **kwargs)

    def _read_worker_global_config(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._read_worker_global_config(*args, **kwargs)

    def _read_worker_provider_loader_config(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._read_worker_provider_loader_config(*args, **kwargs)

    def _write_worker_global_config_unlocked(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._write_worker_global_config_unlocked(*args, **kwargs)

    def _write_worker_global_config(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._write_worker_global_config(*args, **kwargs)

    def _read_trigger_document(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._read_trigger_document(*args, **kwargs)

    def _write_trigger_document(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._write_trigger_document(*args, **kwargs)

    def _write_trigger_document_unlocked(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._write_trigger_document_unlocked(*args, **kwargs)

    def _validate_worker_global_config_payload(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._validate_worker_global_config_payload(*args, **kwargs)

    def _load_workflow_provider_catalog(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._load_workflow_provider_catalog(*args, **kwargs)

    def _load_workflow_provider_output_schemas(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._load_workflow_provider_output_schemas(*args, **kwargs)

    def assistant_settings_path(self) -> Path:
        return self.operations_admin.assistant_settings_path()

    def read_assistant_settings(self) -> dict[str, object]:
        return self.operations_admin.read_assistant_settings()

    def write_assistant_settings(self, payload: dict[str, object]) -> dict[str, object]:
        return self.operations_admin.write_assistant_settings(payload)

    def ensure_chat_assistant_workflow(self) -> TriggerConfigDocument:
        return self.operations_admin.ensure_chat_assistant_workflow()

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
        return self.operations_admin.publish_chat_assistant_message(
            user_id=user_id,
            thread_id=thread_id,
            user_message_id=user_message_id,
            content=content,
            backend=backend,
            assistant_capability_mode=assistant_capability_mode,
        )

    def publish_external_workflow_event(
        self,
        *,
        event_type: object,
        payload: dict[str, Any],
        correlation_key: object | None = None,
        idempotency_key: str | None = None,
        source: InternalEventSource | dict[str, Any] | None = None,
    ) -> dict[str, object]:
        return self.operations_admin.publish_external_workflow_event(
            event_type=event_type,
            payload=payload,
            correlation_key=correlation_key,
            idempotency_key=idempotency_key,
            source=source,
        )

    def _workflow_editor_default_layout_path(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._workflow_editor_default_layout_path(*args, **kwargs)

    def _workflow_editor_pack_layout_path(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._workflow_editor_pack_layout_path(*args, **kwargs)

    def _workflow_editor_pack_reference(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._workflow_editor_pack_reference(*args, **kwargs)

    def _workflow_editor_layout_path(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._workflow_editor_layout_path(*args, **kwargs)

    def _workflow_editor_revision(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._workflow_editor_revision(*args, **kwargs)

    def _workflow_editor_pack_path(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._workflow_editor_pack_path(*args, **kwargs)

    def _workflow_editor_configured_pack_references(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._workflow_editor_configured_pack_references(
            *args, **kwargs
        )

    def _workflow_editor_selected_pack_reference(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._workflow_editor_selected_pack_reference(*args, **kwargs)

    def _workflow_editor_pack_descriptors(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._workflow_editor_pack_descriptors(*args, **kwargs)

    def _workflow_editor_config_for_pack_composition(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._workflow_editor_config_for_pack_composition(
            *args, **kwargs
        )

    def _workflow_editor_pack_graphs(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._workflow_editor_pack_graphs(*args, **kwargs)

    def _workflow_editor_stored_graphs(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._workflow_editor_stored_graphs(*args, **kwargs)

    def _workflow_editor_nodes_by_id(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._workflow_editor_nodes_by_id(*args, **kwargs)

    def _workflow_editor_connected_graph_node_ids(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._workflow_editor_connected_graph_node_ids(*args, **kwargs)

    def _workflow_editor_default_graph_name(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._workflow_editor_default_graph_name(*args, **kwargs)

    def _workflow_editor_graph_id_base(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._workflow_editor_graph_id_base(*args, **kwargs)

    def _coerce_workflow_editor_graphs(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._coerce_workflow_editor_graphs(*args, **kwargs)

    def _workflow_editor_graphs_for_config(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._workflow_editor_graphs_for_config(*args, **kwargs)

    def _workflow_editor_graph_payloads_to_persist(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._workflow_editor_graph_payloads_to_persist(
            *args, **kwargs
        )

    def _compose_workflow_editor_config(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._compose_workflow_editor_config(*args, **kwargs)

    def _workflow_editor_pack_backed_save_paths(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._workflow_editor_pack_backed_save_paths(*args, **kwargs)

    def _workflow_editor_pack_payload_to_persist(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._workflow_editor_pack_payload_to_persist(*args, **kwargs)

    def _write_workflow_editor_pack_config(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._write_workflow_editor_pack_config(*args, **kwargs)

    def _validate_selected_workflow_pack_runtime_config(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._validate_selected_workflow_pack_runtime_config(
            *args, **kwargs
        )

    def _write_json_file_atomic(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._write_json_file_atomic(*args, **kwargs)

    def _workflow_generation_request_payload(
        self,
        *,
        description: str,
        directory_path: str,
        catalog: WorkflowEditorCatalogPayload,
        include_current_packs: bool,
        model: str,
    ) -> dict[str, object]:
        return self.workflow_generation_admin._workflow_generation_request_payload(
            description=description,
            directory_path=directory_path,
            catalog=catalog,
            include_current_packs=include_current_packs,
            model=model,
        )

    def _workflow_generation_response_schema(
        self, catalog: WorkflowEditorCatalogPayload
    ) -> dict[str, object]:
        return self.workflow_generation_admin._workflow_generation_response_schema(catalog)

    def _workflow_generation_catalog_context(
        self, catalog: WorkflowEditorCatalogPayload
    ) -> list[dict[str, object]]:
        return self.workflow_generation_admin._workflow_generation_catalog_context(catalog)

    def _workflow_generation_examples(self) -> list[dict[str, object]]:
        return self.workflow_generation_admin._workflow_generation_examples()

    def _sanitize_workflow_generation_value(self, value: object) -> object:
        return self.workflow_generation_admin._sanitize_workflow_generation_value(value)

    def _workflow_generation_response_text(self, payload: object) -> str:
        return self.workflow_generation_admin._workflow_generation_response_text(payload)

    def _workflow_generation_materialize_payload(
        self,
        payload: dict[str, object],
        *,
        directory_path: str,
        catalog: WorkflowEditorCatalogPayload,
    ) -> tuple[
        dict[str, object],
        dict[str, object],
        list[WorkflowEditorGraphMetadata],
        list[str],
    ]:
        return self.workflow_generation_admin._workflow_generation_materialize_payload(
            payload,
            directory_path=directory_path,
            catalog=catalog,
        )

    def _workflow_generation_layout(self, config: dict[str, object]) -> dict[str, object]:
        return self.workflow_generation_admin._workflow_generation_layout(config)

    def _read_composed_workflow_editor_document(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._read_composed_workflow_editor_document(*args, **kwargs)

    def _read_workflow_editor_document(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._read_workflow_editor_document(*args, **kwargs)

    def _communication_target_options(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._communication_target_options(*args, **kwargs)

    def _communication_send_target_options(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._communication_send_target_options(*args, **kwargs)

    def _communication_notification_target_options(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._communication_notification_target_options(
            *args, **kwargs
        )

    def _communication_read_target_options(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._communication_read_target_options(*args, **kwargs)

    def _agent_communication_target_options(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._agent_communication_target_options(*args, **kwargs)

    def _read_workflow_editor_catalog(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._read_workflow_editor_catalog(*args, **kwargs)

    def _validate_workflow_editor_document(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._validate_workflow_editor_document(*args, **kwargs)

    def _save_workflow_editor_document_unlocked(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._save_workflow_editor_document_unlocked(*args, **kwargs)

    def _save_workflow_editor_document(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._save_workflow_editor_document(*args, **kwargs)

    def _seed_new_ticket_created_trigger_cursors(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._seed_new_ticket_created_trigger_cursors(*args, **kwargs)

    def _clear_workflow_run(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._clear_workflow_run(*args, **kwargs)

    def _dismiss_workflow_node_failures(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._dismiss_workflow_node_failures(*args, **kwargs)

    def _resume_workflow_run(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._resume_workflow_run(*args, **kwargs)

    def _workflow_run_store_paths(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._workflow_run_store_paths(*args, **kwargs)

    def _workflow_uses_sqlite_store(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._workflow_uses_sqlite_store(*args, **kwargs)

    def _workflow_sqlite_store_bundle(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._workflow_sqlite_store_bundle(*args, **kwargs)

    def _latest_resumable_failed_workflow_record(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._latest_resumable_failed_workflow_record(*args, **kwargs)

    def _workflow_run_store_bundle(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._workflow_run_store_bundle(*args, **kwargs)

    def _workflow_run_event_records(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._workflow_run_event_records(*args, **kwargs)

    def _workflow_config_snapshot_repository(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._workflow_config_snapshot_repository(*args, **kwargs)

    def _communication_artifact_store(self) -> CommunicationArtifactStore:
        return CommunicationArtifactStore(self.config.runtime_dir)

    def _published_artifact_store(self) -> PublishedArtifactStore:
        return self.artifact_admin.published_artifact_store()

    def _workflow_event_journal(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._workflow_event_journal(*args, **kwargs)

    def _collect_workflow_node_ids(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._collect_workflow_node_ids(*args, **kwargs)

    def _normalize_workflow_editor_config(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._normalize_workflow_editor_config(*args, **kwargs)

    def _nest_runtime_outputs_config(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._nest_runtime_outputs_config(*args, **kwargs)

    def _validate_workflow_editor_layout(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._validate_workflow_editor_layout(*args, **kwargs)

    def _strip_system_managed_workflows(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._strip_system_managed_workflows(*args, **kwargs)

    def _merge_system_managed_workflows(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._merge_system_managed_workflows(*args, **kwargs)

    def _upsert_trigger_config(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._upsert_trigger_config(*args, **kwargs)

    def _delete_trigger_config(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._delete_trigger_config(*args, **kwargs)

    def _upsert_effect_config(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._upsert_effect_config(*args, **kwargs)

    def _delete_effect_config(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._delete_effect_config(*args, **kwargs)

    def _next_local_issue_number(self) -> str:
        return self.issue_git_support.next_local_issue_number()

    def _local_issue_creation_lock(self):
        return self.issue_git_support.local_issue_creation_lock()

    def _canonical_local_issue_id(self, issue_number: str, directory_path: str) -> str:
        return self.issue_git_support.canonical_local_issue_id(issue_number, directory_path)

    def _canonical_issue_id(self, record: LocalIssueRecord) -> str:
        return self.issue_git_support.canonical_issue_id(record)

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
        self.issue_git_support.write_local_issue_yaml(issue, updated_at=updated_at)

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
        return self.issue_git_support.refresh_proxy_cache_from_remote(issue, remote_payload)

    def _proxy_target_for_issue(self, issue: LocalIssueRecord) -> tuple[str, str] | None:
        return self.issue_git_support.proxy_target_for_issue(issue)

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
        model_provider_mode: str | None = None,
        vm_user: str = "agent",
        ttl_hours: int | None = None,
        allowed_contact_ids: list[str] | None = None,
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
            model_provider_mode=model_provider_mode,
            vm_user=vm_user,
            ttl_hours=ttl_hours,
            allowed_contact_ids=allowed_contact_ids,
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
        model_provider_mode: str | None = None,
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
            model_provider_mode=model_provider_mode,
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
        model_provider_mode: str | None = None,
        vm_user: str = "agent",
        ttl_seconds: int | None = None,
        ttl_hours: int | None = None,
        allowed_tools: list[str | ToolName] | None = None,
        tool_argument_policies: dict[str, list[Any]] | None = None,
        allowed_script_names: list[str] | None = None,
        allowed_contact_ids: list[str] | None = None,
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
        web_search_enabled: bool | None = None,
        dotnet: bool = False,
        nuget: bool = False,
        mssql: bool = False,
        mssql_init_root: str | None = None,
        playwright: bool = False,
        codex_skills: list[str] | None = None,
        directory_path: str = "root",
        metadata: dict[str, Any] | None = None,
        allow_existing_session: bool = False,
    ) -> VmActionResult:
        return self.vm_agent_admin.create_vm(
            provider=provider,
            vm_name=vm_name,
            session_id=session_id,
            labels=labels,
            model=model,
            model_provider_mode=model_provider_mode,
            vm_user=vm_user,
            ttl_seconds=ttl_seconds,
            ttl_hours=ttl_hours,
            allowed_tools=allowed_tools,
            tool_argument_policies=tool_argument_policies,
            allowed_script_names=allowed_script_names,
            allowed_contact_ids=allowed_contact_ids,
            discord_channels=discord_channels,
            teams_channel_allowlist=teams_channel_allowlist,
            teams_chat_allowlist=teams_chat_allowlist,
            teams_user_allowlist=teams_user_allowlist,
            email_mailboxes=email_mailboxes,
            email_folders=email_folders,
            communication_file_receive_enabled=communication_file_receive_enabled,
            communication_file_send_enabled=communication_file_send_enabled,
            communication_file_max_bytes=communication_file_max_bytes,
            communication_file_require_approval_for_send=(
                communication_file_require_approval_for_send
            ),
            web_search_enabled=web_search_enabled,
            dotnet=dotnet,
            nuget=nuget,
            mssql=mssql,
            mssql_init_root=mssql_init_root,
            playwright=playwright,
            codex_skills=codex_skills,
            directory_path=directory_path,
            metadata=metadata,
            allow_existing_session=allow_existing_session,
        )

    def resolve_vm_defaults(self, directory_path: str) -> EffectiveVmDefaultsConfig:
        return self.vm_agent_admin.resolve_vm_defaults(directory_path)

    def agent_create_options(self, directory_path: str | None = None) -> dict[str, Any]:
        return self.vm_agent_admin.agent_create_options(directory_path)

    def clone_repo_into_vm(
        self,
        *,
        vm_name: str,
        session_id: str | None = None,
        repo_source: str,
        clone_branch: str | None = None,
        vm_user: str = "agent",
        directory_path: str = "root",
        git_author_name: str | None = None,
        git_author_email: str | None = None,
        git_committer_name: str | None = None,
        git_committer_email: str | None = None,
    ) -> VmActionResult:
        return self.vm_agent_admin.clone_repo_into_vm(
            vm_name=vm_name,
            session_id=session_id,
            repo_source=repo_source,
            clone_branch=clone_branch,
            vm_user=vm_user,
            directory_path=directory_path,
            git_author_name=git_author_name,
            git_author_email=git_author_email,
            git_committer_name=git_committer_name,
            git_committer_email=git_committer_email,
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
        return self.vm_agent_admin.destroy_vm(target)

    def destroy_issue_vm(self, target: str) -> VmActionResult:
        return self.vm_agent_admin.destroy_issue_vm(target)

    def delete_local_issue(self, issue_identifier: str) -> VmActionResult:
        return self.issue_git_admin.delete_local_issue(issue_identifier)


def _admin_service_tool_call_audit_index(service: AdminService) -> Any | None:
    approval_audit_admin = getattr(service, "approval_audit_admin", None)
    if approval_audit_admin is not None:
        return approval_audit_admin.tool_call_audit_index
    return getattr(service, "_tool_call_audit_index", None)


def _set_admin_service_tool_call_audit_index(
    service: AdminService, value: Any | None
) -> None:
    service._tool_call_audit_index = value
    approval_audit_admin = getattr(service, "approval_audit_admin", None)
    if approval_audit_admin is not None:
        approval_audit_admin.tool_call_audit_index = value


def _admin_service_normalize_wake_ledger_statuses(
    statuses: list[str] | None,
) -> list[str] | None:
    from agent_sandbox.admin.facades import SessionAdminFacade

    return SessionAdminFacade._normalize_wake_ledger_statuses(statuses)


def _admin_service_derive_review_branch(agent_branch: str) -> str:
    from agent_sandbox.admin.services.issue_git_service import IssueGitAdminService

    return IssueGitAdminService._derive_review_branch(agent_branch)


def _admin_service_derive_local_pr_notes_ref(remote_url: str) -> str:
    from agent_sandbox.admin.services.issue_git_service import IssueGitAdminService

    return IssueGitAdminService._derive_local_pr_notes_ref(remote_url)


_log_page_from_end = vm_agent_service._log_page_from_end


_log_page_from_start = vm_agent_service._log_page_from_start


def _parse_tool_call_datetime(value: Any) -> datetime | None:
    from agent_sandbox.admin.services.approval_audit_service import parse_tool_call_datetime

    return parse_tool_call_datetime(value)


def _tool_call_directory_matches(
    record_directory_path: str | None,
    selected_directory_path: str | None,
    *,
    direct_only: bool,
) -> bool:
    from agent_sandbox.admin.services.approval_audit_service import tool_call_directory_matches

    return tool_call_directory_matches(
        record_directory_path, selected_directory_path, direct_only=direct_only
    )


def _tool_call_normalize_status(status: Any) -> str | None:
    from agent_sandbox.admin.services.approval_audit_service import tool_call_normalize_status

    return tool_call_normalize_status(status)


def _tool_call_failure_status(event: dict[str, Any]) -> str:
    from agent_sandbox.admin.services.approval_audit_service import tool_call_failure_status

    return tool_call_failure_status(event)


def _approval_audit_payload_matches_request(
    payload: dict[str, Any], request: ApprovalRequest
) -> bool:
    from agent_sandbox.admin.services.approval_audit_service import (
        approval_audit_payload_matches_request,
    )

    return approval_audit_payload_matches_request(payload, request)


def _compact_payload_summary(value: Any, *, max_length: int = 180) -> str | None:
    from agent_sandbox.admin.services.approval_audit_service import compact_payload_summary

    return compact_payload_summary(value, max_length=max_length)


def _approval_review_blocked_tool_call_diagnostics(
    audit_context: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    from agent_sandbox.admin.services.approval_audit_service import (
        approval_review_blocked_tool_call_diagnostics,
    )

    return approval_review_blocked_tool_call_diagnostics(audit_context)


def build_approval_review_payload(
    request: ApprovalRequest, audit_context: list[dict[str, Any]]
) -> dict[str, Any]:
    from agent_sandbox.admin.services.approval_audit_service import (
        build_approval_review_payload as _build_approval_review_payload,
    )

    return _build_approval_review_payload(request, audit_context)


def _tool_call_error_summary(
    entry: dict[str, Any], status: str, result: dict[str, Any]
) -> str | None:
    from agent_sandbox.admin.services.approval_audit_service import tool_call_error_summary

    return tool_call_error_summary(entry, status, result)


def _tool_call_safe_text(value: Any) -> str:
    from agent_sandbox.admin.services.approval_audit_service import tool_call_safe_text

    return tool_call_safe_text(value)


def _tool_call_related_links(
    entry: ToolCallAuditEntryPayload,
) -> list[ToolCallAuditRelatedLinkPayload]:
    from agent_sandbox.admin.services.approval_audit_service import tool_call_related_links

    return tool_call_related_links(entry)


def _tool_call_audit_entry_from_payload(
    payload: dict[str, Any],
) -> ToolCallAuditEntryPayload | None:
    from agent_sandbox.admin.services.approval_audit_service import (
        tool_call_audit_entry_from_payload,
    )

    return tool_call_audit_entry_from_payload(payload)


def _tool_call_audit_tail_scan_supported(
    *,
    session_id: str | None,
    tool_name: str | None,
    status: str | None,
    created_after: datetime | None,
    created_before: datetime | None,
    q: str | None,
) -> bool:
    from agent_sandbox.admin.services.approval_audit_service import (
        tool_call_audit_tail_scan_supported,
    )

    return tool_call_audit_tail_scan_supported(
        session_id=session_id,
        tool_name=tool_name,
        status=status,
        created_after=created_after,
        created_before=created_before,
        q=q,
    )


def _tool_call_recent_scan_max_lines(limit: int) -> int:
    from agent_sandbox.admin.services.approval_audit_service import tool_call_recent_scan_max_lines

    return tool_call_recent_scan_max_lines(limit)


def _iter_file_lines_reverse(path: Path, *, block_size: int = 64 * 1024) -> Iterator[str]:
    from agent_sandbox.admin.services.approval_audit_service import iter_file_lines_reverse

    return iter_file_lines_reverse(path, block_size=block_size)


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
    from agent_sandbox.admin.services.approval_audit_service import (
        tool_call_audit_entry_matches_filters,
    )

    return tool_call_audit_entry_matches_filters(
        entry,
        session_id=session_id,
        tool_name=tool_name,
        status=status,
        directory_path=directory_path,
        direct_only=direct_only,
        created_after=created_after,
        created_before=created_before,
        q=q,
    )
