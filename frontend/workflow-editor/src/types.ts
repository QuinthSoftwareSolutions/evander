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
