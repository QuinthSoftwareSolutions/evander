import { hashQueryParam } from "../../adminRouting";
import {
  type WorkflowConfig,
  type WorkflowEdge,
  type WorkflowGraphMetadata,
  type WorkflowLayout,
  type WorkflowNodeConfig,
  type WorkflowNodeExecutionFilter,
  type WorkflowNodeKind,
  type WorkflowRunDetail,
  type WorkflowRunNodeActivity,
  type WorkflowRunNodeDetail,
} from "../../types";
import { sameGraphNodeIdsForRunAnchors } from "../../workflowRunInspect";
import { type WorkflowNodeExecutionFilterDraft, type WorkflowRunDetailLookupKind } from "./editorTypes";
import { formatJson, isRecord } from "./editorValues";
import { fallbackPosition, workflowNodes } from "./graphModel";

export function formatRuntimeTimestamp(value: string | null | undefined): string {
  if (!value) return "Unknown time";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

export function summarizeRunNode(activity: WorkflowRunNodeActivity): string {
  const countLabel = activity.count > 1 ? ` x${activity.count}` : "";
  const errorLabel = activity.error ? `: ${activity.error}` : "";
  return `${activity.node_id} ${activity.status}${countLabel}${errorLabel}`;
}

export function workflowRunDetailLookupQuery(value: string, kind: WorkflowRunDetailLookupKind): URLSearchParams {
  const normalized = value.trim();
  const query = new URLSearchParams();
  if (kind !== "auto") {
    query.set(kind, normalized);
  } else if (normalized.startsWith("comm_")) {
    query.set("artifact_id", normalized);
  } else if (normalized.startsWith("trace")) {
    query.set("trace_id", normalized);
  } else if (normalized.startsWith("session")) {
    query.set("session_id", normalized);
  } else {
    query.set("event_id", normalized);
  }
  return query;
}

export function workflowRunLookupFromHash(): { kind: WorkflowRunDetailLookupKind; value: string } | null {
  if (typeof window === "undefined") {
    return null;
  }
  const hash = window.location.hash;
  const runId = hashQueryParam(hash, "run_id");
  if (runId) {
    return { kind: "run_id", value: runId };
  }
  const eventId = hashQueryParam(hash, "event_id");
  if (eventId) {
    return { kind: "event_id", value: eventId };
  }
  const traceId = hashQueryParam(hash, "trace_id");
  if (traceId) {
    return { kind: "trace_id", value: traceId };
  }
  return null;
}

export function parseWorkflowRunSearchScope(value: string): { kind: "graph" | "node"; id: string } | null {
  const separatorIndex = value.indexOf(":");
  if (separatorIndex < 0) return null;
  const kind = value.slice(0, separatorIndex);
  const id = value.slice(separatorIndex + 1).trim();
  if (!id || (kind !== "graph" && kind !== "node")) return null;
  return { kind, id };
}

export function workflowRunSearchQuery(
  scopeValue: string,
  options: {
    withoutSession: boolean;
    includeCompleted: boolean;
    limit: number;
  },
): URLSearchParams | null {
  const { withoutSession, includeCompleted, limit } = options;
  const scope = parseWorkflowRunSearchScope(scopeValue);
  if (!scope) return null;
  const query = new URLSearchParams({
    without_session: withoutSession ? "true" : "false",
    include_completed: includeCompleted ? "true" : "false",
    limit: String(limit),
  });
  query.set(scope.kind === "graph" ? "graph_id" : "node_id", scope.id);
  return query;
}

export function workflowNodeExecutionLocalTimestamp(value: Date): string {
  const local = new Date(value.getTime() - value.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 23);
}

export function workflowNodeExecutionDefaultWindow(now = new Date()): { from: string; to: string } {
  return {
    from: workflowNodeExecutionLocalTimestamp(new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000)),
    to: workflowNodeExecutionLocalTimestamp(now),
  };
}

export function workflowNodeExecutionDefaultStart(): string {
  return workflowNodeExecutionDefaultWindow().from;
}

export function workflowNodeExecutionDefaultEnd(): string {
  return workflowNodeExecutionDefaultWindow().to;
}

