import {
  type Edge,
  Handle,
  type Node,
  type NodeProps,
  Position,
  type Viewport,
} from "@xyflow/react";
import { Tooltip } from "../../features/admin/Tooltip";
import {
  type WorkflowConfig,
  type WorkflowEditorCatalog,
  type WorkflowEditorNodePortDefinition,
  type WorkflowGraphMetadata,
  type WorkflowLayout,
  type WorkflowNodeActivityCounts,
  type WorkflowNodeConfig,
  type WorkflowNodeKind,
  type WorkflowValidationDiagnostic,
} from "../../types";
import { workflowNodeBounds as nodeBounds } from "../../workflowNodeSnap";
import {
  humanizeWorkflowRouteKey,
  isSwitchGateType,
  normalizeWorkflowSourcePort,
  workflowEdgeRouteLabel,
  workflowOutputHandleIds,
  workflowOutputPortsForNode,
  workflowRouteClass,
} from "../../workflowRoutes";
import { workflowTriggerEnabled } from "../../workflowTriggerState";
import {
  NODE_KIND_DESCRIPTIONS,
  NODE_STATUS_ORDER,
  NODE_TYPE_DESCRIPTIONS,
  NODE_TYPE_DRAG_DATA_MIME,
  WORKFLOW_COMPACT_BREAKPOINT,
} from "./editorConstants";
import {
  type GraphNodeData,
  type GraphNodeStatusBadge,
  type NodeTypeDragPayload,
  type WorkflowGraphLabel,
} from "./editorTypes";
import { getCatalogEntry } from "./editorValues";
import { nodeTypeDescription } from "./fieldModels";
import {
  edgeId,
  fallbackPosition,
  nodeById,
  nodeCollections,
  nodeKindFromType,
} from "./graphModel";

export function isCompactCanvasTouchViewport(): boolean {
  if (typeof window === "undefined") return false;
  return window.innerWidth <= WORKFLOW_COMPACT_BREAKPOINT;
}

export function eventTargetInsideWorkflowNodeDragSurface(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false;
  const dragSurface = target.closest(".node-drag-surface");
  if (!dragSurface) return false;
  const node = dragSurface.closest(".react-flow__node");
  return Boolean(node && node.contains(dragSurface));
}

export function WorkflowNodeCard({ data, selected }: NodeProps) {
  const nodeData = data as GraphNodeData;
  const kindDescription = NODE_KIND_DESCRIPTIONS[nodeData.kind];
  const typeDescription =
    nodeData.description || NODE_TYPE_DESCRIPTIONS[nodeData.type] || `${nodeData.type} node.`;
  return (
    <div
      className={`node-card node-${nodeData.kind}${selected ? " selected" : ""}${
        nodeData.dominantStatus ? ` runtime-${nodeData.dominantStatus}` : ""
      }${nodeData.hasErrorDiagnostics ? " invalid" : ""}${
        nodeData.pendingConnectionRole ? ` connection-${nodeData.pendingConnectionRole}` : ""
      }${nodeData.hasUnsavedChanges ? " unsaved" : ""}${nodeData.isDisabled ? " trigger-disabled" : ""}${nodeData.isInspectGhost ? " inspect-ghost" : ""}${nodeData.runtimePulse ? " runtime-pulse" : ""}${
        nodeData.tutorialHighlight ? ` tutorial-highlight tutorial-highlight-${nodeData.tutorialHighlightMode ?? "pulse"}` : ""
      }${nodeData.tutorialBlocked ? " tutorial-blocked" : ""}`}
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
            {nodeData.diagnosticErrorCount > 0 ? (
              <Tooltip content={`${nodeData.diagnosticErrorCount} workflow binding error${nodeData.diagnosticErrorCount === 1 ? "" : "s"}`}>
                <div className="node-diagnostic-badge error">{nodeData.diagnosticErrorCount}</div>
              </Tooltip>
            ) : null}
            {nodeData.diagnosticWarningCount > 0 ? (
              <Tooltip content={`${nodeData.diagnosticWarningCount} workflow binding warning${nodeData.diagnosticWarningCount === 1 ? "" : "s"}`}>
                <div className="node-diagnostic-badge warning">{nodeData.diagnosticWarningCount}</div>
              </Tooltip>
            ) : null}
            {nodeData.hasUnsavedChanges ? (
              <Tooltip content="Unsaved changes">
                <div className="node-unsaved-badge" aria-label="Unsaved changes">
                  Unsaved changes
                </div>
              </Tooltip>
            ) : null}
            {nodeData.isDisabled ? (
              <Tooltip content="Trigger disabled">
                <div className="node-disabled-badge" aria-label="Trigger disabled">
                  Disabled
                </div>
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
      {workflowOutputHandleIds(nodeData.outputPorts).map((portId) => (
        <Handle
          key={`output-${portId}`}
          id={portId}
          type="source"
          position={Position.Right}
          className="workflow-handle workflow-technical-handle"
        />
      ))}
    </div>
  );
}

export const nodeTypes = {
  workflowNode: WorkflowNodeCard,
};

export function nodeStatusBuckets(
  activity: WorkflowNodeActivityCounts | undefined,
): Array<{ status: string; count: number }> {
  if (!activity) return [];
  // An error-routed execution reached its configured error edge. Keep it in the
  // run history, but do not leave a non-actionable alert on the canvas node.
  const displayableActivity = Object.fromEntries(
    Object.entries(activity).filter(([status]) => status !== "error_routed"),
  );
  const orderedStatuses = new Set<string>(NODE_STATUS_ORDER);
  const buckets: Array<{ status: string; count: number }> = [];
  for (const status of NODE_STATUS_ORDER) {
    const count = displayableActivity[status] ?? 0;
    if (count > 0) {
      buckets.push({ status, count });
    }
  }
  for (const [status, count] of Object.entries(displayableActivity)) {
    if (!orderedStatuses.has(status) && count > 0) {
      buckets.push({ status, count });
    }
  }
  return buckets;
}

export function dominantNodeStatus(activity: WorkflowNodeActivityCounts | undefined): string | null {
  return nodeStatusBuckets(activity)[0]?.status ?? null;
}

export function nodeStatusBadges(activity: WorkflowNodeActivityCounts | undefined): GraphNodeStatusBadge[] {
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

export function buildNodes(
  config: WorkflowConfig,
  layout: WorkflowLayout,
  catalog: WorkflowEditorCatalog,
  nodeActivity: Record<string, WorkflowNodeActivityCounts> = {},
  diagnostics: WorkflowValidationDiagnostic[] = [],
  currentPositions: Record<string, { x: number; y: number }> = {},
  selectedNodeIds: string[] = [],
  dirtyNodeIds: ReadonlySet<string> = new Set<string>(),
  ghostNodeIds: ReadonlySet<string> = new Set<string>(),
  runtimePulseNodeIds: ReadonlySet<string> = new Set<string>(),
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
    const outputPorts = workflowOutputPortsForNode(node, catalogEntry?.output_ports ?? []);
    const nodeDiagnostics = diagnostics.filter((diagnostic) => diagnostic.node_id === node.id);
    const diagnosticErrorCount = nodeDiagnostics.filter((diagnostic) => (diagnostic.severity ?? "error") !== "warning").length;
    const diagnosticWarningCount = nodeDiagnostics.filter((diagnostic) => (diagnostic.severity ?? "error") === "warning").length;
    const diagnosticCount = diagnosticErrorCount + diagnosticWarningCount;
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
        outputPorts,
        dominantStatus: dominantNodeStatus(activity),
        statusBadges: nodeStatusBadges(activity),
        diagnosticCount,
        diagnosticErrorCount,
        diagnosticWarningCount,
        hasDiagnostics: diagnosticCount > 0,
        hasErrorDiagnostics: diagnosticErrorCount > 0,
        hasUnsavedChanges: dirtyNodeIds.has(node.id),
        isDisabled: kind === "trigger" && !workflowTriggerEnabled(node),
        isInspectGhost: ghostNodeIds.has(node.id),
        runtimePulse: runtimePulseNodeIds.has(node.id),
      },
    };
  });
}

