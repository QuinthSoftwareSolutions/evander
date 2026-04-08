import { useEffect, useRef, useState } from "react";
import {
  applyEdgeChanges,
  applyNodeChanges,
  Background,
  Controls,
  Handle,
  MiniMap,
  Position,
  ReactFlow,
  type ReactFlowInstance,
  type Connection,
  type Edge,
  type Node,
  type NodeChange,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  type WorkflowConfig,
  type WorkflowEdge,
  type WorkflowNodeActivityCounts,
  type WorkflowEditorCatalog,
  type WorkflowEditorDocument,
  type WorkflowEditorFieldDefinition,
  type WorkflowLayout,
  type WorkflowNodeConfig,
  type WorkflowNodeKind,
  type WorkflowRunNodeActivity,
  type WorkflowRunSnapshot,
  type WorkflowRunSummary,
} from "./types";

type GraphNodeData = {
  id: string;
  displayName: string;
  kind: WorkflowNodeKind;
  type: string;
  activeRunCount: number;
  dominantStatus: string | null;
  statusLabel: string | null;
};

type SaveState = "idle" | "saving" | "saved";

type BindingOption = {
  value: string;
  label: string;
};

const SYSTEM_BINDING_OPTIONS: ReadonlyArray<BindingOption> = [
  { value: "${{system.now.utc_timestamp}}", label: "[System] now.utc_timestamp" },
  { value: "${{system.now.utc_timestamp_compact}}", label: "[System] now.utc_timestamp_compact" },
  { value: "${{system.now.unix_seconds}}", label: "[System] now.unix_seconds" },
  { value: "${{system.execution.trace_id}}", label: "[System] execution.trace_id" },
  { value: "${{system.execution.event_id}}", label: "[System] execution.event_id" },
  { value: "${{system.execution.node_id}}", label: "[System] execution.node_id" },
  { value: "${{system.execution.started_at}}", label: "[System] execution.started_at" },
  { value: "${{system.execution.started_at_compact}}", label: "[System] execution.started_at_compact" },
  { value: "${{system.unique.compact}}", label: "[System] unique.compact" },
];

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";
const BINDING_EXACT_RE = /^\$\{\{([A-Za-z0-9][A-Za-z0-9_-]*)\.([A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*)\}\}$/;
const EFFECT_TARGET_KEYS = ["target", "session_id", "vm_name"] as const;
type EffectTargetKey = (typeof EFFECT_TARGET_KEYS)[number];

const NODE_GROUPS: ReadonlyArray<{
  kind: WorkflowNodeKind;
  label: string;
  description: string;
}> = [
  { kind: "trigger", label: "Triggers", description: "Start workflow execution from incoming events." },
  { kind: "gate", label: "Gates", description: "Transform, filter, or combine workflow data." },
  { kind: "effect", label: "Effects", description: "Perform side effects in VMs, agents, or Git providers." },
  { kind: "query", label: "Queries", description: "Read workflow state without producing a side effect." },
];

const RUNTIME_POLL_INTERVAL_MS = 5000;
const NODE_STATUS_ORDER = ["running", "waiting", "inflight", "pending"] as const;

function WorkflowNodeCard({ data, selected }: NodeProps) {
  const nodeData = data as GraphNodeData;
  return (
    <div
      className={`node-card node-${nodeData.kind}${selected ? " selected" : ""}${
        nodeData.dominantStatus ? ` runtime-${nodeData.dominantStatus}` : ""
      }`}
    >
      <Handle type="target" position={Position.Left} className="workflow-handle" />
      <div className="node-drag-surface">
        <div className="node-heading">
          <div className="node-kind">{nodeData.kind}</div>
          {nodeData.activeRunCount > 0 && nodeData.statusLabel ? (
            <div className={`node-runtime-badge status-${nodeData.dominantStatus ?? "active"}`}>
              {nodeData.statusLabel}
            </div>
          ) : null}
        </div>
        <div className="node-title">{nodeData.displayName}</div>
        <div className="node-id">{nodeData.id}</div>
      </div>
      <Handle type="source" position={Position.Right} className="workflow-handle" />
    </div>
  );
}

const nodeTypes = {
  workflowNode: WorkflowNodeCard,
};

function emptyConfig(): WorkflowConfig {
  return {
    triggers: [],
    gates: [],
    effects: [],
    queries: [],
    edges: [],
  };
}

function emptyLayout(): WorkflowLayout {
  return { nodes: {} };
}

function emptyCatalog(): WorkflowEditorCatalog {
  return { worker_id: "trigger-orchestrator", nodes: [] };
}

function agentRunCompletedSchemaNode(): WorkflowNodeConfig {
  return {
    id: "__agent-run-completed__",
    display_name: "Agent run completed",
    type: "trigger:agent:run:completed",
  };
}

function nodeCollections(config: WorkflowConfig) {
  return {
    triggers: config.triggers ?? [],
    gates: config.gates ?? [],
    effects: config.effects ?? [],
    queries: config.queries ?? [],
  };
}

function fallbackPosition(index: number): { x: number; y: number } {
  return {
    x: 120 + (index % 4) * 280,
    y: 100 + Math.floor(index / 4) * 180,
  };
}

function nodeKindFromType(type: string): WorkflowNodeKind {
  if (type.startsWith("trigger:")) return "trigger";
  if (type.startsWith("gate:")) return "gate";
  if (type.startsWith("query:")) return "query";
  return "effect";
}

function kindCollectionKey(kind: WorkflowNodeKind): "triggers" | "gates" | "effects" | "queries" {
  if (kind === "trigger") return "triggers";
  if (kind === "gate") return "gates";
  if (kind === "query") return "queries";
  return "effects";
}

function inboundLimit(node: WorkflowNodeConfig): number | null {
  const kind = nodeKindFromType(node.type);
  if (kind === "trigger") return 0;
  if (node.type === "gate:merge") return null;
  return 1;
}

function dominantNodeStatus(activity: WorkflowNodeActivityCounts | undefined): string | null {
  if (!activity) return null;
  for (const status of NODE_STATUS_ORDER) {
    if ((activity[status] ?? 0) > 0) return status;
  }
  const [fallback] = Object.keys(activity);
  return fallback ?? null;
}

function nodeStatusLabel(activity: WorkflowNodeActivityCounts | undefined): string | null {
  if (!activity) return null;
  const status = dominantNodeStatus(activity);
  if (!status) return null;
  const count = Object.values(activity).reduce((total, value) => total + value, 0);
  return `${count} ${status}`;
}

function buildNodes(
  config: WorkflowConfig,
  layout: WorkflowLayout,
  nodeActivity: Record<string, WorkflowNodeActivityCounts> = {},
  currentPositions: Record<string, { x: number; y: number }> = {},
): Node<GraphNodeData>[] {
  const collections = nodeCollections(config);
  const ordered = [
    ...collections.triggers,
    ...collections.gates,
    ...collections.effects,
    ...collections.queries,
  ];
  return ordered.map((node, index) => {
    const kind = nodeKindFromType(node.type);
    const position = currentPositions[node.id] ?? layout.nodes[node.id] ?? fallbackPosition(index);
    const activity = nodeActivity[node.id];
    return {
      id: node.id,
      type: "workflowNode",
      position,
      data: {
        id: node.id,
        displayName: node.display_name,
        kind,
        type: node.type,
        activeRunCount: Object.values(activity ?? {}).reduce((total, value) => total + value, 0),
        dominantStatus: dominantNodeStatus(activity),
        statusLabel: nodeStatusLabel(activity),
      },
    };
  });
}

function buildEdges(config: WorkflowConfig): Edge[] {
  return config.edges.map((edge) => ({
    id: `${edge.source_id}->${edge.target_id}`,
    source: edge.source_id,
    target: edge.target_id,
  }));
}

function renderStringInput(
  field: WorkflowEditorFieldDefinition,
  value: unknown,
  onChange: (nextValue: string) => void,
  disabled: boolean,
  fallbackPlaceholder: string,
) {
  if (field.multiline) {
    return (
      <textarea
        value={typeof value === "string" ? value : ""}
        rows={6}
        placeholder={field.placeholder ?? fallbackPlaceholder}
        onChange={(event) => onChange(event.target.value)}
        disabled={disabled}
      />
    );
  }
  return (
    <input
      value={typeof value === "string" ? value : ""}
      placeholder={field.placeholder ?? fallbackPlaceholder}
      onChange={(event) => onChange(event.target.value)}
      disabled={disabled}
    />
  );
}

function cloneConfig(config: WorkflowConfig): WorkflowConfig {
  return JSON.parse(JSON.stringify(config)) as WorkflowConfig;
}

function cloneLayout(layout: WorkflowLayout): WorkflowLayout {
  return JSON.parse(JSON.stringify(layout)) as WorkflowLayout;
}

function createDefaultNode(kind: WorkflowNodeKind, type: string, id: string): WorkflowNodeConfig {
  if (kind === "trigger" && type === "trigger:cron") {
    return { id, display_name: "New trigger", type, expression: "*/15 * * * *" };
  }
  if (kind === "trigger" && type === "trigger:ticket:created") {
    return { id, display_name: "Ticket created", type, directories: ["root"] };
  }
  if (kind === "trigger" && type === "trigger:ticket:changed") {
    return { id, display_name: "Ticket changed", type, directories: ["root"] };
  }
  if (kind === "trigger" && type === "trigger:discord:new-message") {
    return {
      id,
      display_name: "Discord message",
      type,
      discord_bot_token_env: "DISCORD_BOT_TOKEN",
      channel_ids: [],
      user_ids: [],
    };
  }
  if (kind === "trigger" && type === "trigger:email:new-message") {
    return {
      id,
      display_name: "Email message",
      type,
      provider: "json_store",
      account_identifier: "default",
      mailbox_ids: [],
      auth_secret_refs: {},
    };
  }
  if (kind === "trigger" && type === "trigger:pull-request:new-feedback") {
    return { id, display_name: "PR feedback", type, provider: "local" };
  }
  if (kind === "trigger" && type === "trigger:git:push") {
    return { id, display_name: "Git push", type, path_glob: "/path/to/git-remotes/*.git" };
  }
  if (kind === "trigger" && type === "trigger:agent:run:completed") {
    return { id, display_name: "Agent run completed", type, vm_user: "agent" };
  }
  if (kind === "gate" && type === "gate:filter") {
    return { id, display_name: "New filter", type, outputs: {} };
  }
  if (kind === "gate" && type === "gate:map") {
    return { id, display_name: "New map", type, outputs: {}, template: {} };
  }
  if (kind === "gate" && type === "gate:merge") {
    return { id, display_name: "New merge", type, outputs: {} };
  }
  if (kind === "gate" && type === "gate:await-agent") {
    return { id, display_name: "Await agent", type, outputs: {} };
  }
  if (kind === "effect" && type === "effect:agent:prompt") {
    return { id, display_name: "New prompt", type, prompt: "Describe the next action." };
  }
  if (kind === "effect" && type === "effect:vm:create") {
    return { id, display_name: "New VM effect", type, frontend_install_roots: [] };
  }
  if (kind === "effect" && type === "effect:vm:start") {
    return { id, display_name: "VM start", type };
  }
  if (kind === "effect" && type === "effect:vm:stop") {
    return { id, display_name: "VM stop", type, timeout_seconds: 30 };
  }
  if (kind === "effect" && type === "effect:vm:exec") {
    return { id, display_name: "New exec effect", type, command: "echo hello" };
  }
  if (kind === "effect" && type === "effect:vm:copy-files") {
    return {
      id,
      display_name: "New copy effect",
      type,
      files: [{ destination_path: "/tmp/example.txt", content: "example" }],
    };
  }
  if (kind === "effect" && type === "effect:session:update-metadata") {
    return {
      id,
      display_name: "Update session metadata",
      type,
      metadata: {},
    };
  }
  if (kind === "effect" && type === "effect:git:promote-agent-branch") {
    return {
      id,
      display_name: "Promote agent branch",
      type,
      host_repo_path: "/path/to/trusted/repo",
      bare_repo_path: "/path/to/git-remotes/sample.git",
      agent_branch: "agents/session-1/topic",
    };
  }
  if (kind === "effect" && type === "effect:pr:create") {
    return { id, display_name: "New PR effect", type, host_repo_path: "/path/to/repo" };
  }
  if (kind === "query" && type === "query:vm:run-state") {
    return { id, display_name: "VM run state", type };
  }
  if (kind === "query" && type === "query:git:pr-note") {
    return {
      id,
      display_name: "Git PR note",
      type,
      host_repo_path: "/path/to/trusted/repo",
      local_branch: "review/session-1/topic",
    };
  }
  if (kind === "query" && type === "query:session-context") {
    return {
      id,
      display_name: "Session context",
      type,
    };
  }
  return { id, display_name: "New node", type };
}

function slugifyNodeType(type: string): string {
  return type.replace(/[^a-zA-Z0-9]+/g, "-").replace(/^-+|-+$/g, "").toLowerCase() || "node";
}

function generateNodeId(config: WorkflowConfig, kind: WorkflowNodeKind, type: string): string {
  const collections = nodeCollections(config);
  const existingIds = new Set(
    [...collections.triggers, ...collections.gates, ...collections.effects, ...collections.queries].map(
      (node) => node.id,
    ),
  );
  const base = `${kind}-${slugifyNodeType(type).replace(`${kind}-`, "")}`;
  let index = 1;
  let candidate = `${base}-${index}`;
  while (existingIds.has(candidate)) {
    index += 1;
    candidate = `${base}-${index}`;
  }
  return candidate;
}

function nodeById(config: WorkflowConfig, nodeId: string): WorkflowNodeConfig | undefined {
  const collections = nodeCollections(config);
  return [...collections.triggers, ...collections.gates, ...collections.effects, ...collections.queries].find(
    (node) => node.id === nodeId,
  );
}

function validateConnection(config: WorkflowConfig, sourceId: string, targetId: string): string | null {
  if (!sourceId || !targetId) return "Edge endpoints are required.";
  if (sourceId === targetId) return "Nodes cannot connect to themselves.";
  if (config.edges.some((edge) => edge.source_id === sourceId && edge.target_id === targetId)) {
    return "That edge already exists.";
  }
  const targetNode = nodeById(config, targetId);
  if (!targetNode) return "Target node does not exist.";
  const limit = inboundLimit(targetNode);
  const inboundCount = config.edges.filter((edge) => edge.target_id === targetId).length;
  if (limit !== null && inboundCount >= limit) {
    return `Node ${targetId} already has the maximum supported inbound connections.`;
  }
  const nextEdges = [...config.edges, { source_id: sourceId, target_id: targetId }];
  const downstream = new Map<string, string[]>();
  for (const edge of nextEdges) {
    const current = downstream.get(edge.source_id) ?? [];
    current.push(edge.target_id);
    downstream.set(edge.source_id, current);
  }
  const stack = [...(downstream.get(targetId) ?? [])];
  const visited = new Set<string>();
  while (stack.length > 0) {
    const current = stack.pop()!;
    if (current === sourceId) return "That edge would create a cycle.";
    if (visited.has(current)) continue;
    visited.add(current);
    for (const child of downstream.get(current) ?? []) {
      stack.push(child);
    }
  }
  return null;
}

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  const payload = (await response.json()) as T & { error?: string };
  if (!response.ok && typeof payload === "object" && payload && "error" in payload) {
    throw new Error(String(payload.error));
  }
  return payload as T;
}