export function workflowNodeExecutionFilterValue(draft: WorkflowNodeExecutionFilterDraft): unknown {
  if (draft.valueType === "null") return null;
  if (draft.valueType === "boolean") return draft.value.trim().toLowerCase() === "true";
  if (draft.valueType === "number") {
    const numericText = draft.value.trim();
    if (!numericText) throw new Error("Execution numeric filter values must not be blank.");
    const numericValue = Number(numericText);
    if (!Number.isFinite(numericValue)) {
      throw new Error("Execution numeric filter values must be finite.");
    }
    if (Number.isInteger(numericValue) && !Number.isSafeInteger(numericValue)) {
      throw new Error("Execution integer filter values must be within JavaScript's safe range.");
    }
    return numericValue;
  }
  return draft.value;
}

export function workflowNodeExecutionFilters(
  drafts: WorkflowNodeExecutionFilterDraft[],
): WorkflowNodeExecutionFilter[] {
  return drafts.flatMap((draft) => {
    const path = draft.path.trim();
    if (!path) return [];
    if (!path.startsWith("/")) {
      throw new Error("Execution filter paths must be JSON Pointers beginning with '/'.");
    }
    return [{
      location: draft.location,
      path,
      operator: draft.operator,
      ...(!["exists", "not_exists"].includes(draft.operator)
        ? { value: workflowNodeExecutionFilterValue(draft) }
        : {}),
    }];
  });
}

export function summarizeRunSearchStatuses(counts: Record<string, number>): string {
  const entries = Object.entries(counts).filter(([, count]) => count > 0);
  if (entries.length === 0) return "No recorded node status";
  return entries.map(([status, count]) => `${status} ${count}`).join(", ");
}

export function workflowRunStateLabel(runState: string | null | undefined, fallbackState: string | null | undefined = null): string {
  return runState ?? fallbackState ?? "active";
}

export function workflowRunStateClassName(
  runState: string | null | undefined,
  fallbackState: string | null | undefined = null,
): string {
  const state = workflowRunStateLabel(runState, fallbackState);
  return state === "errored" ? "failed" : state;
}

export function workflowRunDetailGraphNodes(detail: WorkflowRunDetail): Array<WorkflowNodeConfig & { kind: WorkflowNodeKind }> {
  const snapshot = detail.graph.snapshot;
  const source = snapshot
    ? {
        triggers: snapshot.triggers ?? [],
        gates: snapshot.gates ?? [],
        effects: snapshot.effects ?? [],
        queries: snapshot.queries ?? [],
      }
    : detail.graph.config;
  return [
    ...(source.triggers ?? []).map((node) => ({ ...node, kind: "trigger" as const })),
    ...(source.gates ?? []).map((node) => ({ ...node, kind: "gate" as const })),
    ...(source.effects ?? []).map((node) => ({ ...node, kind: "effect" as const })),
    ...(source.queries ?? []).map((node) => ({ ...node, kind: "query" as const })),
  ].filter((node) => Boolean(node.id));
}

export function workflowRunDetailGraphEdges(detail: WorkflowRunDetail): WorkflowEdge[] {
  const rawEdges = detail.graph.snapshot?.edges ?? detail.graph.config.edges ?? [];
  return rawEdges.flatMap((edge) => {
    const rawEdge = edge as Record<string, unknown>;
    const source = typeof rawEdge.source_id === "string" ? rawEdge.source_id : typeof rawEdge.source === "string" ? rawEdge.source : "";
    const target = typeof rawEdge.target_id === "string" ? rawEdge.target_id : typeof rawEdge.target === "string" ? rawEdge.target : "";
    if (!source || !target) return [];
    return [{
      source_id: source,
      target_id: target,
      source_port: typeof rawEdge.source_port === "string" ? rawEdge.source_port : undefined,
    }];
  });
}

export function workflowRunDetailNodeById(detail: WorkflowRunDetail): Map<string, WorkflowRunNodeDetail> {
  return new Map(detail.nodes.map((node) => [node.node_id, node]));
}

export function workflowRunDetailVisitedNodeIds(detail: WorkflowRunDetail): Set<string> {
  const nodeIds = new Set<string>();
  detail.nodes.forEach((node) => {
    if (node.node_id) nodeIds.add(node.node_id);
  });
  detail.active_nodes.forEach((activity) => {
    if (activity.node_id) nodeIds.add(activity.node_id);
  });
  if (detail.source_node_id) nodeIds.add(detail.source_node_id);
  if (detail.trigger_event?.trigger_id) nodeIds.add(detail.trigger_event.trigger_id);
  if (detail.failed_node_id) nodeIds.add(detail.failed_node_id);
  return nodeIds;
}