export function buildEdges(config: WorkflowConfig, catalog: WorkflowEditorCatalog): Edge[] {
  return config.edges.map((edge) => {
    const sourceNode = nodeById(config, edge.source_id);
    const sourceCatalogEntry = sourceNode ? getCatalogEntry(catalog, sourceNode.type) : undefined;
    const sourceOutputPorts = sourceNode
      ? workflowOutputPortsForNode(sourceNode, sourceCatalogEntry?.output_ports ?? [])
      : sourceCatalogEntry?.output_ports ?? [];
    const routeClass = workflowRouteClass(edge.source_port);
    return {
      id: edgeId(edge),
      source: edge.source_id,
      sourceHandle: edge.source_port,
      target: edge.target_id,
      label: workflowSwitchEdgeRouteLabel(sourceNode, edge.source_port, sourceOutputPorts),
      animated: Boolean(edge.allow_cycle),
      className: `workflow-edge workflow-edge-route-${routeClass}`,
    };
  });
}

export function workflowSwitchEdgeRouteLabel(
  sourceNode: WorkflowNodeConfig | undefined,
  sourcePort: string | null | undefined,
  outputPorts: ReadonlyArray<WorkflowEditorNodePortDefinition>,
): string | undefined {
  const routeLabel = workflowEdgeRouteLabel(sourcePort, outputPorts);
  if (routeLabel !== undefined || !isSwitchGateType(sourceNode?.type)) {
    return routeLabel;
  }
  const normalizedSourcePort = normalizeWorkflowSourcePort(sourcePort);
  return normalizedSourcePort ? humanizeWorkflowRouteKey(normalizedSourcePort) : undefined;
}

export function isEditableEventTarget(target: EventTarget | null): boolean {
  return (
    target instanceof HTMLInputElement ||
    target instanceof HTMLTextAreaElement ||
    target instanceof HTMLSelectElement ||
    (target instanceof HTMLElement && target.isContentEditable)
  );
}

export function currentPageVisible(): boolean {
  return typeof globalThis.document === "undefined" || globalThis.document.visibilityState !== "hidden";
}

export function currentPositionsFromNodes(nodes: Node<GraphNodeData>[]): Record<string, { x: number; y: number }> {
  return Object.fromEntries(nodes.map((node) => [node.id, node.position]));
}

export function workflowGraphLabels(
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
    const anchor = graphNodes.map((node) => nodeBounds(node)).reduce((first, bounds) => (
      bounds.left < first.left || (bounds.left === first.left && bounds.top < first.top)
        ? bounds
        : first
    ));
    return [
      {
        graph,
        left: anchor.left * viewport.zoom + viewport.x - 24,
        top: anchor.centerY * viewport.zoom + viewport.y,
      },
    ];
  });
}

export function isWorkflowNodeKind(value: unknown): value is WorkflowNodeKind {
  return value === "trigger" || value === "gate" || value === "effect" || value === "query";
}

export function parseNodeTypeDragPayload(dataTransfer: DataTransfer): NodeTypeDragPayload | null {
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
