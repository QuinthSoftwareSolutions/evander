export type WorkflowNodeKind = "trigger" | "gate" | "effect" | "query";

export type WorkflowNodeConfig = {
  id: string;
  display_name: string;
  type: string;
  [key: string]: unknown;
};

export type WorkflowEdge = {
  source_id: string;
  target_id: string;
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

export type WorkflowEditorDocument = {
  config_path: string;
  layout_path: string;
  config_exists: boolean;
  layout_exists: boolean;
  config: WorkflowConfig;
  layout: WorkflowLayout;
};

export type WorkflowEditorValidation = {
  config: WorkflowConfig;
  layout: WorkflowLayout;
};

export type WorkflowEditorFieldOption = {
  value: string;
  label: string;
};

export type WorkflowEditorFieldDefinition = {
  key: string;
  label: string;
  kind: "string" | "number" | "boolean" | "enum" | "string_list" | "json" | "object_list";
  required: boolean;
  bindable: boolean;
  read_only: boolean;
  multiline: boolean;
  description?: string | null;
  placeholder?: string | null;
  options: WorkflowEditorFieldOption[];
  item_fields: WorkflowEditorFieldDefinition[];
};

export type WorkflowEditorNodeCatalogEntry = {
  kind: WorkflowNodeKind;
  type: string;
  label: string;
  fields: WorkflowEditorFieldDefinition[];
};

export type WorkflowEditorCatalog = {
  worker_id: string;
  nodes: WorkflowEditorNodeCatalogEntry[];
};
