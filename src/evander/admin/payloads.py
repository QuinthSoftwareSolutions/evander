from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator

from evander.capacity_health import (
    DiskUsageTelemetry,
    DirectorySizeTelemetry,
    HealthState,
    SessionStorageSample,
    TelemetryError,
    TelemetryStatus,
)
from evander.models import DirectoryPath
from evander.published_artifacts import PublishedArtifactMetadata
from evander.worker.models import WorkerTelemetryPayload


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
    revision: str | None = None
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
    pack_list_revision: str | None = None


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
    pack_list_revision: str | None = None


class WorkflowEditorPackMutationPayload(BaseModel):
    worker_id: str = "trigger-orchestrator"
    config_path: str
    workflow_packs: list[WorkflowEditorWorkflowPackDescriptor] = Field(default_factory=list)
    selected_workflow_pack: WorkflowEditorWorkflowPackDescriptor | None = None
    revision: str | None = None
    document: WorkflowEditorDocumentPayload


class WorkflowPackActorPayload(BaseModel):
    login: str
    display_name: str | None = None


class WorkflowPackHistoryEntryPayload(BaseModel):
    change_id: str
    operation: str
    workflow_pack: str
    actor: WorkflowPackActorPayload
    changed_at: datetime
    before_revision: str | None = None
    after_revision: str | None = None
    touched_paths: list[str] = Field(default_factory=list)
    summary: dict[str, object] = Field(default_factory=dict)


class WorkflowPackHistoryListPayload(BaseModel):
    entries: list[WorkflowPackHistoryEntryPayload] = Field(default_factory=list)
    next_cursor: str | None = None


class WorkflowPackHistoryFilePayload(BaseModel):
    label: str
    path: str
    exists: bool
    revision: str | None = None
    content: dict[str, object] | None = None
    error: str | None = None


class WorkflowPackHistoryDiffPayload(BaseModel):
    label: str
    path: str
    changes: list[dict[str, object]] = Field(default_factory=list)
    truncated: bool = False


class WorkflowPackHistoryDetailPayload(BaseModel):
    entry: WorkflowPackHistoryEntryPayload
    before_files: list[WorkflowPackHistoryFilePayload] = Field(default_factory=list)
    after_files: list[WorkflowPackHistoryFilePayload] = Field(default_factory=list)
    diffs: list[WorkflowPackHistoryDiffPayload] = Field(default_factory=list)


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
    connector_id: str | None = None
    connector_operation: str | None = None
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
    connector_id: str | None = None
    connector_operation: str | None = None
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
