export type WorkflowNodeKind = "trigger" | "gate" | "effect" | "query";

export type WorkflowNodeConfig = {
  id: string;
  display_name: string;
  type: string;
  config: Record<string, unknown>;
  outputs?: Record<string, unknown> | null;
};

export type WorkflowEdge = {
  source_id: string;
  source_port?: string;
  target_id: string;
  allow_cycle?: boolean;
};

export type WorkflowConfig = {
  runtime_dir?: string;
  poll_interval_seconds?: number;
  jitter_seconds?: number;
  lock_path?: string | null;
  event_queue_store_path?: string | null;
  event_queue_cursor_store_path?: string | null;
  gate_execution_store_path?: string | null;
  gate_cursor_store_path?: string | null;
  effect_execution_store_path?: string | null;
  effect_cursor_store_path?: string | null;
  effect_lock_path?: string | null;
  triggers: WorkflowNodeConfig[];
  gates: WorkflowNodeConfig[];
  effects: WorkflowNodeConfig[];
  queries: WorkflowNodeConfig[];
  edges: WorkflowEdge[];
  [key: string]: unknown;
};

export type WorkflowLayout = {
  nodes: Record<string, { x: number; y: number }>;
};

export type WorkflowGraphMetadata = {
  id: string;
  name: string;
  description?: string | null;
  directory_path: string;
  directory_paths?: string[];
  user_ids: string[];
  node_ids: string[];
};

export type WorkflowOutputSchemas = Record<string, Record<string, unknown>>;

export type WorkflowPackDescriptor = {
  reference: string;
  path: string;
  layout_path: string;
  exists: boolean;
  editable: boolean;
  display_name: string;
  revision?: string | null;
  id?: string | null;
  version?: string | null;
  description?: string | null;
  graph_count: number;
  node_count: number;
  selected?: boolean;
};

export type WorkflowEditorDocument = {
  config_path: string;
  layout_path: string;
  config_exists: boolean;
  layout_exists: boolean;
  document_scope?: "pack" | "inline" | "composed" | string;
  workflow_packs?: WorkflowPackDescriptor[];
  selected_workflow_pack?: WorkflowPackDescriptor | null;
  config: WorkflowConfig;
  layout: WorkflowLayout;
  graphs: WorkflowGraphMetadata[];
  output_schemas?: WorkflowOutputSchemas;
  validation_diagnostics?: WorkflowValidationDiagnostic[];
  revision: string;
  pack_list_revision?: string | null;
};

export type WorkflowEditorValidation = {
  config: WorkflowConfig;
  layout: WorkflowLayout;
  graphs: WorkflowGraphMetadata[];
  output_schemas?: WorkflowOutputSchemas;
  validation_diagnostics: WorkflowValidationDiagnostic[];
};

export type WorkflowEditorSave = {
  document_scope?: "pack" | "inline" | "composed" | string;
  workflow_packs?: WorkflowPackDescriptor[];
  selected_workflow_pack?: WorkflowPackDescriptor | null;
  config: WorkflowConfig;
  layout: WorkflowLayout;
  graphs: WorkflowGraphMetadata[];
  output_schemas?: WorkflowOutputSchemas;
  validation_diagnostics: WorkflowValidationDiagnostic[];
  revision: string;
  pack_list_revision?: string | null;
};

export type WorkflowPackMutation = {
  worker_id: string;
  config_path: string;
  workflow_packs: WorkflowPackDescriptor[];
  selected_workflow_pack?: WorkflowPackDescriptor | null;
  revision?: string | null;
  document: WorkflowEditorDocument;
};

export type WorkflowPackActor = {
  login: string;
  display_name?: string | null;
};

export type WorkflowPackHistoryEntry = {
  change_id: string;
  operation: string;
  workflow_pack: string;
  actor: WorkflowPackActor;
  changed_at: string;
  before_revision?: string | null;
  after_revision?: string | null;
  touched_paths: string[];
  summary: Record<string, unknown>;
};

