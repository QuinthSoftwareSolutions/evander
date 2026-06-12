import { adminHash } from "../../adminRouting.ts";

export const WORKFLOW_FAILURE_CLUSTERS_REPORT_ENDPOINT =
  "/api/admin/reports/workflow-failure-clusters";

export type WorkflowFailureClusterState = "active" | "recent" | "resolved" | "historical";
export type WorkflowFailureClusterSourceKind = "trigger" | "effect" | "gate";

export type WorkflowFailureClusterExamplePayload = {
  event_id: string | null;
  trace_id: string | null;
  session_id: string | null;
  occurred_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  error_summary: string | null;
  run_state: string | null;
  run_detail_href: string | null;
  api_run_detail_href: string | null;
  artifact_ids: string[];
};

export type WorkflowFailureClusterPayload = {
  cluster_id: string;
  graph_id: string | null;
  graph_name: string | null;
  graph_description: string | null;
  directory_path: string | null;
  node_id: string | null;
  node_display_name: string | null;
  node_type: string | null;
  trigger_type: string | null;
  effect_type: string | null;
  gate_type: string | null;
  source_kind: WorkflowFailureClusterSourceKind;
  error_signature: string;
  error_signature_key: string;
  count: number;
  first_seen_at: string | null;
  last_seen_at: string | null;
  latest_error_summary: string | null;
  state: WorkflowFailureClusterState;
  event_ids: string[];
  trace_ids: string[];
  session_ids: string[];
  pr_ids: string[];
  ado_item_ids: string[];
  issue_ids: string[];
  artifact_ids: string[];
  examples: WorkflowFailureClusterExamplePayload[];
};

export type WorkflowFailureClusterReportPayload = {
  worker_id: string;
  generated_at: string;
  period_start: string;
  period_end: string;
  recent_threshold_seconds: number | null;
  limit: number;
  has_more: boolean;
  clusters: WorkflowFailureClusterPayload[];
};

const STATE_LABELS: Record<WorkflowFailureClusterState, string> = {
  active: "Active",
  recent: "Recent",
  resolved: "Resolved",
  historical: "Historical",
};

function humanizeStatus(value: string): string {
  return value
    .replace(/[_-]+/g, " ")
    .trim()
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function stateLabel(state: WorkflowFailureClusterState): string {
  return STATE_LABELS[state] ?? humanizeStatus(state);
}

export function stateClassName(state: WorkflowFailureClusterState): string {
  return `state-${state}`;
}

export function selectWorkflowFailureCluster(
  clusters: ReadonlyArray<WorkflowFailureClusterPayload>,
  selectedClusterId: string,
): WorkflowFailureClusterPayload | null {
  return (
    clusters.find((cluster) => cluster.cluster_id === selectedClusterId) ??
    clusters[0] ??
    null
  );
}

export function workflowFailureClusterNodeType(
  cluster: WorkflowFailureClusterPayload,
): string | null {
  return cluster.node_type ?? cluster.trigger_type ?? cluster.effect_type ?? cluster.gate_type ?? null;
}

export function workflowFailureClusterRepresentativeRunHref(
  example: WorkflowFailureClusterExamplePayload,
): string | null {
  if (example.event_id) {
    return adminHash("workflow", { event_id: example.event_id });
  }
  if (example.trace_id) {
    return adminHash("workflow", { trace_id: example.trace_id });
  }
  return null;
}

export function workflowFailureClusterRepresentativeRunLabel(
  example: WorkflowFailureClusterExamplePayload,
): string {
  if (example.event_id) {
    return `Event ${example.event_id}`;
  }
  if (example.trace_id) {
    return `Trace ${example.trace_id}`;
  }
  return "Run detail";
}