export function workflowRunDetailIsFailed(detail: WorkflowRunDetail): boolean {
  if (detail.run_state === "error_routed") return false;
  return Boolean(
    detail.run_state === "errored" ||
      detail.delivery_state === "failed" ||
      detail.failed_node_id ||
      detail.failed_node_status === "failed",
  );
}

export function coerceWorkflowGraphMetadataList(value: unknown): WorkflowGraphMetadata[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((entry) => {
    if (!isRecord(entry)) return [];
    const id = typeof entry.id === "string" ? entry.id.trim() : "";
    const name = typeof entry.name === "string" ? entry.name.trim() : "";
    const nodeIds = Array.isArray(entry.node_ids)
      ? entry.node_ids.filter((nodeId): nodeId is string => typeof nodeId === "string" && Boolean(nodeId.trim()))
      : [];
    if (!id || !name || nodeIds.length === 0) return [];
    const directoryPath = typeof entry.directory_path === "string" ? entry.directory_path.trim() : "";
    const normalizedDirectoryPath = directoryPath || "root";
    return [{
      id,
      name,
      description: typeof entry.description === "string" ? entry.description : null,
      directory_path: normalizedDirectoryPath,
      user_ids: Array.isArray(entry.user_ids)
        ? entry.user_ids.filter((userId): userId is string => typeof userId === "string" && Boolean(userId.trim()))
        : [],
      node_ids: nodeIds,
    }];
  });
}

export function workflowRunDetailGraphMetadata(detail: WorkflowRunDetail): WorkflowGraphMetadata[] {
  return [
    ...coerceWorkflowGraphMetadataList(detail.graph.snapshot?.workflow_graphs),
    ...coerceWorkflowGraphMetadataList(detail.graph.config.workflow_graphs),
    ...coerceWorkflowGraphMetadataList(detail.graph.config.graphs),
  ];
}

export function workflowRunDetailSameGraphNodeIds(detail: WorkflowRunDetail): Set<string> {
  const allNodeIds = new Set(workflowRunDetailGraphNodes(detail).map((node) => node.id));
  const anchorNodeIds = workflowRunDetailVisitedNodeIds(detail);
  return sameGraphNodeIdsForRunAnchors(
    allNodeIds,
    workflowRunDetailGraphEdges(detail),
    workflowRunDetailGraphMetadata(detail),
    anchorNodeIds,
  );
}

export function workflowRunDetailDisplayNodeIds(detail: WorkflowRunDetail): Set<string> {
  const sameGraphNodeIds = workflowRunDetailSameGraphNodeIds(detail);
  if (workflowRunDetailIsFailed(detail)) return sameGraphNodeIds;
  const visitedNodeIds = workflowRunDetailVisitedNodeIds(detail);
  const displayedNodeIds = new Set([...sameGraphNodeIds].filter((nodeId) => visitedNodeIds.has(nodeId)));
  return displayedNodeIds.size > 0 ? displayedNodeIds : sameGraphNodeIds;
}

export function stripWorkflowRunGraphNodeKind(node: WorkflowNodeConfig & { kind: WorkflowNodeKind }): WorkflowNodeConfig {
  const { kind: _kind, ...nodeConfig } = node;
  return nodeConfig;
}

export function workflowRunDetailConfig(detail: WorkflowRunDetail, displayNodeIds: ReadonlySet<string>): WorkflowConfig {
  const nodes = workflowRunDetailGraphNodes(detail).filter((node) => displayNodeIds.has(node.id));
  return {
    triggers: nodes.filter((node) => node.kind === "trigger").map(stripWorkflowRunGraphNodeKind),
    gates: nodes.filter((node) => node.kind === "gate").map(stripWorkflowRunGraphNodeKind),
    effects: nodes.filter((node) => node.kind === "effect").map(stripWorkflowRunGraphNodeKind),
    queries: nodes.filter((node) => node.kind === "query").map(stripWorkflowRunGraphNodeKind),
    edges: workflowRunDetailGraphEdges(detail).filter(
      (edge) => displayNodeIds.has(edge.source_id) && displayNodeIds.has(edge.target_id),
    ),
  };
}

export function workflowRunDetailLayout(
  config: WorkflowConfig,
  baseLayout: WorkflowLayout | undefined,
  currentPositions: Record<string, { x: number; y: number }>,
): WorkflowLayout {
  return {
    nodes: Object.fromEntries(
      workflowNodes(config).map((node, index) => [
        node.id,
        currentPositions[node.id] ?? baseLayout?.nodes[node.id] ?? fallbackPosition(index),
      ]),
    ),
  };
}