export type WorkflowPackHistoryList = {
  entries: WorkflowPackHistoryEntry[];
  next_cursor?: string | null;
};

export type WorkflowPackHistoryFile = {
  label: string;
  path: string;
  exists: boolean;
  revision?: string | null;
  content?: Record<string, unknown> | null;
  error?: string | null;
};

export type WorkflowPackHistoryDiff = {
  label: string;
  path: string;
  changes: Record<string, unknown>[];
  truncated: boolean;
};

export type WorkflowPackHistoryDetail = {
  entry: WorkflowPackHistoryEntry;
  before_files: WorkflowPackHistoryFile[];
  after_files: WorkflowPackHistoryFile[];
  diffs: WorkflowPackHistoryDiff[];
};

export type WorkflowEditorGeneratedDraft = {
  worker_id: string;
  model: string;
  config: WorkflowConfig;
  layout: WorkflowLayout;
  graphs: WorkflowGraphMetadata[];
  output_schemas?: WorkflowOutputSchemas;
  validation_diagnostics: WorkflowValidationDiagnostic[];
  warnings: string[];
};

export type WorkflowValidationDiagnostic = {
  code: string;
  message: string;
  severity?: "error" | "warning" | string;
  node_id?: string | null;
  node_type?: string | null;
  field_path?: string | null;
  reference?: string | null;
};

export type WorkflowEditorFieldOption = {
  value: string;
  label: string;
  description?: string | null;
  metadata?: Record<string, unknown>;
};

export type WorkflowInternalEventCatalogEntry = {
  event_type: string;
  label: string;
  description?: string | null;
  source?: string | null;
  metadata?: Record<string, unknown>;
};

export type WorkflowEditorFieldDefinition = {
  key: string;
  label: string;
  kind: "string" | "number" | "boolean" | "enum" | "string_list" | "string_map" | "json" | "object_list";
  required: boolean;
  bindable: boolean;
  read_only: boolean;
  multiline: boolean;
  description?: string | null;
  placeholder?: string | null;
  json_value_type?: "object" | "array" | "string" | "number" | "boolean" | "null" | "any" | string | null;
  default_value?: unknown | null;
  options: WorkflowEditorFieldOption[];
  item_fields: WorkflowEditorFieldDefinition[];
  metadata?: Record<string, unknown>;
};

export type WorkflowEditorNodePortDefinition = {
  key: string;
  label: string;
  description?: string | null;
};

export type WorkflowEditorNodeCatalogEntry = {
  kind: WorkflowNodeKind;
  type: string;
  label: string;
  description?: string | null;
  fields: WorkflowEditorFieldDefinition[];
  input_ports: WorkflowEditorNodePortDefinition[];
  output_ports: WorkflowEditorNodePortDefinition[];
};

export type WorkflowEditorCatalog = {
  worker_id: string;
  nodes: WorkflowEditorNodeCatalogEntry[];
  internal_events: WorkflowInternalEventCatalogEntry[];
};

export type WorkflowRunNodeActivity = {
  node_id: string;
  status: string;
  count: number;
  source?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  dispatch_id?: string | null;
  error?: string | null;
};

export type WorkflowRunRecentNodeActivity = {
  activity_key: string;
  event_id: string;
  node_id: string;
  status: string;
  source: string;
  activity_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  trace_id?: string | null;
  error?: string | null;
};

export type WorkflowRunSummary = {
  event_id: string;
  run_state: string;
  trace_id?: string | null;
  source_node_id?: string | null;
  source_trigger_type?: string | null;
  delivery_state?: string | null;
  occurred_at?: string | null;
  latest_activity_at?: string | null;
  active_nodes: WorkflowRunNodeActivity[];
  failed_node_id?: string | null;
  failed_node_status?: string | null;
  failed_at?: string | null;
  error?: string | null;
  resumable?: boolean;
};

