import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type DragEvent as ReactDragEvent,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";
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
  type Viewport,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  type WorkflowConfig,
  type WorkflowEdge,
  type WorkflowGraphMetadata,
  type WorkflowNodeActivityCounts,
  type WorkflowEditorCatalog,
  type WorkflowEditorDocument,
  type WorkflowEditorFieldDefinition,
  type WorkflowEditorNodeCatalogEntry,
  type WorkflowEditorNodePortDefinition,
  type WorkflowEditorSave,
  type WorkflowEditorValidation,
  type WorkflowLayout,
  type WorkflowNodeConfig,
  type WorkflowNodeKind,
  type WorkflowOutputSchemas,
  type WorkflowRunNodeActivity,
  type WorkflowRunSnapshot,
  type WorkflowRunSummary,
  type WorkflowValidationDiagnostic,
} from "./types";
import { useAdminUi } from "./features/admin/AdminContext";
import { Tooltip } from "./features/admin/Tooltip";
import {
  BindingParseError,
  isParsedBindingExpression,
  isParsedBindingReference,
  parseBindingExpression,
  rewriteWorkflowNodeConfigBindings,
  type ParsedBindingExpression,
  type ParsedBindingReference,
} from "./workflowBindings";
import {
  normalizeWorkflowSourcePort,
  workflowConnectionRouteOptions,
  workflowEdgeRouteLabel,
  workflowRouteClass,
  type WorkflowConnectionRouteOption,
} from "./workflowRoutes";

type GraphNodeData = {
  id: string;
  displayName: string;
  kind: WorkflowNodeKind;
  type: string;
  description: string;
  inputPorts: ReadonlyArray<WorkflowEditorNodePortDefinition>;
  outputPorts: ReadonlyArray<WorkflowEditorNodePortDefinition>;
  dominantStatus: string | null;
  statusBadges: ReadonlyArray<GraphNodeStatusBadge>;
  diagnosticCount: number;
  hasDiagnostics: boolean;
  pendingConnectionRole?: PendingConnectionRole | null;
};

