export type WorkflowNodeKind = "trigger" | "gate" | "effect";

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
