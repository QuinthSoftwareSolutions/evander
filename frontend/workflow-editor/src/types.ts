export type WorkflowNodeKind = "trigger" | "gate" | "effect" | "query";

export type WorkflowNodeConfig = {
  id: string;
  display_name: string;
  type: string;
  [key: string]: unknown;
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
  directory_paths: string[];
  user_ids: string[];
  node_ids: string[];
};

export type WorkflowOutputSchemas = Record<string, Record<string, unknown>>;

export type WorkflowEditorDocument = {
  config_path: string;
  layout_path: string;
  config_exists: boolean;
  layout_exists: boolean;
  config: WorkflowConfig;
  layout: WorkflowLayout;
  graphs: WorkflowGraphMetadata[];
  output_schemas?: WorkflowOutputSchemas;
  validation_diagnostics?: WorkflowValidationDiagnostic[];
};

export type WorkflowEditorValidation = {
  config: WorkflowConfig;
  layout: WorkflowLayout;
  graphs: WorkflowGraphMetadata[];
  output_schemas?: WorkflowOutputSchemas;
  validation_diagnostics: WorkflowValidationDiagnostic[];
};

export type WorkflowEditorSave = {
  config: WorkflowConfig;
  layout: WorkflowLayout;
  graphs: WorkflowGraphMetadata[];
  output_schemas?: WorkflowOutputSchemas;
  validation_diagnostics: WorkflowValidationDiagnostic[];
};

export type WorkflowValidationDiagnostic = {
  code: string;
  message: string;
  node_id?: string | null;
  node_type?: string | null;
  field_path?: string | null;
  reference?: string | null;
};

export type WorkflowEditorFieldOption = {
  value: string;
  label: string;
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
  options: WorkflowEditorFieldOption[];
  item_fields: WorkflowEditorFieldDefinition[];
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
};

export type WorkflowRunDataAvailability = {
  graph_snapshot: string;
  binding_trace: string;
  approvals: string;
  agent_links: string;
  communication_artifacts: string;
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

export type WorkflowRunAgentLink = {
  node_id: string;
  node_type?: string | null;
  session_id?: string | null;
  vm_name?: string | null;
  prompt?: string | null;
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
  nodes: WorkflowRunNodeDetail[];
  data_availability: WorkflowRunDataAvailability;
};
