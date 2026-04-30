import { useEffect, useRef, useState, type CSSProperties, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent } from "react";
import {
  applyEdgeChanges,
  applyNodeChanges,
  Background,
  Handle,
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
  type WorkflowEditorNodePortDefinition,
  type WorkflowLayout,
  type WorkflowNodeConfig,
  type WorkflowNodeKind,
  type WorkflowRunNodeActivity,
  type WorkflowRunSnapshot,
  type WorkflowRunSummary,
} from "./types";
import { useAdminUi } from "./features/admin/AdminContext";
import { Tooltip } from "./features/admin/Tooltip";

type GraphNodeData = {
  id: string;
  displayName: string;
  kind: WorkflowNodeKind;
  type: string;
  inputPorts: ReadonlyArray<WorkflowEditorNodePortDefinition>;
  outputPorts: ReadonlyArray<WorkflowEditorNodePortDefinition>;
  activeRunCount: number;
  dominantStatus: string | null;
  statusLabel: string | null;
};

type SaveState = "idle" | "saving" | "saved";

type BindingOption = {
  value: string;
  label: string;
};

type SnapGuide = {
  orientation: "horizontal" | "vertical";
  position: number;
};

type NodeBounds = {
  left: number;
  centerX: number;
  right: number;
  top: number;
  centerY: number;
  bottom: number;
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

const NODE_KIND_DESCRIPTIONS: Record<WorkflowNodeKind, string> = {
  trigger: "Triggers start workflow execution from incoming events.",
  gate: "Gates transform, filter, fan out, or combine workflow data between nodes.",
  effect: "Effects perform side effects such as VM actions, session updates, Git operations, or agent prompts.",
  query: "Queries read workflow or environment state without causing a side effect.",
};

const NODE_TYPE_DESCRIPTIONS: Record<string, string> = {
  "trigger:cron": "Start a workflow on a recurring cron schedule.",
  "trigger:ticket:created": "Start when a tracked ticket is created.",
  "trigger:ticket:changed": "Start when a tracked ticket changes state or metadata.",
  "trigger:discord:new-message": "Start from new Discord messages that match the configured filters.",
  "trigger:teams:new-message": "Start from new Microsoft Teams messages that match the configured targets.",
  "trigger:email:new-message": "Start from newly ingested email messages for the configured mailbox scope.",
  "trigger:pull-request:new-feedback": "Start when new pull-request feedback arrives from the configured provider.",
  "trigger:git:push": "Start from Git push events captured by the host-side bare-repo hook.",
  "trigger:agent:run:completed": "Start when a guest agent run completes.",
  "gate:filter": "Allow only matching events to continue downstream.",
  "gate:map": "Project upstream outputs into a new payload shape.",
  "gate:merge": "Merge compatible inbound event streams into one downstream path.",
  "gate:await-agent": "Pause the workflow until a selected agent session completes.",
  "gate:for": "Iterate over an array and execute the body branch for each item.",
  "effect:agent:prompt": "Send a prompt to an agent session.",
  "effect:agent:exec": "Run a shell command in an agent session.",
  "effect:vm:create": "Create a VM for the workflow.",
  "effect:vm:clone-repo": "Clone a repository into a target VM.",
  "effect:vm:bootstrap-python": "Bootstrap a Python checkout and environment in a VM.",
  "effect:vm:bootstrap-frontend": "Bootstrap frontend dependencies in a VM.",
  "effect:vm:start": "Start an existing VM.",
  "effect:vm:stop": "Stop an existing VM.",
  "effect:vm:exec": "Run a shell command inside a VM.",
  "effect:vm:copy-files": "Copy rendered file content into a VM.",
  "effect:vm:delete-files": "Delete files from a VM.",
  "effect:session:update-metadata": "Patch session metadata on the host.",
  "effect:git:promote-agent-branch": "Promote an agent branch into a trusted host checkout.",
  "effect:pr:create": "Create a pull request from the host side.",
  "query:vm:run-state": "Read the current run state for a VM.",
  "query:git:pr-note": "Read pull-request note context from a host checkout.",
  "query:session-context": "Read session context and metadata.",
  "query:issue:get": "Read a local issue record by id.",
};

const RUNTIME_POLL_INTERVAL_MS = 5000;
const NODE_STATUS_ORDER = ["failed", "running", "waiting", "inflight", "pending"] as const;
const SNAP_THRESHOLD = 12;
const FALLBACK_NODE_WIDTH = 212;
const FALLBACK_NODE_HEIGHT = 108;
const WORKFLOW_SIDEBAR_WIDTH_STORAGE_KEY = "workflow-editor:sidebar-width";
const WORKFLOW_SIDEBAR_DEFAULT_WIDTH = 320;
const WORKFLOW_SIDEBAR_MIN_WIDTH = 320;
const WORKFLOW_SIDEBAR_MAX_WIDTH = 760;
const WORKFLOW_SIDEBAR_CANVAS_MIN_WIDTH = 420;
const WORKFLOW_SIDEBAR_RESIZE_STEP = 32;
const WORKFLOW_COMPACT_BREAKPOINT = 980;

type SidebarResizeState = {
  pointerId: number;
  startX: number;
  startWidth: number;
};

function readStoredWorkflowSidebarWidth(): number {
  if (typeof window === "undefined") return WORKFLOW_SIDEBAR_DEFAULT_WIDTH;
  const raw = window.localStorage.getItem(WORKFLOW_SIDEBAR_WIDTH_STORAGE_KEY);
  if (!raw) return WORKFLOW_SIDEBAR_DEFAULT_WIDTH;
  const parsed = Number.parseInt(raw, 10);
  if (!Number.isFinite(parsed)) return WORKFLOW_SIDEBAR_DEFAULT_WIDTH;
  return Math.min(Math.max(parsed, WORKFLOW_SIDEBAR_MIN_WIDTH), WORKFLOW_SIDEBAR_MAX_WIDTH);
}

function maxWorkflowSidebarWidth(containerWidth: number): number {
  return Math.max(
    WORKFLOW_SIDEBAR_MIN_WIDTH,
    Math.min(WORKFLOW_SIDEBAR_MAX_WIDTH, Math.floor(containerWidth - WORKFLOW_SIDEBAR_CANVAS_MIN_WIDTH)),
  );
}

function clampWorkflowSidebarWidth(width: number, containerWidth: number): number {
  const maxWidth = maxWorkflowSidebarWidth(containerWidth);
  return Math.min(Math.max(width, WORKFLOW_SIDEBAR_MIN_WIDTH), maxWidth);
}

function WorkflowNodeCard({ data, selected }: NodeProps) {
  const nodeData = data as GraphNodeData;
  const kindDescription = NODE_KIND_DESCRIPTIONS[nodeData.kind];
  const typeDescription = NODE_TYPE_DESCRIPTIONS[nodeData.type] ?? `${nodeData.type} node.`;
  return (
    <div
      className={`node-card node-${nodeData.kind}${selected ? " selected" : ""}${
        nodeData.dominantStatus ? ` runtime-${nodeData.dominantStatus}` : ""
      }`}
    >
      {nodeData.inputPorts.length > 0 ? (
        <div className="workflow-port-stack port-stack-input">
          {nodeData.inputPorts.map((port, index) => (
            <div
              key={`input-${port.key}`}
              className="workflow-port"
              style={{ top: `${30 + index * 24}px` }}
            >
              <Tooltip content={port.description ?? `${port.label} input port.`} className="workflow-port-tooltip" inline={false}>
                <Handle id={port.key} type="target" position={Position.Left} className="workflow-handle" />
                <span className="workflow-port-label">{port.label}</span>
              </Tooltip>
            </div>
          ))}
        </div>
      ) : (
        <Handle type="target" position={Position.Left} className="workflow-handle" />
      )}
      <div className="node-drag-surface">
        <div className="node-heading">
          <Tooltip content={`${kindDescription} ${typeDescription}`}>
            <div className="node-kind">{nodeData.kind}</div>
          </Tooltip>
          {nodeData.activeRunCount > 0 && nodeData.statusLabel ? (
            <div className={`node-runtime-badge status-${nodeData.dominantStatus ?? "active"}`}>
              {nodeData.statusLabel}
            </div>
          ) : null}
        </div>
        <Tooltip content={typeDescription}>
          <div className="node-title">{nodeData.displayName}</div>
        </Tooltip>
        <div className="node-id">{nodeData.id}</div>
      </div>
      {nodeData.outputPorts.length > 0 ? (
        <div className="workflow-port-stack port-stack-output">
          {nodeData.outputPorts.map((port, index) => (
            <div
              key={`output-${port.key}`}
              className="workflow-port workflow-port-output"
              style={{ top: `${30 + index * 24}px` }}
            >
              <Tooltip content={port.description ?? `${port.label} output port.`} className="workflow-port-tooltip" inline={false}>
                <span className="workflow-port-label">{port.label}</span>
                <Handle id={port.key} type="source" position={Position.Right} className="workflow-handle" />
              </Tooltip>
            </div>
          ))}
        </div>
      ) : (
        <Handle type="source" position={Position.Right} className="workflow-handle" />
      )}
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
  if (node.type === "gate:merge" || node.type === "gate:for") return null;
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
  catalog: WorkflowEditorCatalog,
  nodeActivity: Record<string, WorkflowNodeActivityCounts> = {},
  currentPositions: Record<string, { x: number; y: number }> = {},
  selectedNodeIds: string[] = [],
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
    const catalogEntry = getCatalogEntry(catalog, node.type);
    return {
      id: node.id,
      type: "workflowNode",
      position,
      selected: selectedNodeIds.includes(node.id),
      data: {
        id: node.id,
        displayName: node.display_name,
        kind,
        type: node.type,
        inputPorts: catalogEntry?.input_ports ?? [],
        outputPorts: catalogEntry?.output_ports ?? [],
        activeRunCount: Object.values(activity ?? {}).reduce((total, value) => total + value, 0),
        dominantStatus: dominantNodeStatus(activity),
        statusLabel: nodeStatusLabel(activity),
      },
    };
  });
}