export type WorkflowNodeActivityCounts = Record<string, number>;

export type WorkflowRunSnapshot = {
  worker_id: string;
  generated_at: string;
  runs: WorkflowRunSummary[];
  node_activity: Record<string, WorkflowNodeActivityCounts>;
  recent_node_activity?: WorkflowRunRecentNodeActivity[];
};

export type WorkflowRunSearchFilter = {
  node_id?: string | null;
  graph_id?: string | null;
  graph_name?: string | null;
  without_session: boolean;
  include_completed: boolean;
};

export type WorkflowRunSearchItem = {
  event_id: string;
  trace_id?: string | null;
  run_state: string;
  source_node_id?: string | null;
  source_trigger_type?: string | null;
  delivery_state?: string | null;
  occurred_at?: string | null;
  latest_activity_at?: string | null;
  node_ids: string[];
  matched_node_ids: string[];
  session_ids: string[];
  has_agent_session: boolean;
  status_counts: Record<string, number>;
  nodes: WorkflowRunNodeActivity[];
  error?: string | null;
};

export type WorkflowRunSearch = {
  worker_id: string;
  generated_at: string;
  filters: WorkflowRunSearchFilter;
  runs: WorkflowRunSearchItem[];
  matched: number;
  returned: number;
  limit: number;
  has_more: boolean;
};

export type WorkflowRunDataAvailability = {
  graph_snapshot: string;
  binding_trace: string;
  approvals: string;
  agent_links: string;
  communication_artifacts: string;
  tool_calls: string;
  trigger_event: string;
};

export type WorkflowRunGraphSnapshot = {
  revision: string;
  captured_at: string;
  snapshot_type?: string | null;
  source?: string | null;
  node_count: number;
  edge_count: number;
  triggers: WorkflowNodeConfig[];
  gates: WorkflowNodeConfig[];
  effects: WorkflowNodeConfig[];
  queries: WorkflowNodeConfig[];
  edges: Array<WorkflowEdge | { source?: string; target?: string; source_id?: string; target_id?: string; source_port?: string | null }>;
  workflow_graphs?: unknown;
};

export type WorkflowRunGraph = {
  config_revision?: string | null;
  config: WorkflowConfig;
  snapshot?: WorkflowRunGraphSnapshot | null;
  data_availability: WorkflowRunDataAvailability;
};

export type WorkflowRunApprovalLink = {
  request_id: string;
  session_id?: string | null;
  action_type: string;
  tool_name?: string | null;
  directory_path: string;
  payload_digest: string;
  status: string;
  approver?: string | null;
  decision_at?: string | null;
  decision_reason?: string | null;
  expires_at?: string | null;
  human_summary?: string | null;
};

export type WorkflowRunToolCallLink = {
  tool_name: string;
  created_at?: string | null;
  status?: string | null;
  decision?: string | null;
  request_id?: string | null;
  correlation_id?: string | null;
  approval_request_id?: string | null;
  workflow_run_id?: string | null;
  trace_id?: string | null;
  session_id?: string | null;
  node_id?: string | null;
  node_type?: string | null;
  args_summary?: string | null;
  result_summary?: string | null;
  args_missing_reason?: string | null;
  result_status?: string | null;
  result_missing_reason?: string | null;
  result_empty?: boolean | null;
  args?: Record<string, unknown>;
  result?: Record<string, unknown>;
  redacted_args?: unknown;
  redacted_result?: unknown;
  href?: string | null;
  filter?: Record<string, unknown> | null;
};