export function preferredWorkflowRunDetailNodeId(detail: WorkflowRunDetail): string | null {
  const displayNodeIds = workflowRunDetailDisplayNodeIds(detail);
  const candidates = [
    detail.failed_node_id,
    [...detail.nodes].reverse()[0]?.node_id,
    detail.active_nodes[0]?.node_id,
    detail.source_node_id,
    detail.trigger_event?.trigger_id,
  ];
  return candidates.find((nodeId): nodeId is string => Boolean(nodeId && displayNodeIds.has(nodeId))) ?? null;
}

export function objectHasData(value: Record<string, unknown> | null | undefined): boolean {
  return Boolean(value && Object.keys(value).length > 0);
}

export function truncateRunDetailText(value: string, maxLength = 120): string {
  const trimmed = value.trim();
  if (trimmed.length <= maxLength) {
    return trimmed;
  }
  return `${trimmed.slice(0, maxLength - 1).trimEnd()}…`;
}

export function firstNonEmptyString(...values: Array<string | null | undefined>): string | null {
  for (const value of values) {
    const trimmed = value?.trim();
    if (trimmed) {
      return trimmed;
    }
  }
  return null;
}

export function operatorSteeringKey(sessionId: string | null | undefined, dispatchId: string | null | undefined): string | null {
  const normalizedSessionId = sessionId?.trim();
  const normalizedDispatchId = dispatchId?.trim();
  if (!normalizedSessionId || !normalizedDispatchId) return null;
  return `${normalizedSessionId}::${normalizedDispatchId}`;
}

export function operatorSteeringQuery(sessionId: string, dispatchId: string): string {
  const params = new URLSearchParams();
  params.set("session_id", sessionId);
  params.set("dispatch_id", dispatchId);
  return params.toString();
}

export function normalizeAvailabilityState(value: string | null | undefined): string {
  const normalized = value?.trim().toLowerCase();
  if (!normalized) {
    return "missing";
  }
  if (normalized === "available" || normalized === "recorded" || normalized === "present") {
    return "available";
  }
  return normalized;
}

export function availabilityFlag(value: boolean | string | null | undefined, fallback: boolean): boolean {
  if (typeof value === "boolean") {
    return value;
  }
  const normalized = normalizeAvailabilityState(value);
  if (
    normalized === "available" ||
    normalized === "session_available" ||
    normalized === "agent_available" ||
    normalized === "log_available" ||
    normalized === "artifact_available" ||
    normalized === "result_available" ||
    normalized === "recorded"
  ) {
    return true;
  }
  if (
    normalized === "missing" ||
    normalized === "session_missing" ||
    normalized === "agent_missing" ||
    normalized === "log_unavailable" ||
    normalized === "artifact_missing" ||
    normalized === "result_missing" ||
    normalized === "unavailable" ||
    normalized === "empty" ||
    normalized === "recorded_empty" ||
    normalized === "missing_legacy_or_unrecorded"
  ) {
    return false;
  }
  return fallback;
}

export function availabilitySummary(value: string | null | undefined, availableText = "Available", missingText = "Missing legacy or unrecorded"): string {
  const normalized = normalizeAvailabilityState(value);
  if (normalized === "available") {
    return availableText;
  }
  return value ? `${missingText} (${value})` : missingText;
}

export function structuredValueSummary(value: unknown, missingText: string, emptyText: string, maxLength = 120): string {
  if (value === undefined || value === null) {
    return missingText;
  }
  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed ? truncateRunDetailText(trimmed, maxLength) : missingText;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (Array.isArray(value)) {
    return value.length === 0 ? emptyText : truncateRunDetailText(formatJson(value, "[]").replace(/\s+/g, " "), maxLength);
  }
  if (isRecord(value)) {
    return objectHasData(value)
      ? truncateRunDetailText(formatJson(value, "{}").replace(/\s+/g, " "), maxLength)
      : emptyText;
  }
  return String(value);
}

export function detailHashTarget(tab: "tool-calls" | "agents" | "sessions", params: Record<string, string | null | undefined> = {}): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    const trimmed = value?.trim();
    if (trimmed) {
      query.set(key, trimmed);
    }
  }
  const queryText = query.toString();
  return queryText ? `${tab}?${queryText}` : tab;
}

export function durationLabel(durationMs: number | null | undefined): string {
  if (durationMs === null || durationMs === undefined) return "Unknown duration";
  if (durationMs < 1000) return `${durationMs} ms`;
  return `${(durationMs / 1000).toFixed(2)} s`;
}