function edgeId(edge: WorkflowEdge): string {
  return `${edge.source_id}:${edge.source_port ?? ""}->${edge.target_id}`;
}

function wouldCreateCycle(config: WorkflowConfig, sourceId: string, targetId: string): boolean {
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
    if (current === sourceId) return true;
    if (visited.has(current)) continue;
    visited.add(current);
    for (const child of downstream.get(current) ?? []) {
      stack.push(child);
    }
  }
  return false;
}

function buildEdges(config: WorkflowConfig): Edge[] {
  return config.edges.map((edge) => ({
    id: edgeId(edge),
    source: edge.source_id,
    sourceHandle: edge.source_port,
    target: edge.target_id,
    label: edge.source_port ?? undefined,
    animated: Boolean(edge.allow_cycle),
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

function cloneNodeConfig(node: WorkflowNodeConfig): WorkflowNodeConfig {
  return JSON.parse(JSON.stringify(node)) as WorkflowNodeConfig;
}

function flattenWorkflowNodeConfig(node: WorkflowNodeConfig): WorkflowNodeConfig {
  const clonedNode = cloneNodeConfig(node);
  const nestedConfig = isRecord(clonedNode.config) ? clonedNode.config : undefined;
  if (!nestedConfig) return clonedNode;
  const { config: _config, ...rest } = clonedNode;
  return {
    ...nestedConfig,
    ...rest,
  };
}

function normalizeWorkflowConfigShape(config: WorkflowConfig): WorkflowConfig {
  const nextConfig = cloneConfig(config);
  nextConfig.triggers = (nextConfig.triggers ?? []).map(flattenWorkflowNodeConfig);
  nextConfig.gates = (nextConfig.gates ?? []).map(flattenWorkflowNodeConfig);
  nextConfig.effects = (nextConfig.effects ?? []).map(flattenWorkflowNodeConfig);
  nextConfig.queries = (nextConfig.queries ?? []).map(flattenWorkflowNodeConfig);
  return nextConfig;
}

function isEditableEventTarget(target: EventTarget | null): boolean {
  return (
    target instanceof HTMLInputElement ||
    target instanceof HTMLTextAreaElement ||
    target instanceof HTMLSelectElement ||
    (target instanceof HTMLElement && target.isContentEditable)
  );
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
  if (kind === "trigger" && type === "trigger:teams:new-message") {
    return {
      id,
      display_name: "Teams message",
      type,
      targets: [],
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
    return { id, display_name: "Await agent", type, session_id: "session-id", outputs: {} };
  }
  if (kind === "effect" && type === "effect:agent:prompt") {
    return { id, display_name: "New prompt", type, prompt: "Describe the next action." };
  }
  if (kind === "effect" && type === "effect:agent:exec") {
    return { id, display_name: "Agent exec", type, command: "echo hello" };
  }
  if (kind === "effect" && type === "effect:vm:create") {
    return { id, display_name: "New VM effect", type };
  }
  if (kind === "effect" && type === "effect:vm:clone-repo") {
    return { id, display_name: "Clone repo into VM", type, repo_source: "/path/to/repo", clone_branch: "main" };
  }
  if (kind === "effect" && type === "effect:vm:bootstrap-python") {
    return {
      id,
      display_name: "Bootstrap Python in VM",
      type,
      host_checkout: "/path/to/repo",
      target_dir: "/home/agent/repo",
    };
  }
  if (kind === "effect" && type === "effect:vm:bootstrap-frontend") {
    return {
      id,
      display_name: "Bootstrap frontend deps in VM",
      type,
      host_checkout: "/path/to/repo",
      install_roots: ["/path/to/repo/Src/Clients"],
    };
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
  if (kind === "effect" && type === "effect:vm:delete-files") {
    return {
      id,
      display_name: "Delete files",
      type,
      paths: ["/tmp/example.txt"],
      missing_ok: true,
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
  if (kind === "query" && type === "query:issue:get") {
    return {
      id,
      display_name: "Issue get",
      type,
      issue_id: "issue-1",
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

function validateConnection(
  config: WorkflowConfig,
  catalog: WorkflowEditorCatalog,
  sourceId: string,
  targetId: string,
  sourcePort?: string | null,
): string | null {
  if (!sourceId || !targetId) return "Edge endpoints are required.";
  if (sourceId === targetId) return "Nodes cannot connect to themselves.";
  if (
    config.edges.some(
      (edge) => edge.source_id === sourceId && edge.target_id === targetId && (edge.source_port ?? "") === (sourcePort ?? ""),
    )
  ) {
    return "That edge already exists.";
  }
  const sourceNode = nodeById(config, sourceId);
  const targetNode = nodeById(config, targetId);
  if (!sourceNode) return "Source node does not exist.";
  if (!targetNode) return "Target node does not exist.";
  const sourceCatalogEntry = getCatalogEntry(catalog, sourceNode.type);
  const allowedPorts = sourceCatalogEntry?.output_ports ?? [];
  if (allowedPorts.length > 0 && !allowedPorts.some((port) => port.key === sourcePort)) {
    return `Node ${sourceId} requires selecting one of its named output ports.`;
  }
  const limit = inboundLimit(targetNode);
  const inboundCount = config.edges.filter((edge) => edge.target_id === targetId).length;
  if (limit !== null && inboundCount >= limit) {
    return `Node ${targetId} already has the maximum supported inbound connections.`;
  }
  if (wouldCreateCycle(config, sourceId, targetId)) {
    if (sourceNode.type === "gate:for" && sourcePort === "body") return null;
    if (targetNode.type === "gate:for") return null;
    return "That edge would create a cycle.";
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

function nodeBounds(node: Node): NodeBounds {
  const width = node.measured?.width ?? node.width ?? FALLBACK_NODE_WIDTH;
  const height = node.measured?.height ?? node.height ?? FALLBACK_NODE_HEIGHT;
  const left = node.position.x;
  const top = node.position.y;
  return {
    left,
    centerX: left + width / 2,
    right: left + width,
    top,
    centerY: top + height / 2,
    bottom: top + height,
  };
}

function closestSnapDelta(sourceAnchors: number[], targetAnchors: number[]): { delta: number; guide: number } | null {
  let closest: { delta: number; guide: number } | null = null;
  for (const sourceAnchor of sourceAnchors) {
    for (const targetAnchor of targetAnchors) {
      const delta = targetAnchor - sourceAnchor;
      if (Math.abs(delta) > SNAP_THRESHOLD) continue;
      if (!closest || Math.abs(delta) < Math.abs(closest.delta)) {
        closest = { delta, guide: targetAnchor };
      }
    }
  }
  return closest;
}

function snapDraggedNodes(activeNode: Node, draggedNodes: Node<GraphNodeData>[], allNodes: Node<GraphNodeData>[]) {
  const stationaryNodes = allNodes.filter((node) => !draggedNodes.some((dragged) => dragged.id === node.id));
  if (stationaryNodes.length === 0) {
    return { snappedNodes: draggedNodes, guides: [] as SnapGuide[] };
  }
  const activeBounds = nodeBounds(activeNode);
  let bestX: { delta: number; guide: number } | null = null;
  let bestY: { delta: number; guide: number } | null = null;

  for (const candidate of stationaryNodes) {
    const candidateBounds = nodeBounds(candidate);
    const xSnap = closestSnapDelta(
      [activeBounds.left, activeBounds.centerX, activeBounds.right],
      [candidateBounds.left, candidateBounds.centerX, candidateBounds.right],
    );
    if (xSnap && (!bestX || Math.abs(xSnap.delta) < Math.abs(bestX.delta))) {
      bestX = xSnap;
    }
    const ySnap = closestSnapDelta(
      [activeBounds.top, activeBounds.centerY, activeBounds.bottom],
      [candidateBounds.top, candidateBounds.centerY, candidateBounds.bottom],
    );
    if (ySnap && (!bestY || Math.abs(ySnap.delta) < Math.abs(bestY.delta))) {
      bestY = ySnap;
    }
  }

  if (!bestX && !bestY) {
    return { snappedNodes: draggedNodes, guides: [] as SnapGuide[] };
  }

  const xDelta = bestX?.delta ?? 0;
  const yDelta = bestY?.delta ?? 0;
  return {
    snappedNodes: draggedNodes.map((node) => ({
      ...node,
      position: {
        x: node.position.x + xDelta,
        y: node.position.y + yDelta,
      },
    })),
    guides: [
      ...(bestX ? [{ orientation: "vertical" as const, position: bestX.guide }] : []),
      ...(bestY ? [{ orientation: "horizontal" as const, position: bestY.guide }] : []),
    ],
  };
}

function formatRuntimeTimestamp(value: string | null | undefined): string {
  if (!value) return "Unknown time";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function summarizeRunNode(activity: WorkflowRunNodeActivity): string {
  const countLabel = activity.count > 1 ? ` x${activity.count}` : "";
  const errorLabel = activity.error ? `: ${activity.error}` : "";
  return `${activity.node_id} ${activity.status}${countLabel}${errorLabel}`;
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

function objectListJsonDraftKey(fieldKey: string, index: number, itemFieldKey: string): string {
  return `${fieldKey}.${index}.${itemFieldKey}`;
}

function getCatalogEntry(catalog: WorkflowEditorCatalog, type: string) {
  return catalog.nodes.find((entry) => entry.type === type);
}

function nodeTypeDescription(type: string, kind: WorkflowNodeKind): string {
  return NODE_TYPE_DESCRIPTIONS[type] ?? NODE_KIND_DESCRIPTIONS[kind];
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
    "trigger:teams:new-message": {
      message: {
        type: "object",
        properties: {
          cursor: { type: "string" },
          message_id: { type: "string" },
          author_id: { type: "string" },
          team_id: { type: "string" },
          channel_id: { type: "string" },
          thread_id: { type: "string" },
          content: { type: "string" },
          created_at: { type: "string" },
          metadata: { type: "object" },
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
    "trigger:review:new-request": {
      review_request: {
        type: "object",
        properties: {
          request_id: { type: "string" },
          requested_at: { type: "string" },
          requested_by: { type: "string" },
          request_channel: { type: "string" },
          provider: { type: "string" },
          repo: { type: "string" },
          pr_id: { type: "string" },
          canonical_url: { type: "string" },
          title: { type: "string" },
          body: { type: "string" },
          base_branch: { type: "string" },
          head_branch: { type: "string" },
          base_commit: { type: "string" },
          head_commit: { type: "string" },
          review_type: { type: "string" },
          instructions: { type: "string" },
          directory_path: { type: "string" },
          model: { type: "string" },
          labels: { type: "object" },
          metadata: { type: "object" },
          status: { type: "string" },
          updated_at: { type: "string" },
          lease_owner: { type: "string" },
          lease_expires_at: { type: "string" },
          dispatch_count: { type: "integer" },
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
      message: { type: "string" },
    },
    "effect:vm:clone-repo": {
      session_id: { type: "string" },
      vm_name: { type: "string" },
      success: { type: "boolean" },
      target_dir: { type: "string" },
      guest_repo_name: { type: "string" },
      remote_url: { type: "string" },
    },
    "effect:vm:bootstrap-python": {
      session_id: { type: "string" },
      vm_name: { type: "string" },
      success: { type: "boolean" },
      message: { type: "string" },
    },
    "effect:vm:bootstrap-frontend": {
      session_id: { type: "string" },
      vm_name: { type: "string" },
      success: { type: "boolean" },
      message: { type: "string" },
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
    "effect:agent:exec": {
      success: { type: "boolean" },
      vm_name: { type: "string" },
      session_id: { type: "string" },
      execution_backend: { type: "string" },
      execution_id: { type: "string" },
      status: { type: "string" },
      action: { type: "string" },
      message: { type: "string" },
      exit_code: { type: "integer" },
      stdout: { type: "string" },
      stderr: { type: "string" },
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
    "effect:vm:delete-files": {
      success: { type: "boolean" },
      action: { type: "string" },
      message: { type: "string" },
      target: { type: "string" },
      vm_name: { type: "string" },
      vm_user: { type: "string" },
      deleted_count: { type: "integer" },
      deleted_paths: { type: "array" },
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
    "query:issue:get": {
      issue_id: { type: "string" },
      issue_number: { type: "string" },
      title: { type: "string" },
      body: { type: "string" },
      state: { type: "string" },
      url: { type: "string" },
      source: { type: "string" },
      source_type: { type: "string" },
      directory_path: { type: "string" },
      yaml_path: { type: "string" },
      repo_source: { type: "string" },
      metadata: { type: "object" },
      comments: { type: "array" },
      created_at: { type: "string" },
      updated_at: { type: "string" },
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
  const { privilegedUnlocked } = useAdminUi();
  const appShellRef = useRef<HTMLDivElement | null>(null);
  const hasAutoFitRef = useRef(false);
  const copiedNodeRef = useRef<WorkflowNodeConfig | null>(null);
  const sidebarResizeRef = useRef<SidebarResizeState | null>(null);
  const [document, setDocument] = useState<WorkflowEditorDocument | null>(null);
  const [catalog, setCatalog] = useState<WorkflowEditorCatalog>(emptyCatalog());
  const [runSnapshot, setRunSnapshot] = useState<WorkflowRunSnapshot | null>(null);
  const [flowNodes, setFlowNodes] = useState<Node<GraphNodeData>[]>([]);
  const [flowEdges, setFlowEdges] = useState<Edge[]>([]);
  const [flowInstance, setFlowInstance] = useState<ReactFlowInstance | null>(null);
  const [selectedNodeIds, setSelectedNodeIds] = useState<string[]>([]);
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
  const [clearingRunIds, setClearingRunIds] = useState<Record<string, boolean>>({});
  const [resumingRunIds, setResumingRunIds] = useState<Record<string, boolean>>({});
  const [snapGuides, setSnapGuides] = useState<SnapGuide[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [sidebarWidth, setSidebarWidth] = useState<number>(() => readStoredWorkflowSidebarWidth());
  const [isResizingSidebar, setIsResizingSidebar] = useState(false);
  const editLocked = !privilegedUnlocked;

  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(WORKFLOW_SIDEBAR_WIDTH_STORAGE_KEY, String(sidebarWidth));
  }, [sidebarWidth]);

  useEffect(() => {
    if (typeof window === "undefined") return;

    const handleResize = () => {
      if (window.innerWidth <= WORKFLOW_COMPACT_BREAKPOINT) return;
      const containerWidth = appShellRef.current?.getBoundingClientRect().width ?? window.innerWidth;
      setSidebarWidth((current) => clampWorkflowSidebarWidth(current, containerWidth));
    };

    handleResize();
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;

    const handlePointerMove = (event: PointerEvent) => {
      const resizeState = sidebarResizeRef.current;
      if (!resizeState || event.pointerId !== resizeState.pointerId) return;
      const containerWidth = appShellRef.current?.getBoundingClientRect().width ?? window.innerWidth;
      const delta = event.clientX - resizeState.startX;
      setSidebarWidth(clampWorkflowSidebarWidth(resizeState.startWidth + delta, containerWidth));
    };

    const stopResize = (event?: PointerEvent) => {
      const resizeState = sidebarResizeRef.current;
      if (!resizeState) return;
      if (event && event.pointerId !== resizeState.pointerId) return;
      sidebarResizeRef.current = null;
      setIsResizingSidebar(false);
    };

    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", stopResize);
    window.addEventListener("pointercancel", stopResize);

    return () => {
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", stopResize);
      window.removeEventListener("pointercancel", stopResize);
    };
  }, []);

  useEffect(() => {
    if (!isResizingSidebar || typeof globalThis.document === "undefined") return;
    const previousCursor = globalThis.document.body.style.cursor;
    const previousUserSelect = globalThis.document.body.style.userSelect;
    globalThis.document.body.style.cursor = "col-resize";
    globalThis.document.body.style.userSelect = "none";
    return () => {
      globalThis.document.body.style.cursor = previousCursor;
      globalThis.document.body.style.userSelect = previousUserSelect;
    };
  }, [isResizingSidebar]);

  useEffect(() => {
    Promise.all([
      fetchJson<WorkflowEditorDocument>("/api/workflow-editor/document"),
      fetchJson<WorkflowEditorCatalog>("/api/workflow-editor/catalog"),
      fetchJson<WorkflowRunSnapshot>("/api/workflow-editor/runs"),
    ])
      .then(([payload, catalogPayload, runSnapshotPayload]) => {
        const normalizedConfig = normalizeDerivedNodeFields(normalizeWorkflowConfigShape(payload.config));
        setCatalog(catalogPayload);
        setRunSnapshot(runSnapshotPayload);
        setDocument({ ...payload, config: normalizedConfig });
        setFlowNodes(buildNodes(normalizedConfig, payload.layout, catalogPayload, runSnapshotPayload.node_activity));
        setFlowEdges(buildEdges(normalizedConfig));
      })
      .catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : String(reason));
      });
  }, []);

  useEffect(() => {
    const stream = new EventSource("/api/workflow-editor/runs/stream");

    stream.addEventListener("snapshot", (event) => {
      if (!(event instanceof MessageEvent)) return;
      try {
        const payload = JSON.parse(event.data) as WorkflowRunSnapshot;
        setRunSnapshot(payload);
      } catch {
        // Ignore malformed event payloads and keep last known snapshot.
      }
    });

    stream.addEventListener("error", () => {
      // Keep the last snapshot visible when the stream reports an error.
    });

    return () => stream.close();
  }, []);

  async function clearWorkflowRun(eventId: string, runState?: string) {
    const normalizedEventId = eventId.trim();
    if (!normalizedEventId || clearingRunIds[normalizedEventId] || editLocked) return;
    const verb = runState === "errored" ? "Dismiss" : "Clear";
    if (!window.confirm(`${verb} workflow run ${normalizedEventId}?`)) return;
    setClearingRunIds((current) => ({ ...current, [normalizedEventId]: true }));
    try {
      await fetchJson(`/api/workflow-editor/runs/${encodeURIComponent(normalizedEventId)}`, {
        method: "DELETE",
      });
      setRunSnapshot((current) => {
        if (!current) return current;
        return {
          ...current,
          runs: current.runs.filter((run) => run.event_id !== normalizedEventId),
        };
      });
      setError(null);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setClearingRunIds((current) => {
        const next = { ...current };
        delete next[normalizedEventId];
        return next;
      });
    }
  }

  async function resumeWorkflowRun(eventId: string) {
    const normalizedEventId = eventId.trim();
    if (!normalizedEventId || resumingRunIds[normalizedEventId] || editLocked) return;
    setResumingRunIds((current) => ({ ...current, [normalizedEventId]: true }));
    try {
      await fetchJson(`/api/workflow-editor/runs/${encodeURIComponent(normalizedEventId)}/resume`, {
        method: "POST",
      });
      setError(null);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setResumingRunIds((current) => {
        const next = { ...current };
        delete next[normalizedEventId];
        return next;
      });
    }
  }

  function startSidebarResize(event: ReactPointerEvent<HTMLDivElement>) {
    if (event.button !== 0 || typeof window === "undefined" || window.innerWidth <= WORKFLOW_COMPACT_BREAKPOINT) return;
    sidebarResizeRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startWidth: sidebarWidth,
    };
    setIsResizingSidebar(true);
    event.preventDefault();
  }

  function handleSidebarResizeKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (typeof window === "undefined" || window.innerWidth <= WORKFLOW_COMPACT_BREAKPOINT) return;
    const containerWidth = appShellRef.current?.getBoundingClientRect().width ?? window.innerWidth;
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      setSidebarWidth((current) => clampWorkflowSidebarWidth(current - WORKFLOW_SIDEBAR_RESIZE_STEP, containerWidth));
      return;
    }
    if (event.key === "ArrowRight") {
      event.preventDefault();
      setSidebarWidth((current) => clampWorkflowSidebarWidth(current + WORKFLOW_SIDEBAR_RESIZE_STEP, containerWidth));
      return;
    }
    if (event.key === "Home") {
      event.preventDefault();
      setSidebarWidth(WORKFLOW_SIDEBAR_MIN_WIDTH);
      return;
    }
    if (event.key === "End") {
      event.preventDefault();
      setSidebarWidth(maxWorkflowSidebarWidth(containerWidth));
    }
  }

  useEffect(() => {
    if (!document) return;
    setFlowNodes((currentNodes) =>
      buildNodes(
        document.config,
        document.layout,
        catalog,
        runSnapshot?.node_activity ?? {},
        currentPositionsFromNodes(currentNodes),
        currentNodes.filter((node) => node.selected).map((node) => node.id),
      ),
    );
    setFlowEdges(buildEdges(document.config));
  }, [catalog, document, runSnapshot]);

  useEffect(() => {
    if (!flowInstance || flowNodes.length === 0 || hasAutoFitRef.current) return;
    queueMicrotask(() => {
      flowInstance.fitView({ padding: 0.2 });
      hasAutoFitRef.current = true;
    });
  }, [flowInstance, flowNodes, flowEdges]);

  const config = document?.config ?? emptyConfig();
  const layout = document?.layout ?? emptyLayout();
  const visibleRuns = runSnapshot?.runs ?? [];
  const selectedNode = selectedNodeIds.length === 1 && selectedNodeId ? nodeById(config, selectedNodeId) : undefined;
  const selectedCatalogEntry = selectedNode ? getCatalogEntry(catalog, selectedNode.type) : undefined;
  const selectedEdge = selectedEdgeId
    ? config.edges.find((edge) => edgeId(edge) === selectedEdgeId)
    : undefined;
  const selectedNodeBindingOptions = selectedNode ? upstreamBindingOptions(config, selectedNode.id) : [];
  const hasCollapsedTargetUi =
    selectedNode?.type === "effect:vm:copy-files" ||
    selectedNode?.type === "effect:agent:prompt" ||
    selectedNode?.type === "effect:agent:exec" ||
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
      if (field.kind !== "object_list") continue;
      const items: unknown[] = Array.isArray(nextKnownValues[field.key]) ? (nextKnownValues[field.key] as unknown[]) : [];
      items.forEach((item: unknown, index: number) => {
        const itemValue = isRecord(item) ? item : {};
        field.item_fields.forEach((itemField) => {
          if (itemField.kind !== "json") return;
          const draftKey = objectListJsonDraftKey(field.key, index, itemField.key);
          const nestedValue = itemValue[itemField.key];
          nextJsonDrafts[draftKey] = nestedValue === undefined
            ? (itemField.required ? "{}" : "")
            : formatJson(nestedValue, itemField.required ? "{}" : "");
        });
      });
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

  useEffect(() => {
    const nextSelectedNodeIds = flowNodes.filter((node) => node.selected).map((node) => node.id);
    setSelectedNodeIds(nextSelectedNodeIds);
    setSelectedNodeId((currentSelectedNodeId) => {
      if (nextSelectedNodeIds.length === 0) return null;
      if (currentSelectedNodeId && nextSelectedNodeIds.includes(currentSelectedNodeId)) return currentSelectedNodeId;
      return nextSelectedNodeIds[nextSelectedNodeIds.length - 1] ?? null;
    });
  }, [flowNodes]);

  function patchDocument(nextConfig: WorkflowConfig, nextLayout: WorkflowLayout) {
    if (!document) return;
    const normalizedConfig = normalizeDerivedNodeFields(normalizeWorkflowConfigShape(nextConfig));
    const nextDocument = {
      ...document,
      config: normalizedConfig,
      layout: nextLayout,
    };
    setDocument(nextDocument);
    setSaveState("idle");
  }

  function setProgrammaticSelection(nextNodeIds: string[], preferredNodeId: string | null = null) {
    const uniqueNodeIds = Array.from(new Set(nextNodeIds));
    setSelectedNodeIds(uniqueNodeIds);
    setSelectedNodeId((currentSelectedNodeId) => {
      if (uniqueNodeIds.length === 0) return null;
      if (preferredNodeId && uniqueNodeIds.includes(preferredNodeId)) return preferredNodeId;
      if (currentSelectedNodeId && uniqueNodeIds.includes(currentSelectedNodeId)) return currentSelectedNodeId;
      return uniqueNodeIds[uniqueNodeIds.length - 1] ?? null;
    });
    setFlowNodes((currentNodes) =>
      currentNodes.map((node) => {
        const isSelected = uniqueNodeIds.includes(node.id);
        return node.selected === isSelected ? node : { ...node, selected: isSelected };
      }),
    );
  }

  function updateNodePositions(changes: NodeChange[]) {
    setFlowNodes((currentNodes) => applyNodeChanges(changes, currentNodes) as Node<GraphNodeData>[]);
  }

  function updateDraggedNodePositions(activeNode: Node, draggedNodes: Node[]) {
    setFlowNodes((currentNodes) => {
      const draggedNodeIds = new Set(draggedNodes.map((node) => node.id));
      const typedDraggedNodes = draggedNodes as Node<GraphNodeData>[];
      const { snappedNodes, guides } = snapDraggedNodes(activeNode, typedDraggedNodes, currentNodes);
      setSnapGuides(guides);
      const snappedById = new Map(snappedNodes.map((node) => [node.id, node]));
      return currentNodes.map((node) => snappedById.get(node.id) ?? node);
    });
  }

  function updateEdgeChanges(changes: Parameters<typeof applyEdgeChanges>[0]) {
    setFlowEdges((currentEdges) => applyEdgeChanges(changes, currentEdges));
  }

  function deleteSelectedEdge() {
    if (!document || !selectedEdgeId || editLocked) return;
    const nextConfig = cloneConfig(document.config);
    nextConfig.edges = nextConfig.edges.filter(
      (edge) => edgeId(edge) !== selectedEdgeId,
    );
    patchDocument(nextConfig, cloneLayout(document.layout));
    setSelectedEdgeId(null);
    setError(null);
  }

  function deleteNodes(nodeIds: string[]) {
    if (!document || nodeIds.length === 0 || editLocked) return;
    const deletedNodeIds = new Set(nodeIds);
    const nextConfig = cloneConfig(document.config);
    nextConfig.triggers = (nextConfig.triggers ?? []).filter((node) => !deletedNodeIds.has(node.id));
    nextConfig.gates = (nextConfig.gates ?? []).filter((node) => !deletedNodeIds.has(node.id));
    nextConfig.effects = (nextConfig.effects ?? []).filter((node) => !deletedNodeIds.has(node.id));
    nextConfig.queries = (nextConfig.queries ?? []).filter((node) => !deletedNodeIds.has(node.id));
    nextConfig.edges = nextConfig.edges.filter(
      (edge) => !deletedNodeIds.has(edge.source_id) && !deletedNodeIds.has(edge.target_id),
    );
    const nextLayout = cloneLayout(document.layout);
    for (const nodeId of deletedNodeIds) {
      delete nextLayout.nodes[nodeId];
    }
    patchDocument(nextConfig, nextLayout);
    setProgrammaticSelection([]);
    setSelectedEdgeId(null);
    setError(null);
  }

  function handleAppShellKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    if (isEditableEventTarget(event.target)) return;
    const isCopy = (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "c";
    const isPaste = (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "v";
    if (isCopy) {
      if (!selectedNode) return;
      event.preventDefault();
      copiedNodeRef.current = cloneNodeConfig(selectedNode);
      setError(null);
      return;
    }
    if (isPaste) {
      if (!document || !copiedNodeRef.current || editLocked) return;
      event.preventDefault();
      const templateNode = copiedNodeRef.current;
      const templateKind = nodeKindFromType(templateNode.type);
      const nextConfig = cloneConfig(document.config);
      nextConfig.triggers ??= [];
      nextConfig.gates ??= [];
      nextConfig.effects ??= [];
      nextConfig.queries ??= [];
      const nextId = generateNodeId(nextConfig, templateKind, templateNode.type);
      const duplicatedNode = cloneNodeConfig(templateNode);
      duplicatedNode.id = nextId;
      duplicatedNode.display_name = `${templateNode.display_name} Copy`;
      nextConfig[kindCollectionKey(templateKind)].push(duplicatedNode);
      const currentPosition =
        flowNodes.find((node) => node.id === templateNode.id)?.position ??
        document.layout.nodes[templateNode.id] ??
        fallbackPosition(0);
      const nextLayout = cloneLayout(document.layout);
      nextLayout.nodes[nextId] = { x: currentPosition.x + 40, y: currentPosition.y + 40 };
      patchDocument(nextConfig, nextLayout);
      setProgrammaticSelection([nextId], nextId);
      setSelectedEdgeId(null);
      setError(null);
      return;
    }
    if (!selectedEdgeId) return;
    if (event.key !== "Delete" && event.key !== "Backspace") return;
    event.preventDefault();
    deleteSelectedEdge();
  }

  function persistNodePositions(nodes: Node[]) {
    if (!document || editLocked) return;
    setSnapGuides([]);
    const nextLayout = cloneLayout(document.layout);
    for (const node of nodes) {
      nextLayout.nodes[node.id] = node.position;
    }
    setDocument({
      ...document,
      layout: nextLayout,
    });
    setSaveState("idle");
  }

  function addNode(kind: WorkflowNodeKind, type: string) {
    if (!document || editLocked) return;
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
    setProgrammaticSelection([node.id], node.id);
    setSelectedEdgeId(null);
    setError(null);
  }

  function deleteSelectedNode() {
    deleteNodes(selectedNodeIds);
  }

  function selectDownstreamBranch() {
    if (!document || !selectedNodeId) return;
    const queuedNodeIds = [selectedNodeId];
    const selectedNodeSet = new Set<string>();
    while (queuedNodeIds.length > 0) {
      const currentNodeId = queuedNodeIds.shift();
      if (!currentNodeId || selectedNodeSet.has(currentNodeId)) continue;
      selectedNodeSet.add(currentNodeId);
      for (const edge of document.config.edges) {
        if (edge.source_id === currentNodeId && !selectedNodeSet.has(edge.target_id)) {
          queuedNodeIds.push(edge.target_id);
        }
      }
    }
    setProgrammaticSelection([...selectedNodeSet], selectedNodeId);
    setSelectedEdgeId(null);
  }

  function applyInspectorChanges() {
    if (!document || !selectedNode || editLocked) return;
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
      for (const field of selectedCatalogEntry?.fields ?? []) {
        if (field.kind !== "object_list") continue;
        const currentItems = Array.isArray(nextKnownValues[field.key]) ? [...(nextKnownValues[field.key] as unknown[])] : [];
        field.item_fields.forEach((itemField) => {
          if (itemField.kind !== "json") return;
          currentItems.forEach((item, index) => {
            if (!isRecord(item)) return;
            const draftKey = objectListJsonDraftKey(field.key, index, itemField.key);
            const currentValue = item[itemField.key];
            if (isExactBindingValue(currentValue)) return;
            const fallbackValue = itemField.required ? {} : undefined;
            const parsedValue = parseJsonDraft(inspectorJsonDrafts[draftKey] ?? "", fallbackValue);
            if (parsedValue === undefined) {
              delete item[itemField.key];
            } else {
              item[itemField.key] = parsedValue;
            }
          });
        });
        nextKnownValues[field.key] = currentItems;
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
        ...edge,
        source_id: edge.source_id === selectedNode.id ? rawId : edge.source_id,
        target_id: edge.target_id === selectedNode.id ? rawId : edge.target_id,
      }));
      const nextLayout = cloneLayout(document.layout);
      if (rawId !== selectedNode.id) {
        nextLayout.nodes[rawId] = nextLayout.nodes[selectedNode.id] ?? fallbackPosition(0);
        delete nextLayout.nodes[selectedNode.id];
      }
      patchDocument(nextConfig, nextLayout);
      setProgrammaticSelection([rawId], rawId);
      setSelectedEdgeId(null);
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  function onConnect(connection: Connection) {
    if (!document || !connection.source || !connection.target || editLocked) return;
    const validationError = validateConnection(
      document.config,
      catalog,
      connection.source,
      connection.target,
      connection.sourceHandle,
    );
    if (validationError) {
      setError(validationError);
      return;
    }
    const targetNode = nodeById(document.config, connection.target);
    const allowCycle = targetNode?.type === "gate:for" && wouldCreateCycle(document.config, connection.source, connection.target);
    const nextConfig = cloneConfig(document.config);
    const nextEdge: WorkflowEdge = {
      source_id: connection.source,
      target_id: connection.target,
    };
    if (connection.sourceHandle) {
      nextEdge.source_port = connection.sourceHandle;
    }
    if (allowCycle) {
      nextEdge.allow_cycle = true;
    }
    nextConfig.edges = [
      ...nextConfig.edges,
      nextEdge,
    ];
    patchDocument(nextConfig, cloneLayout(document.layout));
    setSelectedEdgeId(null);
    setError(null);
  }

  function onEdgesDelete(deletedEdges: Edge[]) {
    if (!document || editLocked) return;
    const deleted = new Set(deletedEdges.map((edge) => edge.id));
    const nextConfig = cloneConfig(document.config);
    nextConfig.edges = nextConfig.edges.filter(
      (edge) => !deleted.has(edgeId(edge)),
    );
    patchDocument(nextConfig, cloneLayout(document.layout));
    setSelectedEdgeId(null);
  }

  function onNodesDelete(deletedNodes: Node[]) {
    if (editLocked) return;
    deleteNodes(deletedNodes.map((node) => node.id));
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
    if (!document || editLocked) return;
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
    const isDisabled = effectTargetField.read_only || editLocked;
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
    if (editLocked) return;
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
    if (editLocked) return;
    setInspectorKnownValues((current) => {
      const currentItems = Array.isArray(current[key]) ? [...current[key]] : [];
      currentItems.splice(index, 1);
      return { ...current, [key]: currentItems };
    });
  }

  function renderField(field: WorkflowEditorFieldDefinition) {
    const value = inspectorKnownValues[field.key];
    const mode = inspectorFieldModes[field.key] ?? fieldValueMode(field, value);
    const isDisabled = field.read_only || editLocked;

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
                  <button type="button" className="subtle-button" onClick={() => removeObjectListItem(field.key, index)} disabled={isDisabled}>
                    Remove
                  </button>
                </div>
                {field.item_fields.map((itemField) => {
                  const currentValue = itemValue[itemField.key];
                  const currentMode = fieldValueMode(itemField, currentValue);
                  const nestedJsonKey = objectListJsonDraftKey(field.key, index, itemField.key);
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
                        : itemField.kind === "json"
                          ? (
                            <textarea
                              value={inspectorJsonDrafts[nestedJsonKey] ?? ""}
                              rows={6}
                              onChange={(event) =>
                                setInspectorJsonDrafts((current) => ({
                                  ...current,
                                  [nestedJsonKey]: event.target.value,
                                }))
                              }
                            />
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
          <button type="button" className="subtle-button" onClick={() => addObjectListItem(field.key, field)} disabled={isDisabled}>
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
  const appShellStyle = {
    "--workflow-sidebar-width": `${sidebarWidth}px`,
  } as CSSProperties;

  return (
    <div className="admin-page workflow-embedded-page">
      <div
        className={`app-shell workflow-editor-shell${isResizingSidebar ? " is-resizing-sidebar" : ""}`}
        ref={appShellRef}
        style={appShellStyle}
        tabIndex={-1}
        onKeyDown={handleAppShellKeyDown}
      >
          <aside className="sidebar">
            {editLocked ? <div className="callout warning-callout workflow-lock-callout">Unlock privileged operations to modify workflow state or clear runtime runs.</div> : null}

            <details className="sidebar-section current-runs-section">
              <summary className="current-runs-summary">
                <span className="section-label current-runs-label">Current runs</span>
                <span className="current-runs-count">{visibleRuns.length}</span>
              </summary>
              <p className="runtime-summary">
                {visibleRuns.length === 0
                  ? "No active or errored workflow runs."
                  : `${visibleRuns.length} ${visibleRuns.length === 1 ? "run" : "runs"} visible on the graph.`}
              </p>
              <div className="runtime-runs">
                {visibleRuns.map((run: WorkflowRunSummary) => (
                  <div key={run.event_id} className="runtime-run-card">
                    <div className="runtime-run-header">
                      <div className="runtime-run-event">{run.event_id}</div>
                      <div className={`runtime-run-state state-${run.run_state === "errored" ? "failed" : run.delivery_state ?? "active"}`}>
                        {run.run_state === "errored" ? "errored" : run.delivery_state ?? "active"}
                      </div>
                    </div>
                    <div className="runtime-run-meta">
                      Last activity {formatRuntimeTimestamp(run.latest_activity_at ?? run.occurred_at)}
                    </div>
                    {run.run_state === "errored" ? (
                      <div className="runtime-run-error">
                        Failed at <strong>{run.failed_node_id ?? "unknown node"}</strong>
                        {run.error ? `: ${run.error}` : ""}
                      </div>
                    ) : null}
                    <div className="runtime-run-nodes">
                      {run.active_nodes.map((activity, index) => (
                        <div key={`${run.event_id}-${activity.node_id}-${activity.status}-${index}`} className="runtime-run-node">
                          {summarizeRunNode(activity)}
                        </div>
                      ))}
                    </div>
                    <div className="runtime-run-actions">
                      {run.run_state === "errored" && run.resumable ? (
                        <button
                          type="button"
                          className="subtle-button"
                          onClick={() => void resumeWorkflowRun(run.event_id)}
                          disabled={Boolean(resumingRunIds[run.event_id]) || editLocked}
                        >
                          {resumingRunIds[run.event_id] ? "Resuming..." : "Resume from failure"}
                        </button>
                      ) : null}
                      <button
                        type="button"
                        className="subtle-button runtime-run-clear"
                          onClick={() => void clearWorkflowRun(run.event_id, run.run_state)}
                        disabled={Boolean(clearingRunIds[run.event_id]) || editLocked}
                      >
                        {clearingRunIds[run.event_id]
                          ? run.run_state === "errored"
                            ? "Dismissing..."
                            : "Clearing..."
                          : run.run_state === "errored"
                            ? "Dismiss run"
                            : "Clear run"}
                      </button>
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
                    <Tooltip content={group.description}>
                      <span>{group.label}</span>
                    </Tooltip>
                    <span className="create-group-count">{group.entries.length}</span>
                  </summary>
                  <p className="create-group-description">{group.description}</p>
                  <div className="create-group-actions">
                    {group.entries.map((entry) => (
                      <Tooltip key={entry.type} content={nodeTypeDescription(entry.type, entry.kind)} inline={false}>
                        <button onClick={() => addNode(entry.kind, entry.type)} disabled={editLocked}>
                          {entry.label}
                        </button>
                      </Tooltip>
                    ))}
                  </div>
                </details>
              ))}
            </div>

            <div className="sidebar-section">
              <div className="section-label">Save</div>
              <button onClick={() => void validateDocument()}>Validate</button>
              <button className="primary" onClick={() => void saveDocument()} disabled={editLocked}>
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

            {selectedNodeIds.length > 1 ? (
              <div className="sidebar-section inspector">
                <div className="section-label">Selection</div>
                <div className="inspector-summary">
                  <div>{selectedNodeIds.length} nodes selected</div>
                </div>
                <button onClick={() => setProgrammaticSelection([])}>Clear selection</button>
                <button onClick={selectDownstreamBranch} disabled={!selectedNodeId}>
                  Select downstream branch
                </button>
                <button className="danger" onClick={deleteSelectedNode} disabled={editLocked}>
                  Delete selected nodes
                </button>
              </div>
            ) : null}

            {selectedNode ? (
              <div className="sidebar-section inspector">
                <div className="section-label">Inspector</div>
                <div className="inspector-summary">
                  <div>{selectedNode.type}</div>
                  <div>{selectedNode.id}</div>
                </div>
                <button onClick={selectDownstreamBranch}>Select downstream branch</button>
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

                <button className="primary" onClick={applyInspectorChanges} disabled={editLocked}>
                  Apply node changes
                </button>
                <button className="danger" onClick={deleteSelectedNode} disabled={editLocked}>
                  Delete node
                </button>
              </div>
            ) : null}

            {selectedEdge ? (
              <div className="sidebar-section inspector">
                <div className="section-label">Edge</div>
                <div className="inspector-summary">
                  <div>{selectedEdge.source_id}</div>
                  {selectedEdge.source_port ? <div>Port: {selectedEdge.source_port}</div> : null}
                  <div>{selectedEdge.target_id}</div>
                  {selectedEdge.allow_cycle ? <div>Cycle return</div> : null}
                </div>
                <button className="danger" onClick={deleteSelectedEdge} disabled={editLocked}>
                  Delete edge
                </button>
              </div>
            ) : null}

            {error ? <div className="error-box">{error}</div> : null}
          </aside>

          <div
            className="workflow-sidebar-resize-handle"
            role="separator"
            aria-label="Resize workflow sidebar"
            aria-orientation="vertical"
            aria-valuemin={WORKFLOW_SIDEBAR_MIN_WIDTH}
            aria-valuemax={maxWorkflowSidebarWidth(appShellRef.current?.getBoundingClientRect().width ?? sidebarWidth + WORKFLOW_SIDEBAR_CANVAS_MIN_WIDTH)}
            aria-valuenow={sidebarWidth}
            tabIndex={0}
            onPointerDown={startSidebarResize}
            onKeyDown={handleSidebarResizeKeyDown}
          />

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
                appShellRef.current?.focus();
              }}
              onEdgeClick={(_, edge) => {
                setProgrammaticSelection([]);
                setSelectedEdgeId(edge.id);
              }}
              onNodeDrag={(_, node, nodes) => updateDraggedNodePositions(node, nodes.length > 0 ? nodes : [node])}
              onNodeDragStop={(_, node, nodes) => persistNodePositions(nodes.length > 0 ? nodes : [node])}
              onPaneClick={() => {
                setSelectedEdgeId(null);
                setSnapGuides([]);
              }}
              onConnect={onConnect}
              onEdgesDelete={onEdgesDelete}
              onNodesDelete={onNodesDelete}
              nodesDraggable={!editLocked}
              nodesConnectable={!editLocked}
              elementsSelectable
              fitView
              deleteKeyCode={["Backspace", "Delete"]}
              multiSelectionKeyCode={["Control", "Meta"]}
            >
              <Background gap={20} size={1} />
            </ReactFlow>
            {flowInstance
              ? snapGuides.map((guide) => {
                  const viewport = flowInstance.getViewport();
                  const scaledPosition = guide.position * viewport.zoom;
                  const offset = guide.orientation === "vertical" ? viewport.x : viewport.y;
                  const style =
                    guide.orientation === "vertical"
                      ? { left: `${scaledPosition + offset}px` }
                      : { top: `${scaledPosition + offset}px` };
                  return (
                    <div
                      key={`${guide.orientation}-${guide.position}`}
                      className={`snap-guide snap-guide-${guide.orientation}`}
                      style={style}
                    />
                  );
                })
              : null}
          </main>
        </div>
    </div>
  );
}