function currentPositionsFromNodes(nodes: Node<GraphNodeData>[]): Record<string, { x: number; y: number }> {
  return Object.fromEntries(nodes.map((node) => [node.id, node.position]));
}

function formatRuntimeTimestamp(value: string | null | undefined): string {
  if (!value) return "Unknown time";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function summarizeRunNode(activity: WorkflowRunNodeActivity): string {
  const countLabel = activity.count > 1 ? ` x${activity.count}` : "";
  return `${activity.node_id} ${activity.status}${countLabel}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isExactBindingValue(value: unknown): value is string {
  return typeof value === "string" && BINDING_EXACT_RE.test(value);
}

function hasBindingToken(value: unknown): boolean {
  return typeof value === "string" && value.includes("${{");
}

function formatJson(value: unknown, fallback = "{}"): string {
  if (value === undefined) return fallback;
  return JSON.stringify(value, null, 2);
}

function getCatalogEntry(catalog: WorkflowEditorCatalog, type: string) {
  return catalog.nodes.find((entry) => entry.type === type);
}

function directUpstreamNodeIds(config: WorkflowConfig, targetId: string): string[] {
  return config.edges.filter((edge) => edge.target_id === targetId).map((edge) => edge.source_id);
}

function declaredOutputsForNode(node: WorkflowNodeConfig | undefined, config?: WorkflowConfig): Record<string, unknown> {
  if (!node) return {};
  const schema: Record<string, unknown> = {};
  const derivedOnlyTypes = new Set(["gate:filter", "gate:merge", "gate:await-agent"]);
  const staticSchemas: Record<string, Record<string, unknown>> = {
    "trigger:ticket:created": {
      ticket: {
        type: "object",
        properties: {
          issue_id: { type: "string" },
          issue_number: { type: "string" },
          title: { type: "string" },
          body: { type: "string" },
          description: { type: "string" },
          reason: { type: "string" },
          rejection_reason: { type: "string" },
          yaml_path: { type: "string" },
          directory_path: { type: "string" },
          repo_source: { type: "string" },
          source_type: { type: "string" },
          tags: { type: "array", items: { type: "string" } },
          state: { type: "string" },
          url: { type: "string" },
          comments: { type: "array" },
          source: { type: "string" },
          metadata: { type: "object" },
          created_at: { type: "string" },
          updated_at: { type: "string" },
        },
      },
    },
    "trigger:ticket:changed": {
      ticket: {
        type: "object",
        properties: {
          issue_id: { type: "string" },
          issue_number: { type: "string" },
          title: { type: "string" },
          body: { type: "string" },
          description: { type: "string" },
          reason: { type: "string" },
          rejection_reason: { type: "string" },
          yaml_path: { type: "string" },
          directory_path: { type: "string" },
          repo_source: { type: "string" },
          source_type: { type: "string" },
          tags: { type: "array", items: { type: "string" } },
          state: { type: "string" },
          url: { type: "string" },
          comments: { type: "array" },
          source: { type: "string" },
          metadata: { type: "object" },
          created_at: { type: "string" },
          updated_at: { type: "string" },
        },
      },
    },
    "trigger:discord:new-message": {
      message: {
        type: "object",
        properties: {
          message_id: { type: "string" },
          channel_id: { type: "string" },
          author_id: { type: "string" },
          author_name: { type: "string" },
          content: { type: "string" },
          created_at: { type: "string" },
          in_reply_to: { type: "string" },
          raw: { type: "object" },
        },
      },
    },
    "trigger:email:new-message": {
      message: {
        type: "object",
        properties: {
          mailbox_id: { type: "string" },
          provider: { type: "string" },
          folder: { type: "string" },
          message_id: { type: "string" },
          thread_id: { type: "string" },
          sender: { type: "string" },
          recipients: { type: "array", items: { type: "string" } },
          received_at: { type: "string" },
          unread: { type: "boolean" },
          body_snippet: { type: "string" },
          metadata: { type: "object" },
        },
      },
    },
    "trigger:pull-request:new-feedback": {
      session_id: { type: "string" },
      pr: {
        type: "object",
        properties: {
          provider: { type: "string" },
          repo: { type: "string" },
          pr_id: { type: "string" },
        },
      },
      feedback: {
        type: "object",
        properties: {
          feedback_id: { type: "string" },
          provider: { type: "string" },
          repo: { type: "string" },
          pr_id: { type: "string" },
          thread_id: { type: "string" },
          comment_id: { type: "string" },
          author: { type: "string" },
          body: { type: "string" },
          file_path: { type: "string" },
          line: { type: "integer" },
          created_at: { type: "string" },
          in_reply_to: { type: "string" },
          resolved: { type: "boolean" },
        },
      },
    },
    "trigger:git:push": {
      branch_name: { type: "string" },
      commit_hash: { type: "string" },
      old_commit_hash: { type: "string" },
      ref_name: { type: "string" },
      repo_path: { type: "string" },
      repo_name: { type: "string" },
      session_id: { type: "string" },
      review_branch: { type: "string" },
      trusted_repo_path: { type: "string" },
      base_branch: { type: "string" },
      canonical_origin_url: { type: "string" },
    },
    "trigger:agent:run:completed": {
      vm: {
        type: "object",
        properties: {
          vm_name: { type: "string" },
          issue_number: { type: "string" },
          session_ids: { type: "array", items: { type: "string" } },
          labels: { type: "object" },
          directory_paths: { type: "array", items: { type: "string" } },
          libvirt_state: { type: "string" },
          ip_address: { type: "string" },
          agent_state: { type: "string" },
          agent_repo_names: { type: "array", items: { type: "string" } },
          last_state_update_at: { type: "string" },
        },
      },
      vm_name: { type: "string" },
      session_ids: { type: "array" },
      labels: { type: "object" },
      directory_paths: { type: "array" },
      agent_repo_names: { type: "array" },
      libvirt_state: { type: "string" },
      agent_state: { type: "string" },
      previous_agent_state: { type: "string" },
      transition: {
        type: "object",
        properties: {
          from: { type: "string" },
          to: { type: "string" },
        },
      },
      last_state_update_at: { type: "string" },
      dispatch_id: { type: "string" },
      scope: {
        type: "object",
        properties: {
          directory_path: { type: "string" },
          vm_name: { type: "string" },
          session_id: { type: "string" },
          vm_user: { type: "string" },
        },
      },
      ip_address: { type: "string" },
    },
    "effect:vm:create": {
      session_id: { type: "string" },
      vm_name: { type: "string" },
      labels: { type: "object" },
      success: { type: "boolean" },
      target_dir: { type: "string" },
      guest_repo_name: { type: "string" },
      remote_url: { type: "string" },
    },
    "effect:vm:start": {
      success: { type: "boolean" },
      action: { type: "string" },
      target: { type: "string" },
      vm_name: { type: "string" },
      message: { type: "string" },
    },
    "effect:vm:stop": {
      success: { type: "boolean" },
      action: { type: "string" },
      target: { type: "string" },
      vm_name: { type: "string" },
      message: { type: "string" },
    },
    "effect:agent:prompt": {
      dispatch_id: { type: "string" },
      thread_id: { type: "string" },
      success: { type: "boolean" },
      output_mode: { type: "string" },
    },
    "effect:vm:exec": {
      success: { type: "boolean" },
      vm_name: { type: "string" },
      session_id: { type: "string" },
      action: { type: "string" },
      message: { type: "string" },
      exit_code: { type: "integer" },
      stdout: { type: "string" },
      stderr: { type: "string" },
    },
    "effect:vm:copy-files": {
      success: { type: "boolean" },
      action: { type: "string" },
      message: { type: "string" },
      target: { type: "string" },
      vm_name: { type: "string" },
      vm_ip: { type: "string" },
      vm_user: { type: "string" },
      directory_path: { type: "string" },
      copied_count: { type: "integer" },
      copied_paths: { type: "array" },
      file_count: { type: "integer" },
      copied_files: { type: "array" },
    },
    "effect:session:update-metadata": {
      success: { type: "boolean" },
      action: { type: "string" },
      message: { type: "string" },
      session_id: { type: "string" },
      vm_name: { type: "string" },
      directory_path: { type: "string" },
      metadata: { type: "object" },
    },
    "effect:git:promote-agent-branch": {
      success: { type: "boolean" },
      action: { type: "string" },
      message: { type: "string" },
      host_repo_path: { type: "string" },
      bare_repo_path: { type: "string" },
      agent_branch: { type: "string" },
      review_branch: { type: "string" },
      remote_name: { type: "string" },
    },
    "effect:pr:create": {
      success: { type: "boolean" },
      action: { type: "string" },
      message: { type: "string" },
      pr_id: { type: "string" },
      pr_url: { type: "string" },
    },
    "query:vm:run-state": {
      vm_name: { type: "string" },
      session_ids: { type: "array" },
      libvirt_state: { type: "string" },
      state_code: { type: "string" },
      is_running: { type: "boolean" },
      is_terminal: { type: "boolean" },
    },
    "query:git:pr-note": {
      body: { type: "string" },
      has_note: { type: "boolean" },
      local_branch: { type: "string" },
      host_repo_path: { type: "string" },
      notes_ref: { type: "string" },
      local_notes_ref: { type: "string" },
      remote_name: { type: "string" },
      commit_sha: { type: "string" },
    },
    "query:session-context": {
      session_id: { type: "string" },
      vm_name: { type: "string" },
      directory_path: { type: "string" },
      metadata: { type: "object" },
      created_at: { type: "string" },
      expires_at: { type: "string" },
      model: { type: "string" },
    },
  };
  Object.assign(schema, staticSchemas[node.type] ?? {});
  if (node.type === "gate:map" && isRecord(node.template)) {
    for (const key of Object.keys(node.template)) {
      schema[key] = { type: "string" };
    }
    return schema;
  }
  if (node.type === "gate:await-agent") {
    return declaredOutputsForNode(
      agentRunCompletedSchemaNode(),
      config,
    );
  }
  if (derivedOnlyTypes.has(node.type)) return schema;
  if (isRecord(node.outputs)) {
    Object.assign(schema, node.outputs);
  }
  return schema;
}

function mergeDeclaredOutputIntersection(outputs: Record<string, unknown>[]): Record<string, unknown> {
  if (outputs.length === 0) return {};
  const sharedKeys = new Set(Object.keys(outputs[0]));
  for (const current of outputs.slice(1)) {
    for (const key of [...sharedKeys]) {
      if (!(key in current)) {
        sharedKeys.delete(key);
        continue;
      }
      if (JSON.stringify(outputs[0][key]) !== JSON.stringify(current[key])) {
        sharedKeys.delete(key);
      }
    }
  }
  const merged: Record<string, unknown> = {};
  for (const key of Object.keys(outputs[0])) {
    if (sharedKeys.has(key)) merged[key] = outputs[0][key];
  }
  return merged;
}

function normalizeDerivedNodeFields(config: WorkflowConfig): WorkflowConfig {
  const nextConfig = cloneConfig(config);
  for (const gate of nextConfig.gates ?? []) {
    const upstreamIds = directUpstreamNodeIds(nextConfig, gate.id);
    if (gate.type === "gate:filter") {
      gate.outputs = declaredOutputsForNode(nodeById(nextConfig, upstreamIds[0] ?? ""), nextConfig);
    } else if (gate.type === "gate:map") {
      gate.outputs = declaredOutputsForNode(gate, nextConfig);
    } else if (gate.type === "gate:await-agent") {
      delete gate.completion_trigger_id;
      gate.outputs = declaredOutputsForNode(agentRunCompletedSchemaNode(), nextConfig);
    } else if (gate.type === "gate:merge") {
      gate.outputs = mergeDeclaredOutputIntersection(
        upstreamIds.map((nodeId) => declaredOutputsForNode(nodeById(nextConfig, nodeId), nextConfig)),
      );
    }
  }
  return nextConfig;
}

function collectAncestorNodeIds(config: WorkflowConfig, nodeId: string): string[] {
  const inboundByTarget = new Map<string, string[]>();
  for (const edge of config.edges) {
    const current = inboundByTarget.get(edge.target_id) ?? [];
    current.push(edge.source_id);
    inboundByTarget.set(edge.target_id, current);
  }
  const ordered: string[] = [];
  const queue = [...(inboundByTarget.get(nodeId) ?? [])];
  const seen = new Set<string>();
  while (queue.length > 0) {
    const current = queue.shift()!;
    if (seen.has(current)) continue;
    seen.add(current);
    ordered.push(current);
    queue.push(...(inboundByTarget.get(current) ?? []));
  }
  return ordered;
}

function upstreamBindingOptions(config: WorkflowConfig, nodeId: string): BindingOption[] {
  const options: BindingOption[] = [...SYSTEM_BINDING_OPTIONS];
  const seen = new Set<string>();
  for (const option of SYSTEM_BINDING_OPTIONS) {
    seen.add(option.label.replace(/^\[System\] /, ""));
    seen.add(option.value);
  }
  for (const upstreamNodeId of collectAncestorNodeIds(config, nodeId)) {
    const outputs = declaredOutputsForNode(nodeById(config, upstreamNodeId), config);
    for (const outputName of Object.keys(outputs)) {
      for (const outputPath of flattenOutputPaths(outputName, outputs[outputName])) {
        const ref = `${upstreamNodeId}.${outputPath}`;
        if (seen.has(`\${{${ref}}}`)) continue;
        seen.add(`\${{${ref}}}`);
        options.push({ value: `\${{${ref}}}`, label: ref });
      }
    }
  }
  return options;
}

function flattenOutputPaths(prefix: string, schema: unknown): string[] {
  const paths = [prefix];
  if (!isRecord(schema)) return paths;
  const schemaType = typeof schema.type === "string" ? schema.type : "";
  if (schemaType === "object" && isRecord(schema.properties)) {
    for (const [key, childSchema] of Object.entries(schema.properties)) {
      const childKey = key.trim();
      if (!childKey) continue;
      for (const childPath of flattenOutputPaths(`${prefix}.${childKey}`, childSchema)) {
        paths.push(childPath);
      }
    }
  }
  if (schemaType === "array" && schema.items !== undefined) {
    for (const childPath of flattenOutputPaths(`${prefix}.0`, schema.items)) {
      paths.push(childPath);
    }
  }
  return paths;
}

function fieldValueMode(field: WorkflowEditorFieldDefinition, value: unknown): "literal" | "bind" | "template" {
  if (!field.bindable) return "literal";
  if (isExactBindingValue(value)) return "bind";
  if (field.kind === "string" && hasBindingToken(value)) return "template";
  return "literal";
}

function parseJsonDraft(text: string, fallback: unknown): unknown {
  const trimmed = text.trim();
  if (!trimmed) return fallback;
  return JSON.parse(trimmed) as unknown;
}

function removeUndefinedValues(value: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(value).filter(([, entry]) => entry !== undefined));
}

function renderBindingSelect(
  field: WorkflowEditorFieldDefinition,
  bindingOptions: BindingOption[],
  value: unknown,
  onChange: (nextValue: string) => void,
) {
  const stringValue = typeof value === "string" ? value : "";
  const hasPresetOption = bindingOptions.some((option) => option.value === stringValue);
  return (
    <div className="binding-editor">
      <label className="field-inline">
        <span>Suggested upstream output</span>
        <select
          value={hasPresetOption ? stringValue : ""}
          onChange={(event) => {
            if (event.target.value) onChange(event.target.value);
          }}
          disabled={bindingOptions.length === 0}
        >
          <option value="">{bindingOptions.length === 0 ? "No suggested outputs available" : "Select output"}</option>
          {bindingOptions.map((option) => (
            <option key={`${field.key}-${option.value}`} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </label>
      <label className="field-inline">
        <span>Binding reference</span>
        <input
          value={stringValue}
          placeholder="${{node.output.path}}"
          onChange={(event) => onChange(event.target.value)}
        />
      </label>
    </div>
  );
}

function insertBindingReference(currentValue: unknown, bindingReference: string): string {
  const text = typeof currentValue === "string" ? currentValue : "";
  const trimmedReference = bindingReference.trim();
  if (!trimmedReference) return text;
  if (!text.trim()) return trimmedReference;
  return `${text}${trimmedReference}`;
}

function activeEffectTargetKey(values: Record<string, unknown>): EffectTargetKey {
  if (typeof values.session_id === "string" && values.session_id.trim()) return "session_id";
  if (typeof values.vm_name === "string" && values.vm_name.trim()) return "vm_name";
  return "target";
}

export default function App() {
  const appShellRef = useRef<HTMLDivElement | null>(null);
  const hasAutoFitRef = useRef(false);
  const [document, setDocument] = useState<WorkflowEditorDocument | null>(null);
  const [catalog, setCatalog] = useState<WorkflowEditorCatalog>(emptyCatalog());
  const [runSnapshot, setRunSnapshot] = useState<WorkflowRunSnapshot | null>(null);
  const [flowNodes, setFlowNodes] = useState<Node<GraphNodeData>[]>([]);
  const [flowEdges, setFlowEdges] = useState<Edge[]>([]);
  const [flowInstance, setFlowInstance] = useState<ReactFlowInstance | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const [inspectorNodeId, setInspectorNodeId] = useState("");
  const [inspectorDisplayName, setInspectorDisplayName] = useState("");
  const [inspectorKnownValues, setInspectorKnownValues] = useState<Record<string, unknown>>({});
  const [inspectorUnknownValues, setInspectorUnknownValues] = useState<Record<string, unknown>>({});
  const [inspectorJsonDrafts, setInspectorJsonDrafts] = useState<Record<string, string>>({});
  const [inspectorStringListDrafts, setInspectorStringListDrafts] = useState<Record<string, string>>({});
  const [inspectorFieldModes, setInspectorFieldModes] = useState<Record<string, "literal" | "bind" | "template">>({});
  const [rawJsonDraft, setRawJsonDraft] = useState("{}");
  const [error, setError] = useState<string | null>(null);
  const [saveState, setSaveState] = useState<SaveState>("idle");

  useEffect(() => {
    Promise.all([
      fetchJson<WorkflowEditorDocument>("/api/workflow-editor/document"),
      fetchJson<WorkflowEditorCatalog>("/api/workflow-editor/catalog"),
      fetchJson<WorkflowRunSnapshot>("/api/workflow-editor/runs"),
    ])
      .then(([payload, catalogPayload, runSnapshotPayload]) => {
        const normalizedConfig = normalizeDerivedNodeFields(payload.config);
        setCatalog(catalogPayload);
        setRunSnapshot(runSnapshotPayload);
        setDocument({ ...payload, config: normalizedConfig });
        setFlowNodes(buildNodes(normalizedConfig, payload.layout, runSnapshotPayload.node_activity));
        setFlowEdges(buildEdges(normalizedConfig));
      })
      .catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : String(reason));
      });
  }, []);

  useEffect(() => {
    const intervalId = window.setInterval(() => {
      fetchJson<WorkflowRunSnapshot>("/api/workflow-editor/runs")
        .then((payload) => {
          setRunSnapshot(payload);
        })
        .catch(() => {
          // Keep the last snapshot visible when the polling request fails.
        });
    }, RUNTIME_POLL_INTERVAL_MS);
    return () => window.clearInterval(intervalId);
  }, []);

  useEffect(() => {
    if (!document) return;
    setFlowNodes((currentNodes) =>
      buildNodes(
        document.config,
        document.layout,
        runSnapshot?.node_activity ?? {},
        currentPositionsFromNodes(currentNodes),
      ),
    );
    setFlowEdges(buildEdges(document.config));
  }, [document, runSnapshot]);

  useEffect(() => {
    if (!flowInstance || flowNodes.length === 0 || hasAutoFitRef.current) return;
    queueMicrotask(() => {
      flowInstance.fitView({ padding: 0.2 });
      hasAutoFitRef.current = true;
    });
  }, [flowInstance, flowNodes, flowEdges]);

  const config = document?.config ?? emptyConfig();
  const layout = document?.layout ?? emptyLayout();
  const activeRuns = runSnapshot?.runs ?? [];
  const selectedNode = selectedNodeId ? nodeById(config, selectedNodeId) : undefined;
  const selectedCatalogEntry = selectedNode ? getCatalogEntry(catalog, selectedNode.type) : undefined;
  const selectedEdge = selectedEdgeId
    ? config.edges.find((edge) => `${edge.source_id}->${edge.target_id}` === selectedEdgeId)
    : undefined;
  const selectedNodeBindingOptions = selectedNode ? upstreamBindingOptions(config, selectedNode.id) : [];
  const hasCollapsedTargetUi =
    selectedNode?.type === "effect:vm:copy-files" ||
    selectedNode?.type === "effect:agent:prompt" ||
    selectedNode?.type === "effect:session:update-metadata" ||
    selectedNode?.type === "query:session-context";
  const effectTargetKey = activeEffectTargetKey(inspectorKnownValues);
  const inspectorFields = (() => {
    if (!selectedCatalogEntry) return [];
    if (!hasCollapsedTargetUi) return selectedCatalogEntry.fields;
    return selectedCatalogEntry.fields.filter((field) => !EFFECT_TARGET_KEYS.includes(field.key as EffectTargetKey));
  })();
  const effectTargetField = selectedCatalogEntry?.fields.find((field) => field.key === effectTargetKey)
    ?? selectedCatalogEntry?.fields.find((field) => field.key === "target");

  useEffect(() => {
    if (!selectedNode) {
      setInspectorNodeId("");
      setInspectorDisplayName("");
      setInspectorKnownValues({});
      setInspectorUnknownValues({});
      setInspectorJsonDrafts({});
      setInspectorStringListDrafts({});
      setInspectorFieldModes({});
      setRawJsonDraft("{}");
      return;
    }
    const { id: _id, display_name: _displayName, type: _type, ...extras } = selectedNode;
    const knownFieldKeys = new Set((selectedCatalogEntry?.fields ?? []).map((field) => field.key));
    const nextKnownValues = Object.fromEntries(Object.entries(extras).filter(([key]) => knownFieldKeys.has(key)));
    const nextUnknownValues = Object.fromEntries(Object.entries(extras).filter(([key]) => !knownFieldKeys.has(key)));
    const nextJsonDrafts: Record<string, string> = {};
    const nextStringListDrafts: Record<string, string> = {};
    const nextFieldModes: Record<string, "literal" | "bind" | "template"> = {};
    for (const field of selectedCatalogEntry?.fields ?? []) {
      nextFieldModes[field.key] = fieldValueMode(field, nextKnownValues[field.key]);
      if (field.kind !== "json") continue;
      nextJsonDrafts[field.key] = nextKnownValues[field.key] === undefined
        ? (field.required || field.key === "outputs" ? "{}" : "")
        : formatJson(nextKnownValues[field.key], field.required || field.key === "outputs" ? "{}" : "");
    }
    for (const field of selectedCatalogEntry?.fields ?? []) {
      if (field.kind !== "string_list") continue;
      nextStringListDrafts[field.key] = Array.isArray(nextKnownValues[field.key])
        ? (nextKnownValues[field.key] as unknown[]).map((item) => String(item)).join("\n")
        : "";
    }
    setInspectorNodeId(selectedNode.id);
    setInspectorDisplayName(selectedNode.display_name);
    setInspectorKnownValues(nextKnownValues);
    setInspectorUnknownValues(nextUnknownValues);
    setInspectorJsonDrafts(nextJsonDrafts);
    setInspectorStringListDrafts(nextStringListDrafts);
    setInspectorFieldModes(nextFieldModes);
    setRawJsonDraft(JSON.stringify(extras, null, 2));
  }, [selectedNode, selectedCatalogEntry]);

  useEffect(() => {
    if (!selectedEdgeId) return;
    appShellRef.current?.focus();
  }, [selectedEdgeId]);

  function patchDocument(nextConfig: WorkflowConfig, nextLayout: WorkflowLayout) {
    if (!document) return;
    const normalizedConfig = normalizeDerivedNodeFields(nextConfig);
    const nextDocument = {
      ...document,
      config: normalizedConfig,
      layout: nextLayout,
    };
    setDocument(nextDocument);
    setSaveState("idle");
  }

  function updateNodePositions(changes: NodeChange[]) {
    setFlowNodes((currentNodes) => applyNodeChanges(changes, currentNodes) as Node<GraphNodeData>[]);
  }

  function updateEdgeChanges(changes: Parameters<typeof applyEdgeChanges>[0]) {
    setFlowEdges((currentEdges) => applyEdgeChanges(changes, currentEdges));
  }

  function deleteSelectedEdge() {
    if (!document || !selectedEdgeId) return;
    const nextConfig = cloneConfig(document.config);
    nextConfig.edges = nextConfig.edges.filter(
      (edge) => `${edge.source_id}->${edge.target_id}` !== selectedEdgeId,
    );
    patchDocument(nextConfig, cloneLayout(document.layout));
    setSelectedEdgeId(null);
    setError(null);
  }

  function handleAppShellKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    const target = event.target;
    if (
      target instanceof HTMLInputElement ||
      target instanceof HTMLTextAreaElement ||
      target instanceof HTMLSelectElement ||
      (target instanceof HTMLElement && target.isContentEditable)
    ) {
      return;
    }
    if (!selectedEdgeId) return;
    if (event.key !== "Delete" && event.key !== "Backspace") return;
    event.preventDefault();
    deleteSelectedEdge();
  }

  function persistNodePosition(nodeId: string, position: { x: number; y: number }) {
    if (!document) return;
    const nextLayout = cloneLayout(document.layout);
    nextLayout.nodes[nodeId] = position;
    setDocument({
      ...document,
      layout: nextLayout,
    });
    setSaveState("idle");
  }

  function addNode(kind: WorkflowNodeKind, type: string) {
    if (!document) return;
    const nextConfig = cloneConfig(document.config);
    nextConfig.triggers ??= [];
    nextConfig.gates ??= [];
    nextConfig.effects ??= [];
    nextConfig.queries ??= [];
    const node = createDefaultNode(kind, type, generateNodeId(nextConfig, kind, type));
    nextConfig[kindCollectionKey(kind)].push(node);
    const nextLayout = cloneLayout(document.layout);
    nextLayout.nodes[node.id] = fallbackPosition(
      nextConfig.triggers.length +
        nextConfig.gates.length +
        nextConfig.effects.length +
        nextConfig.queries.length -
        1,
    );
    patchDocument(nextConfig, nextLayout);
    setSelectedNodeId(node.id);
    setSelectedEdgeId(null);
    setError(null);
  }

  function deleteSelectedNode() {
    if (!document || !selectedNodeId) return;
    const nextConfig = cloneConfig(document.config);
    nextConfig.triggers = (nextConfig.triggers ?? []).filter((node) => node.id !== selectedNodeId);
    nextConfig.gates = (nextConfig.gates ?? []).filter((node) => node.id !== selectedNodeId);
    nextConfig.effects = (nextConfig.effects ?? []).filter((node) => node.id !== selectedNodeId);
    nextConfig.queries = (nextConfig.queries ?? []).filter((node) => node.id !== selectedNodeId);
    nextConfig.edges = nextConfig.edges.filter(
      (edge) => edge.source_id !== selectedNodeId && edge.target_id !== selectedNodeId,
    );
    const nextLayout = cloneLayout(document.layout);
    delete nextLayout.nodes[selectedNodeId];
    patchDocument(nextConfig, nextLayout);
    setSelectedNodeId(null);
    setSelectedEdgeId(null);
    setError(null);
  }

  function applyInspectorChanges() {
    if (!document || !selectedNode) return;
    try {
      const rawId = inspectorNodeId.trim();
      const rawName = inspectorDisplayName.trim();
      if (!rawId) throw new Error("Node id is required.");
      if (!rawName) throw new Error("Display name is required.");
      if (rawId !== selectedNode.id && nodeById(document.config, rawId)) {
        throw new Error(`Node ${rawId} already exists.`);
      }
    const nextKnownValues = { ...inspectorKnownValues };
      if (hasCollapsedTargetUi) {
        const activeTargetKey = activeEffectTargetKey(nextKnownValues);
        for (const key of EFFECT_TARGET_KEYS) {
          if (key !== activeTargetKey) {
            delete nextKnownValues[key];
          }
        }
      }
      for (const field of selectedCatalogEntry?.fields ?? []) {
        if (field.kind !== "json") continue;
        if (isExactBindingValue(nextKnownValues[field.key])) continue;
        if (field.read_only) continue;
        const fallbackValue = field.required ? {} : undefined;
        nextKnownValues[field.key] = parseJsonDraft(inspectorJsonDrafts[field.key] ?? "", fallbackValue);
      }
      for (const field of selectedCatalogEntry?.fields ?? []) {
        if (field.kind !== "string_list") continue;
        nextKnownValues[field.key] = (inspectorStringListDrafts[field.key] ?? "")
          .split("\n")
          .map((item) => item.trim())
          .filter(Boolean);
      }
      const nextNode = removeUndefinedValues({
        id: rawId,
        display_name: rawName,
        type: selectedNode.type,
        ...inspectorUnknownValues,
        ...nextKnownValues,
      }) as WorkflowNodeConfig;
      const nextConfig = cloneConfig(document.config);
      const collectionName = kindCollectionKey(nodeKindFromType(selectedNode.type));
      nextConfig[collectionName] = nextConfig[collectionName].map((node) =>
        node.id === selectedNode.id ? nextNode : node,
      );
      nextConfig.edges = nextConfig.edges.map((edge) => ({
        source_id: edge.source_id === selectedNode.id ? rawId : edge.source_id,
        target_id: edge.target_id === selectedNode.id ? rawId : edge.target_id,
      }));
      const nextLayout = cloneLayout(document.layout);
      if (rawId !== selectedNode.id) {
        nextLayout.nodes[rawId] = nextLayout.nodes[selectedNode.id] ?? fallbackPosition(0);
        delete nextLayout.nodes[selectedNode.id];
      }
      patchDocument(nextConfig, nextLayout);
      setSelectedNodeId(rawId);
      setSelectedEdgeId(null);
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  function onConnect(connection: Connection) {
    if (!document || !connection.source || !connection.target) return;
    const validationError = validateConnection(document.config, connection.source, connection.target);
    if (validationError) {
      setError(validationError);
      return;
    }
    const nextConfig = cloneConfig(document.config);
    nextConfig.edges = [
      ...nextConfig.edges,
      { source_id: connection.source, target_id: connection.target } satisfies WorkflowEdge,
    ];
    patchDocument(nextConfig, cloneLayout(document.layout));
    setSelectedEdgeId(null);
    setError(null);
  }

  function onEdgesDelete(deletedEdges: Edge[]) {
    if (!document) return;
    const deleted = new Set(deletedEdges.map((edge) => `${edge.source}->${edge.target}`));
    const nextConfig = cloneConfig(document.config);
    nextConfig.edges = nextConfig.edges.filter(
      (edge) => !deleted.has(`${edge.source_id}->${edge.target_id}`),
    );
    patchDocument(nextConfig, cloneLayout(document.layout));
    setSelectedEdgeId(null);
  }

  async function validateDocument() {
    if (!document) return;
    const validation = await fetchJson<{ config: WorkflowConfig; layout: WorkflowLayout }>(
      "/api/workflow-editor/validate",
      {
        method: "POST",
        body: JSON.stringify({
          config: document.config,
          layout: document.layout,
        }),
      },
    );
    patchDocument(validation.config, validation.layout);
  }

  async function saveDocument() {
    if (!document) return;
    setSaveState("saving");
    setError(null);
    try {
      const saved = await fetchJson<{ config: WorkflowConfig; layout: WorkflowLayout }>(
        "/api/workflow-editor/document",
        {
          method: "PUT",
          body: JSON.stringify({
            config: document.config,
            layout: document.layout,
          }),
        },
      );
      patchDocument(saved.config, saved.layout);
      setSaveState("saved");
    } catch (reason) {
      setSaveState("idle");
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  function updateKnownField(key: string, value: unknown) {
    setInspectorKnownValues((current) => {
      const next = { ...current, [key]: value };
      if (selectedNode?.type === "gate:filter") {
        if (key === "match" && value !== undefined && value !== null && value !== "") {
          delete next.when;
        }
        if (key === "when" && value !== undefined && value !== null && value !== "") {
          delete next.match;
        }
      }
      return next;
    });
    if (selectedNode?.type === "gate:filter") {
      setInspectorJsonDrafts((current) => {
        if (key === "match" && value !== undefined && value !== null && value !== "") {
          return { ...current, when: "" };
        }
        if (key === "when" && value !== undefined && value !== null && value !== "") {
          return { ...current, match: "" };
        }
        return current;
      });
    }
  }

  function updateFieldMode(key: string, mode: "literal" | "bind" | "template") {
    setInspectorFieldModes((current) => ({ ...current, [key]: mode }));
  }

  function updateEffectTargetKey(nextKey: EffectTargetKey) {
    const currentKey = activeEffectTargetKey(inspectorKnownValues);
    const currentValue = inspectorKnownValues[currentKey];
    const currentMode = inspectorFieldModes[currentKey]
      ?? (effectTargetField ? fieldValueMode(effectTargetField, currentValue) : "literal");
    setInspectorKnownValues((current) => {
      const next = { ...current };
      for (const key of EFFECT_TARGET_KEYS) {
        delete next[key];
      }
      if (currentValue !== undefined && currentValue !== "") {
        next[nextKey] = currentValue;
      }
      return next;
    });
    setInspectorFieldModes((current) => {
      const next = { ...current };
      for (const key of EFFECT_TARGET_KEYS) {
        delete next[key];
      }
      next[nextKey] = currentMode;
      return next;
    });
  }

  function renderEffectTargetField() {
    if (!hasCollapsedTargetUi || !effectTargetField) return null;
    const value = inspectorKnownValues[effectTargetKey];
    const mode = inspectorFieldModes[effectTargetKey] ?? fieldValueMode(effectTargetField, value);
    const isDisabled = effectTargetField.read_only;
    const targetTypeLabel = selectedNode?.type === "effect:agent:prompt" ? "Prompt target type" : "Target type";
    const targetPlaceholder =
      effectTargetKey === "session_id"
        ? "Session ID"
        : effectTargetKey === "vm_name"
          ? "VM name"
          : "VM name or session ID";
    return (
      <div className="inspector-field" key="effect-target">
        <div className="field-toolbar">
          <div className="field-header">
            <span>Target</span>
          </div>
          {renderModeSelect(effectTargetField, mode, (nextMode) => {
            updateFieldMode(effectTargetKey, nextMode);
            if (nextMode === "bind") {
              updateKnownField(effectTargetKey, selectedNodeBindingOptions[0]?.value ?? "");
            } else if (nextMode === "template") {
              updateKnownField(effectTargetKey, typeof value === "string" ? value : "");
            } else {
              updateKnownField(effectTargetKey, isExactBindingValue(value) ? "" : value ?? "");
            }
          }, isDisabled)}
        </div>
        <label className="inspector-field">
          <div className="field-toolbar">
            <div className="field-header">
              <span>{targetTypeLabel}</span>
            </div>
          </div>
          <select
            value={effectTargetKey}
            onChange={(event) => updateEffectTargetKey(event.target.value as EffectTargetKey)}
            disabled={isDisabled}
          >
            <option value="target">Generic target</option>
            <option value="session_id">Session ID</option>
            <option value="vm_name">VM name</option>
          </select>
        </label>
        {mode === "bind"
          ? renderBindingSelect(effectTargetField, selectedNodeBindingOptions, value, (nextValue) => updateKnownField(effectTargetKey, nextValue))
          : mode === "template"
            ? (
              <div className="template-editor">
                <label className="field-inline">
                  <span>Insert output</span>
                  <select
                    value=""
                    onChange={(event) => {
                      const nextValue = event.target.value;
                      if (!nextValue) return;
                      updateKnownField(effectTargetKey, insertBindingReference(value, nextValue));
                      event.target.value = "";
                    }}
                    disabled={selectedNodeBindingOptions.length === 0 || isDisabled}
                  >
                    <option value="">
                      {selectedNodeBindingOptions.length === 0 ? "No outputs available" : "Insert output reference"}
                    </option>
                    {selectedNodeBindingOptions.map((option) => (
                      <option key={`vm-copy-target-template-${option.value}`} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </label>
                <input
                  value={typeof value === "string" ? value : ""}
                  placeholder={`${targetPlaceholder} or binding`}
                  onChange={(event) => updateKnownField(effectTargetKey, event.target.value)}
                  disabled={isDisabled}
                />
              </div>
            )
          : (
            <input
              value={typeof value === "string" ? value : ""}
              placeholder={targetPlaceholder}
              onChange={(event) => updateKnownField(effectTargetKey, event.target.value)}
              disabled={isDisabled}
            />
          )}
      </div>
    );
  }

  function renderModeSelect(
    field: WorkflowEditorFieldDefinition,
    mode: "literal" | "bind" | "template",
    onChange: (nextMode: "literal" | "bind" | "template") => void,
    disabled: boolean,
  ) {
    return (
      <label className="mode-select">
        <select value={mode} onChange={(event) => onChange(event.target.value as "literal" | "bind" | "template")} disabled={disabled}>
          <option value="literal">Literal</option>
          {field.bindable ? <option value="bind">Bind</option> : null}
          {field.bindable && field.kind === "string" ? <option value="template">Template</option> : null}
        </select>
      </label>
    );
  }

  function updateStringListField(key: string, value: string) {
    setInspectorStringListDrafts((current) => ({ ...current, [key]: value }));
  }

  function updateObjectListItem(fieldKey: string, index: number, key: string, value: unknown) {
    setInspectorKnownValues((current) => {
      const currentItems = Array.isArray(current[fieldKey]) ? [...(current[fieldKey] as unknown[])] : [];
      const currentItem = isRecord(currentItems[index]) ? { ...currentItems[index] } : {};
      if (value === undefined || value === "") {
        delete currentItem[key];
      } else {
        currentItem[key] = value;
      }
      currentItems[index] = currentItem;
      return { ...current, [fieldKey]: currentItems };
    });
  }

  function addObjectListItem(key: string, field: WorkflowEditorFieldDefinition) {
    setInspectorKnownValues((current) => {
      const currentItems = Array.isArray(current[key]) ? [...current[key]] : [];
      const nextItem = Object.fromEntries(
        field.item_fields
          .filter((itemField) => itemField.required)
          .map((itemField) => [itemField.key, itemField.kind === "boolean" ? false : ""]),
      );
      currentItems.push(nextItem);
      return { ...current, [key]: currentItems };
    });
  }

  function removeObjectListItem(key: string, index: number) {
    setInspectorKnownValues((current) => {
      const currentItems = Array.isArray(current[key]) ? [...current[key]] : [];
      currentItems.splice(index, 1);
      return { ...current, [key]: currentItems };
    });
  }

  function renderField(field: WorkflowEditorFieldDefinition) {
    const value = inspectorKnownValues[field.key];
    const mode = inspectorFieldModes[field.key] ?? fieldValueMode(field, value);
    const isDisabled = field.read_only;

    if (field.kind === "string") {
      return (
        <div key={field.key} className="inspector-field">
          <div className="field-toolbar">
            <div className="field-header">
              <span>{field.label}</span>
            </div>
            {renderModeSelect(field, mode, (nextMode) => {
              updateFieldMode(field.key, nextMode);
              if (nextMode === "bind") {
                updateKnownField(field.key, selectedNodeBindingOptions[0]?.value ?? "");
              } else if (nextMode === "template") {
                updateKnownField(field.key, typeof value === "string" ? value : "");
              } else {
                updateKnownField(field.key, isExactBindingValue(value) ? "" : value ?? "");
              }
            }, isDisabled)}
          </div>
          {field.description ? <p className="field-description">{field.description}</p> : null}
          {mode === "bind"
            ? renderBindingSelect(field, selectedNodeBindingOptions, value, (nextValue) => updateKnownField(field.key, nextValue))
            : mode === "template"
              ? (
                <div className="template-editor">
                  <label className="field-inline">
                    <span>Insert output</span>
                    <select
                      value=""
                      onChange={(event) => {
                        const nextValue = event.target.value;
                        if (!nextValue) return;
                        updateKnownField(field.key, insertBindingReference(value, nextValue));
                        event.target.value = "";
                      }}
                      disabled={selectedNodeBindingOptions.length === 0 || isDisabled}
                    >
                      <option value="">
                        {selectedNodeBindingOptions.length === 0 ? "No outputs available" : "Insert output reference"}
                      </option>
                      {selectedNodeBindingOptions.map((option) => (
                        <option key={`${field.key}-template-${option.value}`} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </label>
                  {renderStringInput(
                    field,
                    value,
                    (nextValue) => updateKnownField(field.key, nextValue),
                    isDisabled,
                    "Use ${{node.output.path}} inside the text",
                  )}
                </div>
              )
            : (
              renderStringInput(
                field,
                value,
                (nextValue) => updateKnownField(field.key, nextValue),
                isDisabled,
                "",
              )
            )}
        </div>
      );
    }

    if (field.kind === "number") {
      return (
        <div key={field.key} className="inspector-field">
          <div className="field-toolbar">
            <div className="field-header">
              <span>{field.label}</span>
            </div>
            {renderModeSelect(field, mode, (nextMode) => {
              updateFieldMode(field.key, nextMode);
              if (nextMode === "bind") {
                updateKnownField(field.key, selectedNodeBindingOptions[0]?.value ?? "");
              } else {
                updateKnownField(field.key, typeof value === "number" ? value : undefined);
              }
            }, isDisabled)}
          </div>
          {field.description ? <p className="field-description">{field.description}</p> : null}
          {mode === "bind"
            ? renderBindingSelect(field, selectedNodeBindingOptions, value, (nextValue) => updateKnownField(field.key, nextValue))
            : (
              <input
                type="number"
                value={typeof value === "number" ? value : ""}
                onChange={(event) => {
                  const nextValue = event.target.value;
                  updateKnownField(field.key, nextValue === "" ? undefined : Number(nextValue));
                }}
                disabled={isDisabled}
              />
            )}
        </div>
      );
    }

    if (field.kind === "boolean") {
      return (
        <div key={field.key} className="inspector-field">
          <div className="field-toolbar">
            <div className="field-header">
              <span>{field.label}</span>
            </div>
            {renderModeSelect(field, mode, (nextMode) => {
              updateFieldMode(field.key, nextMode);
              if (nextMode === "bind") {
                updateKnownField(field.key, selectedNodeBindingOptions[0]?.value ?? "");
              } else {
                updateKnownField(field.key, Boolean(value));
              }
            }, isDisabled)}
          </div>
          {field.description ? <p className="field-description">{field.description}</p> : null}
          {mode === "bind"
            ? renderBindingSelect(field, selectedNodeBindingOptions, value, (nextValue) => updateKnownField(field.key, nextValue))
            : (
              <label className="checkbox-row">
                <input
                  type="checkbox"
                  checked={typeof value === "boolean" ? value : false}
                  onChange={(event) => updateKnownField(field.key, event.target.checked)}
                  disabled={isDisabled}
                />
                <span>{typeof value === "boolean" && value ? "Enabled" : "Disabled"}</span>
              </label>
            )}
        </div>
      );
    }

    if (field.kind === "enum") {
      return (
        <label key={field.key} className="inspector-field">
          <div className="field-toolbar">
            <div className="field-header">
              <span>{field.label}</span>
            </div>
          </div>
          {field.description ? <p className="field-description">{field.description}</p> : null}
          <select
            value={typeof value === "string" ? value : ""}
            onChange={(event) => updateKnownField(field.key, event.target.value)}
            disabled={isDisabled}
          >
            <option value="">Select</option>
            {field.options.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
      );
    }

    if (field.kind === "string_list") {
      const listValue = inspectorStringListDrafts[field.key]
        ?? (Array.isArray(value) ? value.map((item) => String(item)).join("\n") : "");
      return (
        <label key={field.key} className="inspector-field">
          <div className="field-toolbar">
            <div className="field-header">
              <span>{field.label}</span>
            </div>
          </div>
          {field.description ? <p className="field-description">{field.description}</p> : null}
          <textarea
            value={listValue}
            rows={4}
            placeholder={field.placeholder ?? "One value per line"}
            onChange={(event) => updateStringListField(field.key, event.target.value)}
            disabled={isDisabled}
          />
        </label>
      );
    }

    if (field.kind === "json") {
      return (
        <div key={field.key} className="inspector-field">
          <div className="field-toolbar">
            <div className="field-header">
              <span>{field.label}</span>
            </div>
            {renderModeSelect(field, mode, (nextMode) => {
              updateFieldMode(field.key, nextMode);
              if (nextMode === "bind") {
                updateKnownField(field.key, selectedNodeBindingOptions[0]?.value ?? "");
              } else {
                updateKnownField(field.key, undefined);
              }
            }, isDisabled)}
          </div>
          {field.description ? <p className="field-description">{field.description}</p> : null}
          {mode === "bind"
            ? renderBindingSelect(field, selectedNodeBindingOptions, value, (nextValue) => updateKnownField(field.key, nextValue))
            : (
              <textarea
                value={inspectorJsonDrafts[field.key] ?? ""}
                rows={field.key === "outputs" ? 5 : 8}
                onChange={(event) =>
                  setInspectorJsonDrafts((current) => ({ ...current, [field.key]: event.target.value }))
                }
                disabled={isDisabled}
              />
            )}
        </div>
      );
    }

    if (field.kind === "object_list") {
      const items = Array.isArray(value) ? value : [];
      return (
        <div key={field.key} className="inspector-field">
          <div className="field-toolbar">
            <div className="field-header">
              <span>{field.label}</span>
            </div>
          </div>
          {items.map((item, index) => {
            const itemValue = isRecord(item) ? item : {};
            return (
              <div key={`${field.key}-${index}`} className="object-list-item">
                <div className="object-list-title">
                  <span>Item {index + 1}</span>
                  <button type="button" className="subtle-button" onClick={() => removeObjectListItem(field.key, index)}>
                    Remove
                  </button>
                </div>
                {field.item_fields.map((itemField) => {
                  const currentValue = itemValue[itemField.key];
                  const currentMode = fieldValueMode(itemField, currentValue);
                  return (
                    <div key={itemField.key} className="nested-field">
                      <div className="field-toolbar">
                        <div className="field-header">
                          <span>{itemField.label}</span>
                        </div>
                        {itemField.bindable ? (
                          <label className="mode-select">
                            <span>Mode</span>
                            <select
                              value={currentMode}
                              onChange={(event) => {
                                const nextMode = event.target.value as "literal" | "bind";
                                if (nextMode === "bind") {
                                  updateObjectListItem(field.key, index, itemField.key, selectedNodeBindingOptions[0]?.value ?? "");
                                } else {
                                  updateObjectListItem(field.key, index, itemField.key, "");
                                }
                              }}
                            >
                              <option value="literal">Literal</option>
                              <option value="bind">Bind</option>
                            </select>
                          </label>
                        ) : null}
                      </div>
                      {currentMode === "bind"
                        ? renderBindingSelect(itemField, selectedNodeBindingOptions, currentValue, (nextValue) =>
                            updateObjectListItem(field.key, index, itemField.key, nextValue),
                          )
                        : itemField.kind === "enum"
                          ? (
                            <select
                              value={typeof currentValue === "string" ? currentValue : ""}
                              onChange={(event) => updateObjectListItem(field.key, index, itemField.key, event.target.value)}
                            >
                              <option value="">Select</option>
                              {itemField.options.map((option) => (
                                <option key={`${field.key}-${index}-${itemField.key}-${option.value}`} value={option.value}>
                                  {option.label}
                                </option>
                              ))}
                            </select>
                          )
                        : itemField.kind === "boolean"
                          ? (
                            <label className="checkbox-row">
                              <input
                                type="checkbox"
                                checked={typeof currentValue === "boolean" ? currentValue : false}
                                onChange={(event) => updateObjectListItem(field.key, index, itemField.key, event.target.checked)}
                              />
                              <span>{typeof currentValue === "boolean" && currentValue ? "Enabled" : "Disabled"}</span>
                            </label>
                          )
                          : (
                            <input
                              value={typeof currentValue === "string" ? currentValue : ""}
                              onChange={(event) => updateObjectListItem(field.key, index, itemField.key, event.target.value)}
                            />
                          )}
                    </div>
                  );
                })}
              </div>
            );
          })}
          <button type="button" className="subtle-button" onClick={() => addObjectListItem(field.key, field)}>
            Add item
          </button>
        </div>
      );
    }

    return null;
  }

  const createEntriesByKind = NODE_GROUPS.map((group) => ({
    ...group,
    entries: catalog.nodes.filter((entry) => entry.kind === group.kind),
  }));

  return (
    <div className="app-shell" ref={appShellRef} tabIndex={-1} onKeyDown={handleAppShellKeyDown}>
      <aside className="sidebar">
        <div className="sidebar-section">
          <div className="eyebrow">Workflow editor</div>
          <h1>Workflow editor</h1>
          <p>Drag nodes, connect edges, and edit typed node config with literals or upstream bindings.</p>
        </div>

        <details className="sidebar-section current-runs-section">
          <summary className="current-runs-summary">
            <span className="section-label current-runs-label">Current runs</span>
            <span className="current-runs-count">{activeRuns.length}</span>
          </summary>
          <p className="runtime-summary">
            {activeRuns.length === 0
              ? "No active workflow runs."
              : `${activeRuns.length} active ${activeRuns.length === 1 ? "run" : "runs"} on the graph.`}
          </p>
          <div className="runtime-runs">
            {activeRuns.map((run: WorkflowRunSummary) => (
              <div key={run.event_id} className="runtime-run-card">
                <div className="runtime-run-header">
                  <div className="runtime-run-event">{run.event_id}</div>
                  <div className={`runtime-run-state state-${run.delivery_state ?? "active"}`}>
                    {run.delivery_state ?? "active"}
                  </div>
                </div>
                <div className="runtime-run-meta">
                  Last activity {formatRuntimeTimestamp(run.latest_activity_at ?? run.occurred_at)}
                </div>
                <div className="runtime-run-nodes">
                  {run.active_nodes.map((activity, index) => (
                    <div key={`${run.event_id}-${activity.node_id}-${activity.status}-${index}`} className="runtime-run-node">
                      {summarizeRunNode(activity)}
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </details>

        <div className="sidebar-section">
          <div className="section-label">Create</div>
          {createEntriesByKind.map((group) => (
            <details key={group.kind} className="create-group" open={group.kind === "trigger"}>
              <summary className="create-group-summary">
                <span>{group.label}</span>
                <span className="create-group-count">{group.entries.length}</span>
              </summary>
              <p className="create-group-description">{group.description}</p>
              <div className="create-group-actions">
                {group.entries.map((entry) => (
                  <button key={entry.type} onClick={() => addNode(entry.kind, entry.type)}>
                    {entry.label}
                  </button>
                ))}
              </div>
            </details>
          ))}
        </div>

        <div className="sidebar-section">
          <div className="section-label">Save</div>
          <button onClick={() => void validateDocument()}>Validate</button>
          <button className="primary" onClick={() => void saveDocument()}>
            {saveState === "saving" ? "Saving..." : "Save workflow"}
          </button>
          {error ? <div className="error-box save-error-box">{error}</div> : null}
          {document ? (
            <div className="path-note">
              <div>{document.config_path}</div>
              <div>{document.layout_path}</div>
            </div>
          ) : null}
        </div>

        {selectedNode ? (
          <div className="sidebar-section inspector">
            <div className="section-label">Inspector</div>
            <div className="inspector-summary">
              <div>{selectedNode.type}</div>
              <div>{selectedNode.id}</div>
            </div>
            <label className="inspector-label">
              <span>Node id</span>
              <input value={inspectorNodeId} onChange={(event) => setInspectorNodeId(event.target.value)} />
            </label>
            <label className="inspector-label">
              <span>Display name</span>
              <input
                value={inspectorDisplayName}
                onChange={(event) => setInspectorDisplayName(event.target.value)}
              />
            </label>

            {renderEffectTargetField()}
            {inspectorFields.map((field) => renderField(field))}

            {Object.keys(inspectorUnknownValues).length > 0 ? (
              <details className="raw-json-panel">
                <summary>Additional raw JSON</summary>
                <p className="field-description">These keys are preserved because they are not in the current catalog.</p>
                <textarea value={rawJsonDraft} rows={8} readOnly />
              </details>
            ) : null}

            <button className="primary" onClick={applyInspectorChanges}>
              Apply node changes
            </button>
            <button className="danger" onClick={deleteSelectedNode}>
              Delete node
            </button>
          </div>
        ) : null}

        {selectedEdge ? (
          <div className="sidebar-section inspector">
            <div className="section-label">Edge</div>
            <div className="inspector-summary">
              <div>{selectedEdge.source_id}</div>
              <div>{selectedEdge.target_id}</div>
            </div>
            <button className="danger" onClick={deleteSelectedEdge}>
              Delete edge
            </button>
          </div>
        ) : null}

        {error ? <div className="error-box">{error}</div> : null}
      </aside>

      <main className="canvas-shell">
        <ReactFlow
          nodes={flowNodes}
          edges={flowEdges}
          nodeTypes={nodeTypes}
          onInit={setFlowInstance}
          onNodesChange={updateNodePositions}
          onEdgesChange={updateEdgeChanges}
          onNodeClick={(_, node) => {
            setSelectedNodeId(node.id);
            setSelectedEdgeId(null);
          }}
          onEdgeClick={(_, edge) => {
            setSelectedNodeId(null);
            setSelectedEdgeId(edge.id);
          }}
          onNodeDragStop={(_, node) => persistNodePosition(node.id, node.position)}
          onPaneClick={() => {
            setSelectedNodeId(null);
            setSelectedEdgeId(null);
          }}
          onConnect={onConnect}
          onEdgesDelete={onEdgesDelete}
          fitView
          deleteKeyCode={["Backspace", "Delete"]}
        >
          <MiniMap />
          <Controls />
          <Background gap={20} size={1} />
        </ReactFlow>
      </main>
    </div>
  );
}