type GraphNodeStatusBadge = {
  status: string;
  count: number;
  label: string;
  title: string;
  compact: boolean;
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

type WorkflowGraphLabel = {
  graph: WorkflowGraphMetadata;
  left: number;
  top: number;
};

type WorkflowGraphView = "directory" | "private";

type CanvasPoint = {
  x: number;
  y: number;
};

type PendingConnectionState = {
  sourceId: string;
  sourcePort: string | null;
  routeLabel: string;
  routeClass: string;
  sourcePoint: CanvasPoint;
  cursorPoint: CanvasPoint;
};

type NodeConnectionMenuState = {
  nodeId: string;
  position: CanvasPoint;
  sourcePoint: CanvasPoint;
  routes: WorkflowConnectionRouteOption[];
};

type PendingConnectionRole = "source" | "valid-target" | "invalid-target";

type CopiedWorkflowNode = {
  node: WorkflowNodeConfig;
  position: { x: number; y: number };
};

type CopiedWorkflowSelection = {
  nodes: CopiedWorkflowNode[];
  edges: WorkflowEdge[];
  primaryNodeId: string | null;
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
  "trigger:event": "Start when an internal workflow event is emitted.",
  "trigger:ticket:created": "Start when a tracked ticket is created.",
  "trigger:ticket:changed": "Start when a tracked ticket changes state or metadata.",
  "trigger:issue:comment-created": "Start when a tracked issue receives a new comment.",
  "trigger:issue:new-comment": "Start when a tracked issue receives a new comment.",
  "trigger:discord:new-message": "Start from new Discord messages that match the configured filters.",
  "trigger:teams:new-message": "Start from new Microsoft Teams messages that match the configured targets.",
  "trigger:email:new-message": "Start from newly ingested email messages for the configured mailbox scope.",
  "trigger:pull-request:new-feedback": "Start when new pull-request feedback arrives from the configured provider.",
  "trigger:git:push": "Start from Git push events captured by the host-side bare-repo hook.",
  "trigger:agent:run:completed": "Start when a guest agent run completes.",
  "gate:filter": "Allow only matching events to continue downstream.",
  "gate:map": "Project upstream outputs into a new payload shape.",
  "gate:deserialize": "Parse a JSON or YAML string into object properties for downstream bindings.",
  "gate:serialize": "Serialize a bindable value as JSON or YAML text for downstream nodes.",
  "gate:merge": "Merge compatible inbound event streams into one downstream path.",
  "gate:debounce": "Wait for a quiet period, then forward the latest activation.",
  "gate:dedupe": "Suppress duplicate activations for the same key until a TTL expires.",
  "gate:await-agent": "Pause the workflow until a selected agent session completes.",
  "gate:for": "Iterate over an array and execute the body branch for each item.",
  "effect:agent:prompt": "Send a prompt to an agent session.",
  "effect:agent:exec": "Run a shell command in an agent session.",
  "effect:agent:destroy": "Destroy an agent session and its backend target.",
  "effect:semantic:ensure-collection": "Ensure a semantic collection exists with the configured vector schema.",
  "effect:semantic:upsert": "Embed one text string and upsert it as one semantic point.",
  "effect:semantic:delete": "Delete semantic points by explicit point IDs.",
  "effect:vm:create": "Create a VM for the workflow.",
  "effect:vm:clone-repo": "Clone a repository into a target VM.",
  "effect:vm:bootstrap-python": "Bootstrap a Python checkout and environment in a VM.",
  "effect:vm:bootstrap-frontend": "Bootstrap frontend dependencies in a VM.",
  "effect:vm:start": "Start an existing VM.",
  "effect:vm:stop": "Stop an existing VM.",
  "effect:vm:exec": "Run a shell command inside a VM.",
  "effect:vm:copy-files": "Copy rendered file content into a VM.",
  "effect:vm:delete-files": "Delete files from a VM.",
  "effect:event:emit": "Emit an internal workflow event for other graphs.",
  "effect:issue:update-metadata": "Patch metadata on a tracked issue.",
  "effect:session:update-metadata": "Patch session metadata on the host.",
  "effect:git:promote-agent-branch": "Promote an agent branch into a trusted host checkout.",
  "effect:pr:create": "Create a pull request from the host side.",
  "query:vm:run-state": "Read the current run state for a VM.",
  "query:git:pr-note": "Read pull-request note context from a host checkout.",
  "query:session-context": "Read session context and metadata.",
  "query:agent:artifact-text": "Read a UTF-8 text file from an agent session artifacts folder.",
  "query:issue:get": "Read a local issue record by id.",
  "query:ado:work-items": "Query Azure DevOps work items.",
  "query:semantic:store-status": "Read semantic store health and collection status.",
  "query:semantic:search": "Search a semantic collection with an embedded text query.",
  "query:semantic:get": "Retrieve semantic points by explicit point IDs.",
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
const NODE_TYPE_DRAG_DATA_MIME = "application/x-agent-sandbox-workflow-node-type";

type SidebarResizeState = {
  pointerId: number;
  startX: number;
  startWidth: number;
};

type NodeTypeDragPayload = {
  kind: WorkflowNodeKind;
  type: string;
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
  const typeDescription =
    nodeData.description || NODE_TYPE_DESCRIPTIONS[nodeData.type] || `${nodeData.type} node.`;
  return (
    <div
      className={`node-card node-${nodeData.kind}${selected ? " selected" : ""}${
        nodeData.dominantStatus ? ` runtime-${nodeData.dominantStatus}` : ""
      }${nodeData.hasDiagnostics ? " invalid" : ""}${
        nodeData.pendingConnectionRole ? ` connection-${nodeData.pendingConnectionRole}` : ""
      }`}
    >
      <Handle type="target" position={Position.Left} className="workflow-handle workflow-technical-handle" />
      {nodeData.inputPorts.map((port) => (
        <Handle
          key={`input-${port.key}`}
          id={port.key}
          type="target"
          position={Position.Left}
          className="workflow-handle workflow-technical-handle"
        />
      ))}
      <div className="node-drag-surface">
        <div className="node-heading">
          <Tooltip content={`${kindDescription} ${typeDescription}`}>
            <div className="node-kind">{nodeData.kind}</div>
          </Tooltip>
          <div className="node-badges">
            {nodeData.hasDiagnostics ? (
              <Tooltip content={`${nodeData.diagnosticCount} workflow binding problem${nodeData.diagnosticCount === 1 ? "" : "s"}`}>
                <div className="node-diagnostic-badge">{nodeData.diagnosticCount}</div>
              </Tooltip>
            ) : null}
            {nodeData.statusBadges.map((badge) => (
              <Tooltip key={badge.status} content={badge.title}>
                <div
                  className={`node-runtime-badge status-${badge.status}${badge.compact ? " compact" : ""}`}
                  aria-label={badge.title}
                >
                  {badge.label}
                </div>
              </Tooltip>
            ))}
          </div>
        </div>
        <Tooltip content={typeDescription}>
          <div className="node-title">{nodeData.displayName}</div>
        </Tooltip>
        <div className="node-id">{nodeData.id}</div>
      </div>
      <Handle type="source" position={Position.Right} className="workflow-handle workflow-technical-handle" />
      {nodeData.outputPorts.map((port) => (
        <Handle
          key={`output-${port.key}`}
          id={port.key}
          type="source"
          position={Position.Right}
          className="workflow-handle workflow-technical-handle"
        />
      ))}
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

function emptyCatalog(): WorkflowEditorCatalog {
  return { worker_id: "trigger-orchestrator", nodes: [] };
}

function nodeCollections(config: WorkflowConfig) {
  return {
    triggers: config.triggers ?? [],
    gates: config.gates ?? [],
    effects: config.effects ?? [],
    queries: config.queries ?? [],
  };
}

function workflowNodes(config: WorkflowConfig): WorkflowNodeConfig[] {
  const collections = nodeCollections(config);
  return [
    ...collections.triggers,
    ...collections.gates,
    ...collections.effects,
    ...collections.queries,
  ];
}

function workflowNodeIds(config: WorkflowConfig): string[] {
  return workflowNodes(config).map((node) => node.id);
}

function workflowGraphKey(nodeIds: string[]): string {
  return [...nodeIds].sort().join("\u0000");
}

function connectedWorkflowGraphNodeIds(config: WorkflowConfig): string[][] {
  const orderedNodeIds = workflowNodeIds(config);
  const nodeIdSet = new Set(orderedNodeIds);
  const order = new Map(orderedNodeIds.map((nodeId, index) => [nodeId, index]));
  const neighbors = new Map<string, Set<string>>();
  orderedNodeIds.forEach((nodeId) => neighbors.set(nodeId, new Set()));
  for (const edge of config.edges ?? []) {
    if (!nodeIdSet.has(edge.source_id) || !nodeIdSet.has(edge.target_id)) continue;
    neighbors.get(edge.source_id)?.add(edge.target_id);
    neighbors.get(edge.target_id)?.add(edge.source_id);
  }
  const visited = new Set<string>();
  const components: string[][] = [];
  for (const nodeId of orderedNodeIds) {
    if (visited.has(nodeId)) continue;
    const stack = [nodeId];
    const component: string[] = [];
    while (stack.length > 0) {
      const current = stack.pop()!;
      if (visited.has(current)) continue;
      visited.add(current);
      component.push(current);
      const nextNodeIds = [...(neighbors.get(current) ?? [])]
        .filter((candidate) => !visited.has(candidate))
        .sort((left, right) => (order.get(right) ?? 0) - (order.get(left) ?? 0));
      stack.push(...nextNodeIds);
    }
    components.push(component.sort((left, right) => (order.get(left) ?? 0) - (order.get(right) ?? 0)));
  }
  return components;
}

function slugifyGraphId(value: string): string {
  return value.replace(/[^a-zA-Z0-9_-]+/g, "-").replace(/^-+|-+$/g, "").toLowerCase() || "graph";
}

function defaultWorkflowGraphName(config: WorkflowConfig, component: string[], index: number): string {
  const trigger = component
    .map((nodeId) => nodeById(config, nodeId))
    .find((node): node is WorkflowNodeConfig => Boolean(node && node.type.startsWith("trigger:")));
  const fallbackNode = nodeById(config, component[0] ?? "");
  return trigger?.display_name?.trim() || fallbackNode?.display_name?.trim() || `Graph ${index + 1}`;
}

function defaultWorkflowGraphId(config: WorkflowConfig, component: string[], index: number): string {
  const trigger = component
    .map((nodeId) => nodeById(config, nodeId))
    .find((node): node is WorkflowNodeConfig => Boolean(node && node.type.startsWith("trigger:")));
  return `graph-${slugifyGraphId(trigger?.id ?? component[0] ?? String(index + 1))}`;
}

function normalizeWorkflowGraphs(
  config: WorkflowConfig,
  graphs: ReadonlyArray<WorkflowGraphMetadata> = [],
  defaultDirectoryPath = "root",
  defaultUserId = "",
): WorkflowGraphMetadata[] {
  const components = connectedWorkflowGraphNodeIds(config);
  const validNodeIds = new Set(workflowNodeIds(config));
  const sanitizedGraphs = graphs
    .map((graph) => ({
      id: graph.id.trim(),
      name: graph.name.trim(),
      description: graph.description?.trim() || null,
      directory_paths: Array.from(
        new Set(
          (Array.isArray(graph.directory_paths) ? graph.directory_paths : [])
            .map((directoryPath) => directoryPath.trim())
            .filter(Boolean),
        ),
      ),
      user_ids: Array.from(
        new Set(
          (Array.isArray(graph.user_ids) ? graph.user_ids : [])
            .map((userId) => userId.trim())
            .filter(Boolean),
        ),
      ),
      node_ids: Array.from(new Set((graph.node_ids ?? []).map((nodeId) => nodeId.trim()).filter((nodeId) => validNodeIds.has(nodeId)))),
    }))
    .filter((graph) => graph.id && graph.name && graph.node_ids.length > 0);
  const exactGraphs = new Map(sanitizedGraphs.map((graph) => [workflowGraphKey(graph.node_ids), graph]));
  const usedGraphIds = new Set<string>();
  const usedResultIds = new Set<string>();
  return components.map((component, index) => {
    const componentKey = workflowGraphKey(component);
    let graph = exactGraphs.get(componentKey);
    if (!graph) {
      graph = sanitizedGraphs
        .filter((candidate) => !usedGraphIds.has(candidate.id))
        .map((candidate) => ({
          graph: candidate,
          overlap: candidate.node_ids.filter((nodeId) => component.includes(nodeId)).length,
        }))
        .filter((candidate) => candidate.overlap > 0)
        .sort((left, right) => right.overlap - left.overlap)[0]?.graph;
    }
    const baseId = graph?.id ?? defaultWorkflowGraphId(config, component, index);
    let id = baseId;
    let suffix = 2;
    while (usedResultIds.has(id)) {
      id = `${baseId}-${suffix}`;
      suffix += 1;
    }
    usedResultIds.add(id);
    if (graph) usedGraphIds.add(graph.id);
    return {
      id,
      name: graph?.name ?? defaultWorkflowGraphName(config, component, index),
      description: graph?.description ?? null,
      directory_paths: graph?.directory_paths.length ? graph.directory_paths : [defaultDirectoryPath],
      user_ids: graph?.user_ids ?? (defaultUserId.trim() ? [defaultUserId.trim()] : []),
      node_ids: component,
    };
  });
}

function graphVisibleInDirectory(graph: WorkflowGraphMetadata, selectedDirectoryPath: string): boolean {
  const selectedPath = selectedDirectoryPath.trim() || "root";
  const directoryPaths = graph.directory_paths.length > 0 ? graph.directory_paths : ["root"];
  return directoryPaths.includes(selectedPath);
}

function graphVisibleForUser(graph: WorkflowGraphMetadata, currentUserId: string): boolean {
  const userId = currentUserId.trim();
  if (!userId) return false;
  return graph.user_ids.includes(userId);
}

function graphVisibleInView(
  graph: WorkflowGraphMetadata,
  view: WorkflowGraphView,
  selectedDirectoryPath: string,
  currentUserId: string,
): boolean {
  return view === "private"
    ? graphVisibleForUser(graph, currentUserId)
    : graphVisibleInDirectory(graph, selectedDirectoryPath);
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

function nodeStatusBuckets(
  activity: WorkflowNodeActivityCounts | undefined,
): Array<{ status: string; count: number }> {
  if (!activity) return [];
  const orderedStatuses = new Set<string>(NODE_STATUS_ORDER);
  const buckets: Array<{ status: string; count: number }> = [];
  for (const status of NODE_STATUS_ORDER) {
    const count = activity[status] ?? 0;
    if (count > 0) {
      buckets.push({ status, count });
    }
  }
  for (const [status, count] of Object.entries(activity)) {
    if (!orderedStatuses.has(status) && count > 0) {
      buckets.push({ status, count });
    }
  }
  return buckets;
}

function dominantNodeStatus(activity: WorkflowNodeActivityCounts | undefined): string | null {
  return nodeStatusBuckets(activity)[0]?.status ?? null;
}

function nodeStatusBadges(activity: WorkflowNodeActivityCounts | undefined): GraphNodeStatusBadge[] {
  const buckets = nodeStatusBuckets(activity);
  const compact = buckets.length > 1;
  return buckets.map(({ status, count }) => {
    const title = `${count} ${status}`;
    return {
      status,
      count,
      title,
      compact,
      label: compact ? `${count}` : title,
    };
  });
}

function buildNodes(
  config: WorkflowConfig,
  layout: WorkflowLayout,
  catalog: WorkflowEditorCatalog,
  nodeActivity: Record<string, WorkflowNodeActivityCounts> = {},
  diagnostics: WorkflowValidationDiagnostic[] = [],
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
    const diagnosticCount = diagnostics.filter((diagnostic) => diagnostic.node_id === node.id).length;
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
        description: catalogEntry
          ? nodeTypeDescription(catalogEntry)
          : NODE_TYPE_DESCRIPTIONS[node.type] || `${node.type} node.`,
        inputPorts: catalogEntry?.input_ports ?? [],
        outputPorts: catalogEntry?.output_ports ?? [],
        dominantStatus: dominantNodeStatus(activity),
        statusBadges: nodeStatusBadges(activity),
        diagnosticCount,
        hasDiagnostics: diagnosticCount > 0,
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

function buildEdges(config: WorkflowConfig, catalog: WorkflowEditorCatalog): Edge[] {
  return config.edges.map((edge) => {
    const sourceNode = nodeById(config, edge.source_id);
    const sourceCatalogEntry = sourceNode ? getCatalogEntry(catalog, sourceNode.type) : undefined;
    const routeClass = workflowRouteClass(edge.source_port);
    return {
      id: edgeId(edge),
      source: edge.source_id,
      sourceHandle: edge.source_port,
      target: edge.target_id,
      label: workflowEdgeRouteLabel(edge.source_port, sourceCatalogEntry?.output_ports ?? []),
      animated: Boolean(edge.allow_cycle),
      className: `workflow-edge workflow-edge-route-${routeClass}`,
    };
  });
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
  if (kind === "trigger" && type === "trigger:event") {
    return { id, display_name: "Internal event", type, event_type: "custom.operator_event" };
  }
  if (kind === "trigger" && type === "trigger:ticket:created") {
    return { id, display_name: "Ticket created", type, directories: ["root"] };
  }
  if (kind === "trigger" && type === "trigger:ticket:changed") {
    return { id, display_name: "Ticket changed", type, directories: ["root"] };
  }
  if (kind === "trigger" && (type === "trigger:issue:comment-created" || type === "trigger:issue:new-comment")) {
    return {
      id,
      display_name: "Issue comment",
      type,
      provider: "ado",
      directories: ["root"],
    };
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
  if (kind === "gate" && type === "gate:deserialize") {
    return {
      id,
      display_name: "Deserialize",
      type,
      content: "${{node.payload_text}}",
      format: "auto",
      outputs: {},
    };
  }
  if (kind === "gate" && type === "gate:serialize") {
    return {
      id,
      display_name: "Serialize",
      type,
      value: {},
      format: "json",
      outputs: { content: { type: "string" }, format: { type: "string" } },
    };
  }
  if (kind === "gate" && type === "gate:merge") {
    return { id, display_name: "New merge", type, outputs: {} };
  }
  if (kind === "gate" && type === "gate:debounce") {
    return {
      id,
      display_name: "Debounce",
      type,
      key: "debounce:${{system.execution.event_id}}",
      quiet_period: "PT2M",
      max_delay: "PT10M",
      outputs: {},
    };
  }
  if (kind === "gate" && type === "gate:dedupe") {
    return {
      id,
      display_name: "Dedupe",
      type,
      key: "dedupe:${{system.execution.event_id}}",
      ttl: "P7D",
      outputs: {},
    };
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
  if (kind === "effect" && type === "effect:agent:destroy") {
    return { id, display_name: "Agent destroy", type, session_id: "session-id" };
  }
  if (kind === "effect" && type === "effect:semantic:ensure-collection") {
    return { id, display_name: "Ensure semantic collection", type, collection_name: "agent_memory" };
  }
  if (kind === "effect" && type === "effect:semantic:upsert") {
    return {
      id,
      display_name: "Semantic upsert",
      type,
      collection_name: "agent_memory",
      point_id: "memory-1",
      text: "Important context to remember.",
      payload: {},
    };
  }
  if (kind === "effect" && type === "effect:semantic:delete") {
    return {
      id,
      display_name: "Semantic delete",
      type,
      collection_name: "agent_memory",
      point_ids: ["memory-1"],
    };
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
  if (kind === "effect" && type === "effect:event:emit") {
    return {
      id,
      display_name: "Emit internal event",
      type,
      event_type: "custom.operator_event",
      payload: {},
    };
  }
  if (kind === "effect" && type === "effect:issue:update-metadata") {
    return {
      id,
      display_name: "Update issue metadata",
      type,
      issue_id: "issue-1",
      patch: { "fields.workflow_status": "updated" },
      sync_proxy_cache: true,
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
  if (kind === "query" && type === "query:agent:artifact-text") {
    return {
      id,
      display_name: "Agent artifact text",
      type,
      path: "report/summary.txt",
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
  if (kind === "query" && type === "query:ado:work-items") {
    return {
      id,
      display_name: "ADO work items",
      type,
      top: 50,
      skip: 0,
    };
  }
  if (kind === "query" && type === "query:semantic:store-status") {
    return { id, display_name: "Semantic store status", type, collection_name: "agent_memory" };
  }
  if (kind === "query" && type === "query:semantic:search") {
    return {
      id,
      display_name: "Semantic search",
      type,
      collection_name: "agent_memory",
      query_text: "What context is relevant?",
      limit: 10,
    };
  }
  if (kind === "query" && type === "query:semantic:get") {
    return {
      id,
      display_name: "Semantic get",
      type,
      collection_name: "agent_memory",
      point_ids: ["memory-1"],
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

function workflowGraphLabels(
  graphs: ReadonlyArray<WorkflowGraphMetadata>,
  nodes: ReadonlyArray<Node<GraphNodeData>>,
  viewport: Viewport,
): WorkflowGraphLabel[] {
  const nodesById = new Map(nodes.map((node) => [node.id, node]));
  return graphs.flatMap((graph) => {
    const graphNodes = graph.node_ids
      .map((nodeId) => nodesById.get(nodeId))
      .filter((node): node is Node<GraphNodeData> => Boolean(node));
    if (graphNodes.length === 0) return [];
    const bounds = graphNodes.map((node) => nodeBounds(node));
    const left = Math.min(...bounds.map((bound) => bound.left));
    const top = Math.min(...bounds.map((bound) => bound.top));
    const bottom = Math.max(...bounds.map((bound) => bound.bottom));
    return [
      {
        graph,
        left: left * viewport.zoom + viewport.x - 24,
        top: ((top + bottom) / 2) * viewport.zoom + viewport.y,
      },
    ];
  });
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

function nodeTypeDescription(entry: WorkflowEditorNodeCatalogEntry): string {
  const description = entry.description?.trim();
  return description || NODE_TYPE_DESCRIPTIONS[entry.type] || NODE_KIND_DESCRIPTIONS[entry.kind];
}

function directUpstreamNodeIds(config: WorkflowConfig, targetId: string): string[] {
  return config.edges.filter((edge) => edge.target_id === targetId).map((edge) => edge.source_id);
}

function copyOutputSchema(schema: Record<string, unknown> | undefined): Record<string, unknown> {
  return { ...(schema ?? {}) };
}

function deriveWorkflowOutputSchemas(
  config: WorkflowConfig,
  baseOutputSchemas: WorkflowOutputSchemas = {},
): WorkflowOutputSchemas {
  const nodesById = new Map(workflowNodes(config).map((node) => [node.id, node]));
  const cache = new Map<string, Record<string, unknown>>();
  const visiting = new Set<string>();

  function resolve(nodeId: string): Record<string, unknown> {
    const cached = cache.get(nodeId);
    if (cached) return cached;
    const node = nodesById.get(nodeId);
    if (!node) return {};
    if (visiting.has(nodeId)) {
      const fallback = isRecord(baseOutputSchemas[nodeId]) ? baseOutputSchemas[nodeId] : undefined;
      return copyOutputSchema(fallback);
    }

    visiting.add(nodeId);
    let schema: Record<string, unknown>;
    if (node.type === "gate:filter" || node.type === "gate:dedupe") {
      schema = copyOutputSchema(resolve(directUpstreamNodeIds(config, node.id)[0] ?? ""));
    } else if (node.type === "gate:merge") {
      schema = mergeDeclaredOutputIntersection(
        directUpstreamNodeIds(config, node.id).map((upstreamNodeId) => resolve(upstreamNodeId)),
      );
    } else if (node.type === "gate:map" && isRecord(node.template)) {
      schema = mapTemplateOutputSchema(node.template);
    } else {
      const backendSchema = isRecord(baseOutputSchemas[node.id]) ? baseOutputSchemas[node.id] : undefined;
      schema = copyOutputSchema(backendSchema ?? (isRecord(node.outputs) ? node.outputs : undefined));
    }
    visiting.delete(nodeId);
    cache.set(nodeId, schema);
    return schema;
  }

  const outputSchemas: WorkflowOutputSchemas = {};
  for (const node of workflowNodes(config)) {
    outputSchemas[node.id] = copyOutputSchema(resolve(node.id));
  }
  return outputSchemas;
}

function declaredOutputsForNode(
  node: WorkflowNodeConfig | undefined,
  outputSchemas: WorkflowOutputSchemas = {},
): Record<string, unknown> {
  if (!node) return {};
  const schema = isRecord(outputSchemas[node.id]) ? outputSchemas[node.id] : undefined;
  if (schema) return copyOutputSchema(schema);
  return copyOutputSchema(isRecord(node.outputs) ? node.outputs : undefined);
}

function mapTemplateOutputSchema(template: Record<string, unknown>): Record<string, unknown> {
  const schema: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(template)) {
    const outputName = key.trim();
    if (!outputName) continue;
    schema[outputName] = inferSchemaFromTemplateValue(value);
  }
  return schema;
}

function inferSchemaFromTemplateValue(value: unknown): Record<string, unknown> {
  if (typeof value === "boolean") return { type: "boolean" };
  if (typeof value === "number") return { type: Number.isInteger(value) ? "integer" : "number" };
  if (value === null) return { type: "null" };
  if (typeof value === "string") return { type: "string" };
  if (Array.isArray(value)) return { type: "array" };
  if (isRecord(value)) return { type: "object" };
  return { type: "object" };
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

function normalizeDerivedNodeFields(
  config: WorkflowConfig,
  baseOutputSchemas: WorkflowOutputSchemas = {},
): WorkflowConfig {
  const nextConfig = cloneConfig(config);
  const outputSchemas = deriveWorkflowOutputSchemas(nextConfig, baseOutputSchemas);
  for (const gate of nextConfig.gates ?? []) {
    if (gate.type === "gate:filter") {
      gate.outputs = declaredOutputsForNode(gate, outputSchemas);
    } else if (gate.type === "gate:map") {
      gate.outputs = declaredOutputsForNode(gate, outputSchemas);
    } else if (gate.type === "gate:deserialize") {
      gate.outputs = declaredOutputsForNode(gate, outputSchemas);
    } else if (gate.type === "gate:serialize") {
      gate.outputs = declaredOutputsForNode(gate, outputSchemas);
    } else if (gate.type === "gate:dedupe") {
      gate.outputs = declaredOutputsForNode(gate, outputSchemas);
    } else if (gate.type === "gate:await-agent") {
      delete gate.completion_trigger_id;
      gate.outputs = declaredOutputsForNode(gate, outputSchemas);
    } else if (gate.type === "gate:for") {
      gate.outputs = declaredOutputsForNode(gate, outputSchemas);
    } else if (gate.type === "gate:merge") {
      gate.outputs = declaredOutputsForNode(gate, outputSchemas);
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

function upstreamBindingOptions(
  config: WorkflowConfig,
  nodeId: string,
  outputSchemas: WorkflowOutputSchemas = deriveWorkflowOutputSchemas(config),
): BindingOption[] {
  const options: BindingOption[] = [...SYSTEM_BINDING_OPTIONS];
  const seen = new Set<string>();
  for (const option of SYSTEM_BINDING_OPTIONS) {
    seen.add(option.label.replace(/^\[System\] /, ""));
    seen.add(option.value);
  }
  for (const upstreamNodeId of collectAncestorNodeIds(config, nodeId)) {
    const outputs = declaredOutputsForNode(nodeById(config, upstreamNodeId), outputSchemas);
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

function validateWorkflowBindingDiagnostics(
  config: WorkflowConfig,
  outputSchemas: WorkflowOutputSchemas = deriveWorkflowOutputSchemas(config),
): WorkflowValidationDiagnostic[] {
  const diagnostics: WorkflowValidationDiagnostic[] = [];
  const nodes = workflowNodes(config);
  const nodesById = new Map(nodes.map((node) => [node.id, node]));
  const allNodeIds = new Set(nodes.map((node) => node.id));

  for (const node of nodes) {
    const declaredOutputs = new Map(
      collectAncestorNodeIds(config, node.id)
        .map((upstreamNodeId) => [upstreamNodeId, declaredOutputsForNode(nodesById.get(upstreamNodeId), outputSchemas)] as const),
    );
    for (const [fieldPath, value] of walkEditableNodeConfigValues(node)) {
      diagnostics.push(
        ...validateBindingValue({
          value,
          fieldPath,
          node,
          declaredOutputs,
          allNodeIds,
        }),
      );
    }
  }
  return diagnostics;
}

function walkEditableNodeConfigValues(node: WorkflowNodeConfig): Array<[string, unknown]> {
  const values: Array<[string, unknown]> = [];
  for (const [key, value] of Object.entries(node)) {
    if (key === "id" || key === "display_name" || key === "type") continue;
    walkConfigValue(value, key, values);
  }
  return values;
}

function walkConfigValue(value: unknown, path: string, values: Array<[string, unknown]>) {
  if (isRecord(value)) {
    if (Object.keys(value).length === 0) {
      values.push([path, value]);
      return;
    }
    for (const [key, childValue] of Object.entries(value)) {
      walkConfigValue(childValue, `${path}.${key}`, values);
    }
    return;
  }
  if (Array.isArray(value)) {
    if (value.length === 0) {
      values.push([path, value]);
      return;
    }
    value.forEach((childValue, index) => {
      walkConfigValue(childValue, `${path}[${index}]`, values);
    });
    return;
  }
  values.push([path, value]);
}

function validateBindingValue({
  value,
  fieldPath,
  node,
  declaredOutputs,
  allNodeIds,
}: {
  value: unknown;
  fieldPath: string;
  node: WorkflowNodeConfig;
  declaredOutputs: Map<string, Record<string, unknown>>;
  allNodeIds: Set<string>;
}): WorkflowValidationDiagnostic[] {
  if (typeof value !== "string" || !value.includes("${{")) return [];

  const diagnostics: WorkflowValidationDiagnostic[] = [];
  let index = 0;
  while (true) {
    const start = value.indexOf("${{", index);
    if (start === -1) return diagnostics;
    const end = value.indexOf("}}", start + 3);
    if (end === -1) {
      diagnostics.push({
        code: "malformed_binding",
        message: `${fieldPath} contains malformed output reference`,
        node_id: node.id,
        node_type: node.type,
        field_path: fieldPath,
        reference: value.slice(start),
      });
      return diagnostics;
    }

    const sourceText = value.slice(start, end + 2);
    const expressionText = value.slice(start + 3, end);
    try {
      const expression = parseBindingExpression(expressionText, sourceText);
      diagnostics.push(
        ...validateBindingExpression({
          expression,
          fieldPath,
          node,
          declaredOutputs,
          allNodeIds,
        }),
      );
    } catch (error) {
      const parseError = error instanceof BindingParseError
        ? error
        : new BindingParseError("malformed_binding", "Malformed output reference");
      diagnostics.push({
        code: parseError.code,
        message: parseError.code === "malformed_binding_fallback"
          ? `${fieldPath} has malformed fallback syntax`
          : `${fieldPath} contains malformed output reference`,
        node_id: node.id,
        node_type: node.type,
        field_path: fieldPath,
        reference: sourceText,
      });
    }
    index = end + 2;
  }
}

function validateBindingExpression({
  expression,
  fieldPath,
  node,
  declaredOutputs,
  allNodeIds,
}: {
  expression: ParsedBindingExpression;
  fieldPath: string;
  node: WorkflowNodeConfig;
  declaredOutputs: Map<string, Record<string, unknown>>;
  allNodeIds: Set<string>;
}): WorkflowValidationDiagnostic[] {
  const diagnostics = validateBindingReference({
    reference: expression.primary,
    fieldPath,
    node,
    declaredOutputs,
    allNodeIds,
  });
  if (!expression.hasFallback) return diagnostics;
  const fallback = expression.fallback;
  if (isParsedBindingExpression(fallback)) {
    diagnostics.push(
      ...validateBindingExpression({
        expression: fallback,
        fieldPath,
        node,
        declaredOutputs,
        allNodeIds,
      }),
    );
  } else if (isParsedBindingReference(fallback)) {
    diagnostics.push(
      ...validateBindingReference({
        reference: fallback,
        fieldPath,
        node,
        declaredOutputs,
        allNodeIds,
      }),
    );
  }
  return diagnostics;
}

function validateBindingReference({
  reference,
  fieldPath,
  node,
  declaredOutputs,
  allNodeIds,
}: {
  reference: ParsedBindingReference;
  fieldPath: string;
  node: WorkflowNodeConfig;
  declaredOutputs: Map<string, Record<string, unknown>>;
  allNodeIds: Set<string>;
}): WorkflowValidationDiagnostic[] {
  const resolved = resolveAvailableBindingReference(reference, declaredOutputs, allNodeIds);
  const outputRef = `${resolved.nodeId}.${resolved.outputPath}`;
  if (resolved.nodeId === "system") return [];
  if (!declaredOutputs.has(resolved.nodeId)) {
    const relation = allNodeIds.has(resolved.nodeId) ? "non-upstream" : "deleted";
    return [{
      code: "invalid_binding_node",
      message: `${fieldPath} references ${relation} node ${resolved.nodeId}`,
      node_id: node.id,
      node_type: node.type,
      field_path: fieldPath,
      reference: outputRef,
    }];
  }

  const outputName = resolved.outputPath.split(".", 1)[0] ?? "";
  if (!(outputName in (declaredOutputs.get(resolved.nodeId) ?? {}))) {
    return [{
      code: "missing_binding_output",
      message: `${fieldPath} references missing output ${outputRef}`,
      node_id: node.id,
      node_type: node.type,
      field_path: fieldPath,
      reference: outputRef,
    }];
  }
  return [];
}

function resolveAvailableBindingReference(
  reference: ParsedBindingReference,
  declaredOutputs: Map<string, Record<string, unknown>>,
  allNodeIds: Set<string>,
): ParsedBindingReference {
  const segments = `${reference.nodeId}.${reference.outputPath}`.split(".").filter(Boolean);
  for (let index = segments.length - 1; index > 0; index -= 1) {
    const candidateNodeId = segments.slice(0, index).join(".");
    const candidateOutputPath = segments.slice(index).join(".");
    if (candidateNodeId === "system" || declaredOutputs.has(candidateNodeId) || allNodeIds.has(candidateNodeId)) {
      return { nodeId: candidateNodeId, outputPath: candidateOutputPath };
    }
  }
  return reference;
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

function compareNodeCatalogEntries(
  left: WorkflowEditorNodeCatalogEntry,
  right: WorkflowEditorNodeCatalogEntry,
): number {
  return (
    left.label.localeCompare(right.label, undefined, { sensitivity: "base", numeric: true }) ||
    left.type.localeCompare(right.type, undefined, { sensitivity: "base", numeric: true })
  );
}

function isWorkflowNodeKind(value: unknown): value is WorkflowNodeKind {
  return value === "trigger" || value === "gate" || value === "effect" || value === "query";
}

function parseNodeTypeDragPayload(dataTransfer: DataTransfer): NodeTypeDragPayload | null {
  const raw = dataTransfer.getData(NODE_TYPE_DRAG_DATA_MIME);
  if (!raw) return null;
  try {
    const payload = JSON.parse(raw) as Partial<NodeTypeDragPayload>;
    if (!isWorkflowNodeKind(payload.kind) || typeof payload.type !== "string" || !payload.type.trim()) {
      return null;
    }
    return { kind: payload.kind, type: payload.type };
  } catch {
    return null;
  }
}

export default function App() {
  const { privilegedUnlocked, selectedDirectoryPath, currentUserId, currentUserLabel } = useAdminUi();
  const appShellRef = useRef<HTMLDivElement | null>(null);
  const canvasShellRef = useRef<HTMLElement | null>(null);
  const hasAutoFitRef = useRef(false);
  const initialGraphDirectoryPathRef = useRef(selectedDirectoryPath);
  const copiedSelectionRef = useRef<CopiedWorkflowSelection | null>(null);
  const sidebarResizeRef = useRef<SidebarResizeState | null>(null);
  const [document, setDocument] = useState<WorkflowEditorDocument | null>(null);
  const [catalog, setCatalog] = useState<WorkflowEditorCatalog>(emptyCatalog());
  const [runSnapshot, setRunSnapshot] = useState<WorkflowRunSnapshot | null>(null);
  const [validationDiagnostics, setValidationDiagnostics] = useState<WorkflowValidationDiagnostic[]>([]);
  const [flowNodes, setFlowNodes] = useState<Node<GraphNodeData>[]>([]);
  const [flowEdges, setFlowEdges] = useState<Edge[]>([]);
  const [flowInstance, setFlowInstance] = useState<ReactFlowInstance<Node<GraphNodeData>, Edge> | null>(null);
  const [canvasViewport, setCanvasViewport] = useState<Viewport>({ x: 0, y: 0, zoom: 1 });
  const [selectedNodeIds, setSelectedNodeIds] = useState<string[]>([]);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const [selectedGraphId, setSelectedGraphId] = useState<string | null>(null);
  const [nodeConnectionMenu, setNodeConnectionMenu] = useState<NodeConnectionMenuState | null>(null);
  const [pendingConnection, setPendingConnection] = useState<PendingConnectionState | null>(null);
  const [workflowGraphView, setWorkflowGraphView] = useState<WorkflowGraphView>("directory");
  const [graphNameDraft, setGraphNameDraft] = useState("");
  const [graphDescriptionDraft, setGraphDescriptionDraft] = useState("");
  const [graphDirectoriesDraft, setGraphDirectoriesDraft] = useState("");
  const [graphUsersDraft, setGraphUsersDraft] = useState("");
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
  const config = document?.config ?? emptyConfig();
  const workflowGraphs = document?.graphs ?? [];
  const visibleWorkflowGraphs = workflowGraphs.filter((graph) =>
    graphVisibleInView(graph, workflowGraphView, selectedDirectoryPath, currentUserId),
  );
  const visibleGraphNodeIds = new Set(visibleWorkflowGraphs.flatMap((graph) => graph.node_ids));
  const visibleGraphNodeIdKey = workflowGraphKey([...visibleGraphNodeIds]);
  const visibleRuns = (runSnapshot?.runs ?? []).filter((run) =>
    run.active_nodes.some((activity) => visibleGraphNodeIds.has(activity.node_id)),
  );
  const baseDisplayedFlowNodes = flowNodes.filter((node) => visibleGraphNodeIds.has(node.id));
  const displayedFlowNodes = pendingConnection && document
    ? baseDisplayedFlowNodes.map((node) => {
        let pendingConnectionRole: PendingConnectionRole;
        if (node.id === pendingConnection.sourceId) {
          pendingConnectionRole = "source";
        } else {
          pendingConnectionRole = validateConnection(
            document.config,
            catalog,
            pendingConnection.sourceId,
            node.id,
            pendingConnection.sourcePort,
          )
            ? "invalid-target"
            : "valid-target";
        }
        return {
          ...node,
          data: {
            ...node.data,
            pendingConnectionRole,
          },
        };
      })
    : baseDisplayedFlowNodes;
  const displayedFlowEdges = flowEdges.filter(
    (edge) => visibleGraphNodeIds.has(edge.source) && visibleGraphNodeIds.has(edge.target),
  );
  const selectedNode = selectedNodeIds.length === 1 && selectedNodeId ? nodeById(config, selectedNodeId) : undefined;
  const selectedCatalogEntry = selectedNode ? getCatalogEntry(catalog, selectedNode.type) : undefined;
  const selectedNodeDiagnostics = selectedNode
    ? validationDiagnostics.filter((diagnostic) => diagnostic.node_id === selectedNode.id)
    : [];
  const selectedEdge = selectedEdgeId
    ? config.edges.find(
        (edge) =>
          edgeId(edge) === selectedEdgeId &&
          visibleGraphNodeIds.has(edge.source_id) &&
          visibleGraphNodeIds.has(edge.target_id),
      )
    : undefined;
  const selectedEdgeSourceNode = selectedEdge ? nodeById(config, selectedEdge.source_id) : undefined;
  const selectedEdgeSourceCatalogEntry = selectedEdgeSourceNode
    ? getCatalogEntry(catalog, selectedEdgeSourceNode.type)
    : undefined;
  const selectedEdgeRouteLabel = selectedEdge
    ? workflowEdgeRouteLabel(selectedEdge.source_port, selectedEdgeSourceCatalogEntry?.output_ports ?? [])
      ?? (selectedEdge.source_port ? selectedEdge.source_port : "Default")
    : null;
  const selectedGraph = selectedGraphId
    ? visibleWorkflowGraphs.find((graph) => graph.id === selectedGraphId)
    : undefined;
  const graphLabels = workflowGraphLabels(visibleWorkflowGraphs, displayedFlowNodes, canvasViewport);
  const graphViewContext =
    workflowGraphView === "private"
      ? `User: ${currentUserLabel}`
      : `Directory: ${selectedDirectoryPath}`;
  const selectedNodeBindingOptions = selectedNode
    ? upstreamBindingOptions(config, selectedNode.id, document?.output_schemas ?? {})
    : [];
  const hasCollapsedTargetUi =
    selectedNode?.type === "effect:vm:copy-files" ||
    selectedNode?.type === "effect:agent:prompt" ||
    selectedNode?.type === "effect:agent:exec" ||
    selectedNode?.type === "effect:agent:destroy" ||
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
    let cancelled = false;
    Promise.all([
      fetchJson<WorkflowEditorDocument>("/api/workflow-editor/document"),
      fetchJson<WorkflowEditorCatalog>("/api/workflow-editor/catalog"),
      fetchJson<WorkflowRunSnapshot>("/api/workflow-editor/runs"),
    ])
      .then(([payload, catalogPayload, runSnapshotPayload]) => {
        if (cancelled) return;
        const backendOutputSchemas = payload.output_schemas ?? {};
        const normalizedConfig = normalizeDerivedNodeFields(
          normalizeWorkflowConfigShape(payload.config),
          backendOutputSchemas,
        );
        const outputSchemas = deriveWorkflowOutputSchemas(normalizedConfig, backendOutputSchemas);
        const normalizedGraphs = normalizeWorkflowGraphs(
          normalizedConfig,
          payload.graphs ?? [],
          initialGraphDirectoryPathRef.current,
        );
        const normalizedDocument = {
          ...payload,
          config: normalizedConfig,
          graphs: normalizedGraphs,
          output_schemas: outputSchemas,
        };
        const nextDiagnostics = payload.validation_diagnostics
          ?? validateWorkflowBindingDiagnostics(normalizedConfig, outputSchemas);
        setCatalog(catalogPayload);
        setRunSnapshot(runSnapshotPayload);
        setValidationDiagnostics(nextDiagnostics);
        setDocument(normalizedDocument);
        setFlowNodes(buildNodes(normalizedConfig, payload.layout, catalogPayload, runSnapshotPayload.node_activity, nextDiagnostics));
        setFlowEdges(buildEdges(normalizedConfig, catalogPayload));
      })
      .catch((reason: unknown) => {
        if (cancelled) return;
        setError(reason instanceof Error ? reason.message : String(reason));
      });
    return () => {
      cancelled = true;
    };
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
    setFlowNodes((currentNodes) => {
      return buildNodes(
        document.config,
        document.layout,
        catalog,
        runSnapshot?.node_activity ?? {},
        validationDiagnostics,
        currentPositionsFromNodes(currentNodes),
        currentNodes.filter((node) => node.selected).map((node) => node.id),
      );
    });
    setFlowEdges(buildEdges(document.config, catalog));
  }, [catalog, document, runSnapshot, validationDiagnostics]);

  useEffect(() => {
    hasAutoFitRef.current = false;
  }, [selectedDirectoryPath, workflowGraphView, currentUserId]);

  useEffect(() => {
    if (!flowInstance || displayedFlowNodes.length === 0 || hasAutoFitRef.current) return;
    queueMicrotask(() => {
      void flowInstance.fitView({ padding: 0.2 }).finally(() => {
        setCanvasViewport(flowInstance.getViewport());
      });
      hasAutoFitRef.current = true;
    });
  }, [flowInstance, displayedFlowNodes, displayedFlowEdges]);

  useEffect(() => {
    if (!selectedGraph) {
      setGraphNameDraft("");
      setGraphDescriptionDraft("");
      setGraphDirectoriesDraft("");
      setGraphUsersDraft("");
      return;
    }
    setGraphNameDraft(selectedGraph.name);
    setGraphDescriptionDraft(selectedGraph.description ?? "");
    setGraphDirectoriesDraft((selectedGraph.directory_paths.length ? selectedGraph.directory_paths : [selectedDirectoryPath]).join("\n"));
    setGraphUsersDraft(selectedGraph.user_ids.join("\n"));
  }, [selectedGraph, selectedDirectoryPath]);

  useEffect(() => {
    if (!selectedGraphId || visibleWorkflowGraphs.some((graph) => graph.id === selectedGraphId)) return;
    setSelectedGraphId(null);
  }, [selectedGraphId, visibleWorkflowGraphs]);

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
    const nextSelectedNodeIds = flowNodes
      .filter((node) => node.selected && visibleGraphNodeIds.has(node.id))
      .map((node) => node.id);
    setSelectedNodeIds(nextSelectedNodeIds);
    setSelectedNodeId((currentSelectedNodeId) => {
      if (nextSelectedNodeIds.length === 0) return null;
      if (currentSelectedNodeId && nextSelectedNodeIds.includes(currentSelectedNodeId)) return currentSelectedNodeId;
      return nextSelectedNodeIds[nextSelectedNodeIds.length - 1] ?? null;
    });
  }, [flowNodes, visibleGraphNodeIdKey]);

  function patchDocument(
    nextConfig: WorkflowConfig,
    nextLayout: WorkflowLayout,
    nextGraphs?: WorkflowGraphMetadata[],
    selectedNodeIdsAfterPatch?: string[],
    diagnosticsOverride?: WorkflowValidationDiagnostic[],
    outputSchemasOverride?: WorkflowOutputSchemas,
  ) {
    if (!document) return;
    const backendOutputSchemas = outputSchemasOverride ?? document.output_schemas ?? {};
    const normalizedConfig = normalizeDerivedNodeFields(
      normalizeWorkflowConfigShape(nextConfig),
      backendOutputSchemas,
    );
    const outputSchemas = deriveWorkflowOutputSchemas(normalizedConfig, backendOutputSchemas);
    const normalizedGraphs = normalizeWorkflowGraphs(
      normalizedConfig,
      nextGraphs ?? document.graphs ?? [],
      selectedDirectoryPath,
      workflowGraphView === "private" ? currentUserId : "",
    );
    const nextDocument = {
      ...document,
      config: normalizedConfig,
      layout: nextLayout,
      graphs: normalizedGraphs,
      output_schemas: outputSchemas,
    };
    const nextDiagnostics = diagnosticsOverride ?? validateWorkflowBindingDiagnostics(normalizedConfig, outputSchemas);
    setDocument(nextDocument);
    setValidationDiagnostics(nextDiagnostics);
    setFlowNodes((currentNodes) =>
      buildNodes(
        normalizedConfig,
        nextLayout,
        catalog,
        runSnapshot?.node_activity ?? {},
        nextDiagnostics,
        currentPositionsFromNodes(currentNodes),
        selectedNodeIdsAfterPatch ?? currentNodes.filter((node) => node.selected).map((node) => node.id),
      ),
    );
    setFlowEdges(buildEdges(normalizedConfig, catalog));
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
      const visibleCurrentNodes = currentNodes.filter((node) => visibleGraphNodeIds.has(node.id));
      const { snappedNodes, guides } = snapDraggedNodes(activeNode, typedDraggedNodes, visibleCurrentNodes);
      setSnapGuides(guides);
      const snappedById = new Map(snappedNodes.map((node) => [node.id, node]));
      return currentNodes.map((node) => snappedById.get(node.id) ?? node);
    });
  }

  function updateEdgeChanges(changes: Parameters<typeof applyEdgeChanges>[0]) {
    setFlowEdges((currentEdges) => applyEdgeChanges(changes, currentEdges));
  }

  function handleFlowInit(instance: ReactFlowInstance<Node<GraphNodeData>, Edge>) {
    setFlowInstance(instance);
    setCanvasViewport(instance.getViewport());
  }

  function flowPositionFromScreenPoint(clientX: number, clientY: number): { x: number; y: number } | null {
    if (flowInstance) {
      return flowInstance.screenToFlowPosition({ x: clientX, y: clientY });
    }
    const canvasRect = canvasShellRef.current?.getBoundingClientRect();
    if (!canvasRect) return null;
    const zoom = canvasViewport.zoom || 1;
    return {
      x: (clientX - canvasRect.left - canvasViewport.x) / zoom,
      y: (clientY - canvasRect.top - canvasViewport.y) / zoom,
    };
  }

  function canvasPointFromScreenPoint(clientX: number, clientY: number): CanvasPoint | null {
    const canvasRect = canvasShellRef.current?.getBoundingClientRect();
    if (!canvasRect) return null;
    return {
      x: clientX - canvasRect.left,
      y: clientY - canvasRect.top,
    };
  }

  function nodePositionCenteredOnFlowPoint(flowPosition: { x: number; y: number }): { x: number; y: number } {
    return {
      x: flowPosition.x - FALLBACK_NODE_WIDTH / 2,
      y: flowPosition.y - FALLBACK_NODE_HEIGHT / 2,
    };
  }

  function nodePositionFromScreenPoint(clientX: number, clientY: number): { x: number; y: number } | null {
    const flowPosition = flowPositionFromScreenPoint(clientX, clientY);
    return flowPosition ? nodePositionCenteredOnFlowPoint(flowPosition) : null;
  }

  function nodePositionAtCanvasCenter(): { x: number; y: number } | null {
    const canvasRect = canvasShellRef.current?.getBoundingClientRect();
    if (!canvasRect) return null;
    return nodePositionFromScreenPoint(
      canvasRect.left + canvasRect.width / 2,
      canvasRect.top + canvasRect.height / 2,
    );
  }

  function nodeCenterCanvasPoint(node: Node<GraphNodeData>): CanvasPoint {
    const bounds = nodeBounds(node);
    return {
      x: (bounds.centerX * canvasViewport.zoom) + canvasViewport.x,
      y: (bounds.centerY * canvasViewport.zoom) + canvasViewport.y,
    };
  }

  function closeConnectionUi() {
    setNodeConnectionMenu(null);
    setPendingConnection(null);
  }

  function startPendingConnection(route: WorkflowConnectionRouteOption) {
    if (!nodeConnectionMenu || editLocked) return;
    setPendingConnection({
      sourceId: nodeConnectionMenu.nodeId,
      sourcePort: route.sourcePort,
      routeLabel: route.label,
      routeClass: route.routeClass,
      sourcePoint: nodeConnectionMenu.sourcePoint,
      cursorPoint: nodeConnectionMenu.position,
    });
    setNodeConnectionMenu(null);
    setSelectedEdgeId(null);
    setError(null);
    appShellRef.current?.focus();
  }

  function updatePendingConnectionCursor(clientX: number, clientY: number) {
    const cursorPoint = canvasPointFromScreenPoint(clientX, clientY);
    if (!cursorPoint) return;
    setPendingConnection((current) => current ? { ...current, cursorPoint } : current);
  }

  function addWorkflowEdge(sourceId: string, targetId: string, sourcePort?: string | null): boolean {
    if (!document || editLocked) return false;
    const normalizedSourcePort = normalizeWorkflowSourcePort(sourcePort);
    const validationError = validateConnection(
      document.config,
      catalog,
      sourceId,
      targetId,
      normalizedSourcePort,
    );
    if (validationError) {
      setError(validationError);
      return false;
    }
    const targetNode = nodeById(document.config, targetId);
    const allowCycle = targetNode?.type === "gate:for" && wouldCreateCycle(document.config, sourceId, targetId);
    const nextConfig = cloneConfig(document.config);
    const nextEdge: WorkflowEdge = {
      source_id: sourceId,
      target_id: targetId,
    };
    if (normalizedSourcePort) {
      nextEdge.source_port = normalizedSourcePort;
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
    return true;
  }

  function startNodeTypeDrag(
    event: ReactDragEvent<HTMLButtonElement>,
    entry: WorkflowEditorNodeCatalogEntry,
  ) {
    if (editLocked) {
      event.preventDefault();
      return;
    }
    event.dataTransfer.effectAllowed = "copy";
    event.dataTransfer.setData(
      NODE_TYPE_DRAG_DATA_MIME,
      JSON.stringify({ kind: entry.kind, type: entry.type } satisfies NodeTypeDragPayload),
    );
    event.dataTransfer.setData("text/plain", entry.type);
  }

  function handleNodeTypeDragOver(event: ReactDragEvent<HTMLElement>) {
    if (editLocked || !Array.from(event.dataTransfer.types).includes(NODE_TYPE_DRAG_DATA_MIME)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
  }

  function handleNodeTypeDrop(event: ReactDragEvent<HTMLElement>) {
    if (editLocked) return;
    const payload = parseNodeTypeDragPayload(event.dataTransfer);
    if (!payload) return;
    event.preventDefault();
    event.stopPropagation();
    addNode(payload.kind, payload.type, nodePositionFromScreenPoint(event.clientX, event.clientY));
  }

  function handleNodeContextMenu(event: React.MouseEvent, node: Node<GraphNodeData>) {
    event.preventDefault();
    event.stopPropagation();
    if (editLocked) return;
    const position = canvasPointFromScreenPoint(event.clientX, event.clientY);
    if (!position) return;
    setProgrammaticSelection([node.id], node.id);
    setSelectedEdgeId(null);
    setNodeConnectionMenu({
      nodeId: node.id,
      position,
      sourcePoint: nodeCenterCanvasPoint(node),
      routes: workflowConnectionRouteOptions(node.data.outputPorts),
    });
    setPendingConnection(null);
    setError(null);
    appShellRef.current?.focus();
  }

  function handleWorkflowNodeClick(event: React.MouseEvent, node: Node<GraphNodeData>) {
    if (pendingConnection) {
      event.stopPropagation();
      if (addWorkflowEdge(pendingConnection.sourceId, node.id, pendingConnection.sourcePort)) {
        setPendingConnection(null);
      }
      appShellRef.current?.focus();
      return;
    }
    setNodeConnectionMenu(null);
    setSelectedNodeId(node.id);
    setSelectedEdgeId(null);
    appShellRef.current?.focus();
  }

  function handleCanvasPointerMove(event: ReactPointerEvent<HTMLElement>) {
    if (!pendingConnection) return;
    updatePendingConnectionCursor(event.clientX, event.clientY);
  }

  function handlePaneClick() {
    if (pendingConnection || nodeConnectionMenu) {
      closeConnectionUi();
    }
    setSelectedEdgeId(null);
    setSnapGuides([]);
  }

  function handlePaneContextMenu(event: MouseEvent | React.MouseEvent) {
    if (!pendingConnection && !nodeConnectionMenu) return;
    event.preventDefault();
    closeConnectionUi();
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

  function copySelectedNodes() {
    if (!document || selectedNodeIds.length === 0) return false;
    const selectedNodeIdSet = new Set(selectedNodeIds);
    const nodesById = new Map(workflowNodes(document.config).map((node) => [node.id, node]));
    const positionsById = new Map(flowNodes.map((node) => [node.id, node.position]));
    const copiedNodes = selectedNodeIds
      .map((nodeId, index) => {
        const node = nodesById.get(nodeId);
        if (!node) return null;
        return {
          node: cloneNodeConfig(node),
          position: positionsById.get(nodeId) ?? document.layout.nodes[nodeId] ?? fallbackPosition(index),
        };
      })
      .filter((node): node is CopiedWorkflowNode => Boolean(node));
    if (copiedNodes.length === 0) return false;
    copiedSelectionRef.current = {
      nodes: copiedNodes,
      edges: document.config.edges
        .filter((edge) => selectedNodeIdSet.has(edge.source_id) && selectedNodeIdSet.has(edge.target_id))
        .map((edge) => ({ ...edge })),
      primaryNodeId:
        selectedNodeId && selectedNodeIdSet.has(selectedNodeId)
          ? selectedNodeId
          : copiedNodes[0]?.node.id ?? null,
    };
    setError(null);
    return true;
  }

  function pasteCopiedSelection() {
    const copiedSelection = copiedSelectionRef.current;
    if (!document || !copiedSelection || copiedSelection.nodes.length === 0 || editLocked) return false;
    const nextConfig = cloneConfig(document.config);
    nextConfig.triggers ??= [];
    nextConfig.gates ??= [];
    nextConfig.effects ??= [];
    nextConfig.queries ??= [];
    const idMap = new Map<string, string>();
    const duplicatedNodes: Array<{ kind: WorkflowNodeKind; node: WorkflowNodeConfig }> = [];
    for (const copiedNode of copiedSelection.nodes) {
      const templateNode = copiedNode.node;
      const templateKind = nodeKindFromType(templateNode.type);
      const nextId = generateNodeId(nextConfig, templateKind, templateNode.type);
      const duplicatedNode = cloneNodeConfig(templateNode);
      duplicatedNode.id = nextId;
      duplicatedNode.display_name = `${templateNode.display_name} Copy`;
      nextConfig[kindCollectionKey(templateKind)].push(duplicatedNode);
      idMap.set(templateNode.id, nextId);
      duplicatedNodes.push({ kind: templateKind, node: duplicatedNode });
    }
    for (const duplicatedNode of duplicatedNodes) {
      const collection = nextConfig[kindCollectionKey(duplicatedNode.kind)];
      const nodeIndex = collection.findIndex((node) => node.id === duplicatedNode.node.id);
      if (nodeIndex === -1) continue;
      collection[nodeIndex] = rewriteWorkflowNodeConfigBindings(duplicatedNode.node, idMap);
    }
    const duplicatedEdges = copiedSelection.edges
      .map((edge) => {
        const sourceId = idMap.get(edge.source_id);
        const targetId = idMap.get(edge.target_id);
        if (!sourceId || !targetId) return null;
        return {
          ...edge,
          source_id: sourceId,
          target_id: targetId,
        };
      })
      .filter((edge): edge is WorkflowEdge => Boolean(edge));
    nextConfig.edges = [...nextConfig.edges, ...duplicatedEdges];
    const nextLayout = cloneLayout(document.layout);
    for (const copiedNode of copiedSelection.nodes) {
      const nextId = idMap.get(copiedNode.node.id);
      if (!nextId) continue;
      nextLayout.nodes[nextId] = {
        x: copiedNode.position.x + 40,
        y: copiedNode.position.y + 40,
      };
    }
    const pastedNodeIds = copiedSelection.nodes
      .map((copiedNode) => idMap.get(copiedNode.node.id))
      .filter((nodeId): nodeId is string => Boolean(nodeId));
    const preferredPastedNodeId =
      copiedSelection.primaryNodeId ? idMap.get(copiedSelection.primaryNodeId) ?? null : null;
    patchDocument(nextConfig, nextLayout, undefined, pastedNodeIds);
    setProgrammaticSelection(pastedNodeIds, preferredPastedNodeId);
    setSelectedEdgeId(null);
    setError(null);
    return true;
  }

  function handleAppShellKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    if (isEditableEventTarget(event.target)) return;
    if (event.key === "Escape" && (pendingConnection || nodeConnectionMenu)) {
      event.preventDefault();
      closeConnectionUi();
      return;
    }
    const isCopy = (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "c";
    const isPaste = (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "v";
    if (isCopy) {
      if (copySelectedNodes()) event.preventDefault();
      return;
    }
    if (isPaste) {
      if (pasteCopiedSelection()) event.preventDefault();
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

  function addNode(kind: WorkflowNodeKind, type: string, requestedPosition?: { x: number; y: number } | null) {
    if (!document || editLocked) return;
    const nextConfig = cloneConfig(document.config);
    nextConfig.triggers ??= [];
    nextConfig.gates ??= [];
    nextConfig.effects ??= [];
    nextConfig.queries ??= [];
    const node = createDefaultNode(kind, type, generateNodeId(nextConfig, kind, type));
    nextConfig[kindCollectionKey(kind)].push(node);
    const nextLayout = cloneLayout(document.layout);
    const nextNodeIndex =
      nextConfig.triggers.length +
        nextConfig.gates.length +
        nextConfig.effects.length +
        nextConfig.queries.length -
        1;
    nextLayout.nodes[node.id] =
      requestedPosition ?? nodePositionAtCanvasCenter() ?? fallbackPosition(nextNodeIndex);
    patchDocument(nextConfig, nextLayout, undefined, [node.id]);
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

  function selectWorkflowGraph(graph: WorkflowGraphMetadata) {
    setSelectedGraphId(graph.id);
    setProgrammaticSelection(graph.node_ids, graph.node_ids[0] ?? null);
    setSelectedEdgeId(null);
    setError(null);
  }

  function applyWorkflowGraphChanges() {
    if (!document || !selectedGraph || editLocked) return;
    const nextName = graphNameDraft.trim();
    if (!nextName) {
      setError("Graph name is required.");
      return;
    }
    const nextDirectoryPaths = Array.from(
      new Set(
        graphDirectoriesDraft
          .split("\n")
          .map((directoryPath) => directoryPath.trim())
          .filter(Boolean),
      ),
    );
    if (nextDirectoryPaths.length === 0) {
      setError("At least one graph directory is required.");
      return;
    }
    const nextUserIds = Array.from(
      new Set(
        graphUsersDraft
          .split("\n")
          .map((userId) => userId.trim())
          .filter(Boolean),
      ),
    );
    const nextGraphs = document.graphs.map((graph) =>
      graph.id === selectedGraph.id
        ? {
            ...graph,
            name: nextName,
            description: graphDescriptionDraft.trim() || null,
            directory_paths: nextDirectoryPaths,
            user_ids: nextUserIds,
          }
        : graph,
    );
    patchDocument(document.config, document.layout, nextGraphs);
    setError(null);
  }

  function addCurrentUserToGraphDraft() {
    const normalizedUserId = currentUserId.trim();
    if (!normalizedUserId) return;
    const nextUserIds = Array.from(
      new Set([
        ...graphUsersDraft
          .split("\n")
          .map((userId) => userId.trim())
          .filter(Boolean),
        normalizedUserId,
      ]),
    );
    setGraphUsersDraft(nextUserIds.join("\n"));
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
      patchDocument(nextConfig, nextLayout, undefined, [rawId]);
      setProgrammaticSelection([rawId], rawId);
      setSelectedEdgeId(null);
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  function onConnect(connection: Connection) {
    if (!document || !connection.source || !connection.target || editLocked) return;
    addWorkflowEdge(connection.source, connection.target, connection.sourceHandle);
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
    const validation = await fetchJson<WorkflowEditorValidation>(
      "/api/workflow-editor/validate",
      {
        method: "POST",
        body: JSON.stringify({
          config: document.config,
          layout: document.layout,
          graphs: document.graphs,
        }),
      },
    );
    patchDocument(
      validation.config,
      validation.layout,
      validation.graphs,
      undefined,
      validation.validation_diagnostics,
      validation.output_schemas,
    );
  }

  async function saveDocument() {
    if (!document || editLocked) return;
    setSaveState("saving");
    setError(null);
    try {
      const saved = await fetchJson<WorkflowEditorSave>(
        "/api/workflow-editor/document",
        {
          method: "PUT",
          body: JSON.stringify({
            config: document.config,
            layout: document.layout,
            graphs: document.graphs,
          }),
        },
      );
      patchDocument(
        saved.config,
        saved.layout,
        saved.graphs,
        undefined,
        saved.validation_diagnostics,
        saved.output_schemas,
      );
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
          {renderFieldHeader({ ...effectTargetField, label: "Target" })}
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

  function renderFieldHeader(field: WorkflowEditorFieldDefinition) {
    return (
      <div className="field-header">
        <span>{field.label}</span>
        {field.description ? (
          <Tooltip
            content={field.description}
            ariaLabel={`${field.label} field help`}
            focusable
            triggerClassName="workflow-field-tooltip-trigger"
          >
            <span className="workflow-field-tooltip-icon" aria-hidden="true">i</span>
          </Tooltip>
        ) : null}
      </div>
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
            {renderFieldHeader(field)}
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
            {renderFieldHeader(field)}
            {renderModeSelect(field, mode, (nextMode) => {
              updateFieldMode(field.key, nextMode);
              if (nextMode === "bind") {
                updateKnownField(field.key, selectedNodeBindingOptions[0]?.value ?? "");
              } else {
                updateKnownField(field.key, typeof value === "number" ? value : undefined);
              }
            }, isDisabled)}
          </div>
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
            {renderFieldHeader(field)}
            {renderModeSelect(field, mode, (nextMode) => {
              updateFieldMode(field.key, nextMode);
              if (nextMode === "bind") {
                updateKnownField(field.key, selectedNodeBindingOptions[0]?.value ?? "");
              } else {
                updateKnownField(field.key, Boolean(value));
              }
            }, isDisabled)}
          </div>
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
            {renderFieldHeader(field)}
          </div>
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
      const selectedValues = new Set(
        listValue
          .split("\n")
          .map((item) => item.trim())
          .filter(Boolean),
      );
      if (field.options.length > 0) {
        return (
          <div key={field.key} className="inspector-field">
            <div className="field-toolbar">
              {renderFieldHeader(field)}
            </div>
            <div className="option-checkbox-list">
              {field.options.map((option) => {
                const checked = selectedValues.has(option.value);
                return (
                  <label key={option.value} className="checkbox-row option-checkbox-row">
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={(event) => {
                        const nextValues = new Set(selectedValues);
                        if (event.target.checked) {
                          nextValues.add(option.value);
                        } else {
                          nextValues.delete(option.value);
                        }
                        updateStringListField(field.key, Array.from(nextValues).join("\n"));
                      }}
                      disabled={isDisabled}
                    />
                    <span>{option.label}</span>
                  </label>
                );
              })}
            </div>
          </div>
        );
      }
      return (
        <label key={field.key} className="inspector-field">
          <div className="field-toolbar">
            {renderFieldHeader(field)}
          </div>
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
            {renderFieldHeader(field)}
            {renderModeSelect(field, mode, (nextMode) => {
              updateFieldMode(field.key, nextMode);
              if (nextMode === "bind") {
                updateKnownField(field.key, selectedNodeBindingOptions[0]?.value ?? "");
              } else {
                updateKnownField(field.key, undefined);
              }
            }, isDisabled)}
          </div>
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
            {renderFieldHeader(field)}
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
                        {renderFieldHeader(itemField)}
                        {itemField.bindable ? (
                          <label className="mode-select">
                            <span>Value mode</span>
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
    entries: catalog.nodes
      .filter((entry) => entry.kind === group.kind)
      .sort(compareNodeCatalogEntries),
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

            <div className="sidebar-section workflow-graphs-section">
              <div className="section-label">Graphs</div>
              <div className="workflow-graph-view-toggle" role="group" aria-label="Workflow graph view">
                <button
                  type="button"
                  className={workflowGraphView === "directory" ? "selected" : ""}
                  onClick={() => setWorkflowGraphView("directory")}
                >
                  Directory
                </button>
                <button
                  type="button"
                  className={workflowGraphView === "private" ? "selected" : ""}
                  onClick={() => setWorkflowGraphView("private")}
                >
                  Mine
                </button>
              </div>
              <p className="runtime-summary">{graphViewContext}</p>
              <div className="workflow-graph-list">
                {visibleWorkflowGraphs.length === 0 ? (
                  <p className="runtime-summary">
                    {workflowGraphView === "private"
                      ? "No private workflows associated with this user."
                      : "No graphs associated with this directory."}
                  </p>
                ) : (
                  visibleWorkflowGraphs.map((graph) => (
                    <button
                      type="button"
                      key={graph.id}
                      className={`workflow-graph-item${selectedGraphId === graph.id ? " selected" : ""}`}
                      onClick={() => selectWorkflowGraph(graph)}
                    >
                      <span className="workflow-graph-name">{graph.name}</span>
                      <span className="workflow-graph-meta">
                        {graph.node_ids.length} {graph.node_ids.length === 1 ? "node" : "nodes"}
                      </span>
                    </button>
                  ))
                )}
              </div>
              {selectedGraph ? (
                <div className="workflow-graph-editor">
                  <label className="inspector-label">
                    <span>Graph name</span>
                    <input
                      value={graphNameDraft}
                      onChange={(event) => setGraphNameDraft(event.target.value)}
                      disabled={editLocked}
                    />
                  </label>
                  <label className="inspector-label">
                    <span>Description</span>
                    <textarea
                      value={graphDescriptionDraft}
                      rows={3}
                      onChange={(event) => setGraphDescriptionDraft(event.target.value)}
                      disabled={editLocked}
                    />
                  </label>
                  <label className="inspector-label">
                    <span>Directories</span>
                    <textarea
                      value={graphDirectoriesDraft}
                      rows={3}
                      placeholder={selectedDirectoryPath}
                      onChange={(event) => setGraphDirectoriesDraft(event.target.value)}
                      disabled={editLocked}
                    />
                  </label>
                  <label className="inspector-label">
                    <span>Private users</span>
                    <textarea
                      value={graphUsersDraft}
                      rows={3}
                      placeholder={currentUserId}
                      onChange={(event) => setGraphUsersDraft(event.target.value)}
                      disabled={editLocked}
                    />
                  </label>
                  <button
                    type="button"
                    className="subtle-button"
                    onClick={addCurrentUserToGraphDraft}
                    disabled={editLocked || !currentUserId.trim()}
                  >
                    Add current user
                  </button>
                  <button
                    type="button"
                    className="primary"
                    onClick={applyWorkflowGraphChanges}
                    disabled={editLocked}
                  >
                    Apply graph details
                  </button>
                </div>
              ) : null}
            </div>

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
                      <Tooltip key={entry.type} content={nodeTypeDescription(entry)} inline={false}>
                        <button
                          type="button"
                          draggable={!editLocked}
                          onDragStart={(event) => startNodeTypeDrag(event, entry)}
                          onClick={() => addNode(entry.kind, entry.type)}
                          disabled={editLocked}
                        >
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
                {selectedNodeDiagnostics.length > 0 ? (
                  <div className="problems-panel">
                    <div className="problems-heading">
                      <span>Problems</span>
                      <span>{selectedNodeDiagnostics.length}</span>
                    </div>
                    <div className="problems-list">
                      {selectedNodeDiagnostics.map((diagnostic, index) => (
                        <div className="problem-item" key={`${diagnostic.code}-${diagnostic.field_path ?? ""}-${diagnostic.reference ?? ""}-${index}`}>
                          <div className="problem-message">{diagnostic.message}</div>
                          <div className="problem-meta">
                            {diagnostic.field_path ? <span>{diagnostic.field_path}</span> : null}
                            {diagnostic.reference ? <span>{diagnostic.reference}</span> : null}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null}
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
                  <div>Route: {selectedEdgeRouteLabel}</div>
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

          <main
            className="canvas-shell"
            ref={canvasShellRef}
            onDragOver={handleNodeTypeDragOver}
            onDrop={handleNodeTypeDrop}
            onPointerMove={handleCanvasPointerMove}
          >
            <ReactFlow
              nodes={displayedFlowNodes}
              edges={displayedFlowEdges}
              nodeTypes={nodeTypes}
              onInit={handleFlowInit}
              onMove={(_, viewport) => setCanvasViewport(viewport)}
              onNodesChange={updateNodePositions}
              onEdgesChange={updateEdgeChanges}
              onNodeClick={handleWorkflowNodeClick}
              onNodeContextMenu={handleNodeContextMenu}
              onEdgeClick={(_, edge) => {
                setNodeConnectionMenu(null);
                setPendingConnection(null);
                setProgrammaticSelection([]);
                setSelectedEdgeId(edge.id);
              }}
              onNodeDrag={(_, node, nodes) => updateDraggedNodePositions(node, nodes.length > 0 ? nodes : [node])}
              onNodeDragStop={(_, node, nodes) => persistNodePositions(nodes.length > 0 ? nodes : [node])}
              onPaneClick={handlePaneClick}
              onPaneContextMenu={handlePaneContextMenu}
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
            {pendingConnection ? (
              <svg className="pending-connection-layer" aria-hidden="true">
                <line
                  className={`pending-connection-line route-${pendingConnection.routeClass}`}
                  x1={pendingConnection.sourcePoint.x}
                  y1={pendingConnection.sourcePoint.y}
                  x2={pendingConnection.cursorPoint.x}
                  y2={pendingConnection.cursorPoint.y}
                />
                {pendingConnection.sourcePort ? (
                  <text
                    className="pending-connection-label"
                    x={(pendingConnection.sourcePoint.x + pendingConnection.cursorPoint.x) / 2}
                    y={(pendingConnection.sourcePoint.y + pendingConnection.cursorPoint.y) / 2 - 8}
                  >
                    {pendingConnection.routeLabel}
                  </text>
                ) : null}
              </svg>
            ) : null}
            {nodeConnectionMenu ? (
              <div
                className="node-context-menu"
                style={{
                  left: `${nodeConnectionMenu.position.x}px`,
                  top: `${nodeConnectionMenu.position.y}px`,
                }}
                role="menu"
              >
                <div className="node-context-menu-heading">Add connection</div>
                {nodeConnectionMenu.routes.map((route) => (
                  <button
                    key={route.sourcePort ?? "__default"}
                    type="button"
                    className={`node-context-menu-item route-${route.routeClass}`}
                    onClick={() => startPendingConnection(route)}
                    title={route.description ?? undefined}
                    role="menuitem"
                  >
                    {route.label}
                  </button>
                ))}
              </div>
            ) : null}
            <div className="workflow-graph-label-layer" aria-hidden="true">
              {graphLabels.map((label) => (
                <div
                  key={label.graph.id}
                  className={`workflow-graph-canvas-label${selectedGraphId === label.graph.id ? " selected" : ""}`}
                  style={{ left: `${label.left}px`, top: `${label.top}px` }}
                >
                  {label.graph.name}
                </div>
              ))}
            </div>
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