export type WorkflowRunAgentLink = {
  node_id: string;
  node_type?: string | null;
  session_id?: string | null;
  vm_name?: string | null;
  agent_id?: string | null;
  backend?: string | null;
  agent_backend?: string | null;
  target_name?: string | null;
  agent_available?: boolean | null;
  agent_status?: string | null;
  agent_missing_reason?: string | null;
  session_available?: boolean | null;
  session_status?: string | null;
  session_missing_reason?: string | null;
  session_href?: string | null;
  log_available?: boolean | null;
  log_status?: string | null;
  log_missing_reason?: string | null;
  log_href?: string | null;
  log_reference_href?: string | null;
  prompt?: string | null;
  prompt_summary?: string | null;
  prompt_status?: string | null;
  prompt_missing_reason?: string | null;
  result_summary?: string | null;
  result_status?: string | null;
  result_missing_reason?: string | null;
  result_empty?: boolean | null;
  dispatch_id?: string | null;
  thread_id?: string | null;
  execution_id?: string | null;
  artifact_ids: string[];
  status?: string | null;
  waiting?: boolean | null;
  completed?: boolean | null;
  log_reference?: Record<string, unknown> | null;
};

export type WorkflowRunCommunicationArtifactLink = {
  artifact_id: string;
  session_id?: string | null;
  provider?: string | null;
  original_filename?: string | null;
  filename?: string | null;
  mime_type?: string | null;
  byte_count?: number | null;
  sha256?: string | null;
  status?: string | null;
  metadata: Record<string, unknown>;
};

export type WorkflowRunNodeRecord = {
  record_kind: string;
  execution_id?: string | null;
  event_id?: string | null;
  trace_id?: string | null;
  node_id?: string | null;
  node_type?: string | null;
  status?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  duration_ms?: number | null;
  input_payload: Record<string, unknown>;
  outputs: Record<string, unknown>;
  resolved_config: Record<string, unknown>;
  binding_trace?: Record<string, unknown> | null;
  binding_error?: string | null;
  error?: string | null;
  config_revision?: string | null;
};

export type WorkflowRunNodeDetail = {
  node_id: string;
  node_type?: string | null;
  display_name?: string | null;
  source_kind?: string | null;
  execution_id?: string | null;
  event_id?: string | null;
  trace_id?: string | null;
  status?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  duration_ms?: number | null;
  occurred_at?: string | null;
  delivery_state?: string | null;
  resolved_config: Record<string, unknown>;
  binding_trace?: Record<string, unknown> | null;
  binding_error?: string | null;
  input_payload: Record<string, unknown>;
  outputs: Record<string, unknown>;
  inputs_by_source: Record<string, Record<string, unknown>>;
  error?: string | null;
  published_event_id?: string | null;
  completion_event_enqueued_at?: string | null;
  config_revision?: string | null;
  retry_resume_eligible: boolean;
  retry_resume_reason?: string | null;
  missing_fields: string[];
  tool_call_links: WorkflowRunToolCallLink[];
  approval_links: WorkflowRunApprovalLink[];
  agent_links: WorkflowRunAgentLink[];
  communication_artifact_links: WorkflowRunCommunicationArtifactLink[];
  records: WorkflowRunNodeRecord[];
};

export type WorkflowRunRecoveryAction = {
  eligible: boolean;
  reason?: string | null;
  event_id?: string | null;
  target_node_id?: string | null;
  source_node_id?: string | null;
  source_trigger_type?: string | null;
  source_event_id?: string | null;
};

export type WorkflowRunDetail = WorkflowRunSummary & {
  worker_id: string;
  generated_at: string;
  matched_by: string[];
  lookup_candidates: Array<{
    event_id: string;
    trace_id?: string | null;
    latest_activity_at?: string | null;
    matched_by: string[];
  }>;
  recovery: {
    clear: WorkflowRunRecoveryAction;
    resume: WorkflowRunRecoveryAction;
  };
  trigger_event?: {
    event_id: string;
    trigger_id: string;
    trigger_type: string;
    trace_id: string;
    occurred_at?: string | null;
    delivery_state?: string | null;
    payload: Record<string, unknown>;
  } | null;
  graph: WorkflowRunGraph;
  tool_call_links?: WorkflowRunToolCallLink[];
  nodes: WorkflowRunNodeDetail[];
  data_availability: WorkflowRunDataAvailability;
};
