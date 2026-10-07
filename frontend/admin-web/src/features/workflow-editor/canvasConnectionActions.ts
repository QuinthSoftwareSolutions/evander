import {
  type Connection,
  type Edge,
  type Node,
} from "@xyflow/react";
import {
  type Dispatch,
  type MutableRefObject,
  type DragEvent as ReactDragEvent,
  type SetStateAction,
} from "react";
import {
  type WorkflowConfig,
  type WorkflowEdge,
  type WorkflowEditorCatalog,
  type WorkflowEditorDocument,
  type WorkflowEditorNodeCatalogEntry,
  type WorkflowGraphMetadata,
  type WorkflowLayout,
  type WorkflowNodeKind,
  type WorkflowOutputSchemas,
  type WorkflowValidationDiagnostic,
} from "../../types";
import { type SnapGuide } from "../../workflowNodeSnap";
import {
  type WorkflowConnectionRouteOption,
  isSwitchGateType,
  normalizeWorkflowEdgeSourcePort,
  workflowConnectionRouteOptions,
} from "../../workflowRoutes";
import { type WorkflowTutorialRequiredAction } from "../../workflowTutorials";
import { parseNodeTypeDragPayload } from "./canvasModel";
import { NODE_TYPE_DRAG_DATA_MIME } from "./editorConstants";
import {
  type CanvasPoint,
  type CompactCanvasMode,
  type GraphNodeData,
  type NodeConnectionMenuState,
  type NodeTypeDragPayload,
  type PendingConnectionState,
} from "./editorTypes";
import {
  cloneConfig,
  cloneLayout,
  edgeId,
  nodeById,
  validateConnection,
  wouldCreateCycle,
} from "./graphModel";

type CanvasConnectionActionsInputs = {
  setNodeConnectionMenu: Dispatch<SetStateAction<NodeConnectionMenuState | null>>;
  setPendingConnection: Dispatch<SetStateAction<PendingConnectionState | null>>;
  setSelectedEdgeId: Dispatch<SetStateAction<string | null>>;
  setWorkflowTutorialHint: Dispatch<SetStateAction<string | null>>;
  setError: Dispatch<SetStateAction<string | null>>;
  appShellRef: MutableRefObject<HTMLDivElement | null>;
  tutorialCurrentRequiredAction: () => WorkflowTutorialRequiredAction | null;
  isTutorialMode: boolean;
  nodeConnectionMenu: NodeConnectionMenuState | null;
  tutorialActionHintFor: (requiredAction: WorkflowTutorialRequiredAction | null) => string;
  workflowMutationLocked: boolean;
  nodeCenterCanvasPoint: (node: Node<GraphNodeData>) => CanvasPoint;
  setProgrammaticSelection: (nextNodeIds: string[], preferredNodeId?: string | null) => void;
  setSnapGuides: Dispatch<SetStateAction<SnapGuide[]>>;
  canvasPointFromScreenPoint: (clientX: number, clientY: number) => CanvasPoint | null;
  activeWorkflowDocument: WorkflowEditorDocument | null;
  activeWorkflowCatalog: WorkflowEditorCatalog;
  patchWorkflowDocument: (nextConfig: WorkflowConfig, nextLayout: WorkflowLayout, nextGraphs?: WorkflowGraphMetadata[], selectedNodeIdsAfterPatch?: string[], diagnosticsOverride?: WorkflowValidationDiagnostic[], outputSchemasOverride?: WorkflowOutputSchemas, revisionOverride?: string) => void;
  addNode: (kind: WorkflowNodeKind, type: string, requestedPosition?: { x: number; y: number; } | null) => void;
  nodePositionFromScreenPoint: (clientX: number, clientY: number) => { x: number; y: number; } | null;
  isInspectMode: boolean;
  setSelectedNodeIds: Dispatch<SetStateAction<string[]>>;
  setSelectedNodeId: Dispatch<SetStateAction<string | null>>;
  pendingConnection: PendingConnectionState | null;
  tutorialAllowsAction: (action: WorkflowTutorialRequiredAction) => boolean;
  completeTutorialAction: (action: WorkflowTutorialRequiredAction) => boolean;
  isCompactCanvasTouchMode: boolean;
  compactCanvasMode: CompactCanvasMode;
  selectedNodeIds: string[];
  document: WorkflowEditorDocument | null;
  patchDocument: (nextConfig: WorkflowConfig, nextLayout: WorkflowLayout, nextGraphs?: WorkflowGraphMetadata[], selectedNodeIdsAfterPatch?: string[], diagnosticsOverride?: WorkflowValidationDiagnostic[], outputSchemasOverride?: WorkflowOutputSchemas, revisionOverride?: string, options?: { recordUndo?: boolean; }) => void;
  deleteNodes: (nodeIds: string[]) => void;
};

/** Binds canvas connection actions to the current render state. */
export function createCanvasConnectionActions({
  setNodeConnectionMenu,
  setPendingConnection,
  setSelectedEdgeId,
  setWorkflowTutorialHint,
  setError,
  appShellRef,
  tutorialCurrentRequiredAction,
  isTutorialMode,
  nodeConnectionMenu,
  tutorialActionHintFor,
  workflowMutationLocked,
  nodeCenterCanvasPoint,
  setProgrammaticSelection,
  setSnapGuides,
  canvasPointFromScreenPoint,
  activeWorkflowDocument,
  activeWorkflowCatalog,
  patchWorkflowDocument,
  addNode,
  nodePositionFromScreenPoint,
  isInspectMode,
  setSelectedNodeIds,
  setSelectedNodeId,
  pendingConnection,
  tutorialAllowsAction,
  completeTutorialAction,
  isCompactCanvasTouchMode,
  compactCanvasMode,
  selectedNodeIds,
  document,
  patchDocument,
  deleteNodes,
}: CanvasConnectionActionsInputs) {
  function closeConnectionUi() {
    setNodeConnectionMenu(null);
    setPendingConnection(null);
  }

  function setPendingConnectionFromRoute(
    sourceId: string,
    route: WorkflowConnectionRouteOption,
    sourcePoint: CanvasPoint,
    cursorPoint: CanvasPoint = sourcePoint,
  ) {
    setPendingConnection({
      sourceId,
      sourcePort: route.sourcePort,
      routeLabel: route.label,
      routeClass: route.routeClass,
      sourcePoint,
      cursorPoint,
    });
    setNodeConnectionMenu(null);
    setSelectedEdgeId(null);
    setWorkflowTutorialHint(null);
    setError(null);
    appShellRef.current?.focus();
  }

  function startPendingConnection(route: WorkflowConnectionRouteOption) {
    const expectedAction = tutorialCurrentRequiredAction();
    const canStartConnection = Boolean(
      isTutorialMode &&
      expectedAction?.kind === "connect" &&
      nodeConnectionMenu &&
      expectedAction.sourceNodeId === nodeConnectionMenu.nodeId &&
      expectedAction.sourcePort === route.sourcePort,
    );
    if (isTutorialMode && expectedAction?.kind === "connect" && !canStartConnection) {
      setWorkflowTutorialHint(tutorialActionHintFor(expectedAction));
      return;
    }
    if (workflowMutationLocked && !canStartConnection) {
      return;
    }
    if (!nodeConnectionMenu) return;
    setPendingConnectionFromRoute(
      nodeConnectionMenu.nodeId,
      route,
      nodeConnectionMenu.sourcePoint,
      nodeConnectionMenu.position,
    );
  }

  function startCompactConnectionFromNode(node: Node<GraphNodeData>) {
    if (workflowMutationLocked) return;
    const sourcePoint = nodeCenterCanvasPoint(node);
    const implicitGateDefault = node.data.type.startsWith("gate:") &&
      !isSwitchGateType(node.data.type) &&
      node.data.outputPorts.every((port) => port.key === "error");
    const routes = isSwitchGateType(node.data.type) && node.data.outputPorts.length === 0
      ? []
      : workflowConnectionRouteOptions(node.data.outputPorts, implicitGateDefault);
    setProgrammaticSelection([node.id], node.id);
    setSelectedEdgeId(null);
    setSnapGuides([]);
    if (routes.length === 1 && routes[0]) {
      setPendingConnectionFromRoute(node.id, routes[0], sourcePoint);
      return;
    }
    setNodeConnectionMenu({
      nodeId: node.id,
      position: sourcePoint,
      sourcePoint,
      routes,
      failedCount: node.data.statusBadges.find((badge) => badge.status === "failed")?.count ?? 0,
    });
    setPendingConnection(null);
    setError(null);
    appShellRef.current?.focus();
  }

  function updatePendingConnectionCursor(clientX: number, clientY: number) {
    const cursorPoint = canvasPointFromScreenPoint(clientX, clientY);
    if (!cursorPoint) return;
    setPendingConnection((current) => current ? { ...current, cursorPoint } : current);
  }

  function addWorkflowEdge(sourceId: string, targetId: string, sourcePort?: string | null): boolean {
    const workflowDocument = activeWorkflowDocument;
    if (!workflowDocument) return false;
    const sourceNode = nodeById(workflowDocument.config, sourceId);
    const normalizedSourcePort = normalizeWorkflowEdgeSourcePort(sourceNode?.type, sourcePort);
    const validationError = validateConnection(
      workflowDocument.config,
      activeWorkflowCatalog,
      sourceId,
      targetId,
      normalizedSourcePort,
    );
    if (validationError) {
      setError(validationError);
      return false;
    }
    const targetNode = nodeById(workflowDocument.config, targetId);
    const allowCycle = (
      targetNode?.type === "gate:for" || targetNode?.type === "gate:while"
    ) && wouldCreateCycle(workflowDocument.config, sourceId, targetId);
    const nextConfig = cloneConfig(workflowDocument.config);
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
    if (sourceNode?.type === "trigger:event" && targetNode?.type === "gate:event:validate") {
      const gate = nextConfig.gates.find((node) => node.id === targetId);
      if (gate && !gate.config.event_type) {
        gate.config.event_type = `\u0024{{${sourceId}.event_type}}`;
        gate.config.payload = `\u0024{{${sourceId}.payload}}`;
      }
    }
    patchWorkflowDocument(nextConfig, cloneLayout(workflowDocument.layout));
    setSelectedEdgeId(null);
    setError(null);
    return true;
  }

  function startNodeTypeDrag(
    event: ReactDragEvent<HTMLButtonElement>,
    entry: WorkflowEditorNodeCatalogEntry,
  ) {
    const expectedAction = tutorialCurrentRequiredAction();
    const canDrag = Boolean(
      isTutorialMode &&
      expectedAction?.kind === "add-node" &&
      expectedAction.nodeKind === entry.kind &&
      expectedAction.nodeType === entry.type,
    );
    if (isTutorialMode && expectedAction?.kind === "add-node" && !canDrag) {
      event.preventDefault();
      setWorkflowTutorialHint(tutorialActionHintFor(expectedAction));
      return;
    }
    if (workflowMutationLocked && !canDrag) {
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
    const expectedAction = tutorialCurrentRequiredAction();
    if (
      workflowMutationLocked &&
      !(
        isTutorialMode &&
        expectedAction?.kind === "add-node"
      )
    ) return;
    if (!Array.from(event.dataTransfer.types).includes(NODE_TYPE_DRAG_DATA_MIME)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
  }

  function handleNodeTypeDrop(event: ReactDragEvent<HTMLElement>) {
    const expectedAction = tutorialCurrentRequiredAction();
    if (
      workflowMutationLocked &&
      !(
        isTutorialMode &&
        expectedAction?.kind === "add-node"
      )
    ) return;
    const payload = parseNodeTypeDragPayload(event.dataTransfer);
    if (!payload) return;
    if (
      isTutorialMode &&
      expectedAction?.kind === "add-node" &&
      (expectedAction.nodeKind !== payload.kind || expectedAction.nodeType !== payload.type)
    ) {
      setWorkflowTutorialHint(tutorialActionHintFor(expectedAction));
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    addNode(payload.kind, payload.type, nodePositionFromScreenPoint(event.clientX, event.clientY));
  }

  function handleNodeContextMenu(event: React.MouseEvent, node: Node<GraphNodeData>) {
    event.preventDefault();
    event.stopPropagation();
    const expectedAction = tutorialCurrentRequiredAction();
    if (
      workflowMutationLocked &&
      !(
        isTutorialMode &&
        expectedAction?.kind === "connect" &&
        expectedAction.sourceNodeId === node.id
      )
    ) return;
    if (isTutorialMode && expectedAction?.kind === "connect" && expectedAction.sourceNodeId !== node.id) {
      setWorkflowTutorialHint(tutorialActionHintFor(expectedAction));
      return;
    }
    const position = canvasPointFromScreenPoint(event.clientX, event.clientY);
    if (!position) return;
    setProgrammaticSelection([node.id], node.id);
    setSelectedEdgeId(null);
    setNodeConnectionMenu({
      nodeId: node.id,
      position,
      sourcePoint: nodeCenterCanvasPoint(node),
      routes: isSwitchGateType(node.data.type) && node.data.outputPorts.length === 0
        ? []
        : workflowConnectionRouteOptions(
            node.data.outputPorts,
            node.data.type.startsWith("gate:") &&
              !isSwitchGateType(node.data.type) &&
              node.data.outputPorts.every((port) => port.key === "error"),
          ),
      failedCount: node.data.statusBadges.find((badge) => badge.status === "failed")?.count ?? 0,
    });
    setPendingConnection(null);
    setError(null);
    setWorkflowTutorialHint(null);
    appShellRef.current?.focus();
  }

  function handleWorkflowNodeClick(event: React.MouseEvent, node: Node<GraphNodeData>) {
    if (isInspectMode) {
      event.stopPropagation();
      setNodeConnectionMenu(null);
      setPendingConnection(null);
      setSelectedNodeIds([node.id]);
      setSelectedNodeId(node.id);
      setSelectedEdgeId(null);
      setSnapGuides([]);
      appShellRef.current?.focus();
      return;
    }
    if (isTutorialMode) {
      const expectedAction = tutorialCurrentRequiredAction();
      if (pendingConnection) {
        const connectAction: WorkflowTutorialRequiredAction = {
          kind: "connect",
          sourceNodeId: pendingConnection.sourceId,
          sourcePort: pendingConnection.sourcePort,
          targetNodeId: node.id,
        };
        if (tutorialAllowsAction(connectAction)) {
          event.stopPropagation();
          if (addWorkflowEdge(pendingConnection.sourceId, node.id, pendingConnection.sourcePort)) {
            setPendingConnection(null);
            setProgrammaticSelection([node.id], node.id);
            setSelectedEdgeId(null);
            setSnapGuides([]);
            completeTutorialAction(connectAction);
          }
          appShellRef.current?.focus();
          return;
        }
        setWorkflowTutorialHint(tutorialActionHintFor(expectedAction));
        event.stopPropagation();
        appShellRef.current?.focus();
        return;
      }
      if (expectedAction?.kind === "select-node" && expectedAction.nodeId === node.id) {
        event.stopPropagation();
        setNodeConnectionMenu(null);
        setPendingConnection(null);
        setProgrammaticSelection([node.id], node.id);
        setSelectedEdgeId(null);
        setSnapGuides([]);
        setWorkflowTutorialHint(null);
        completeTutorialAction({ kind: "select-node", nodeId: node.id });
        appShellRef.current?.focus();
        return;
      }
      if (expectedAction?.kind === "connect" && expectedAction.sourceNodeId === node.id) {
        event.stopPropagation();
        setProgrammaticSelection([node.id], node.id);
        setSelectedEdgeId(null);
        setWorkflowTutorialHint(null);
        appShellRef.current?.focus();
        return;
      }
      event.stopPropagation();
      setWorkflowTutorialHint(tutorialActionHintFor(expectedAction));
      appShellRef.current?.focus();
      return;
    }
    if (pendingConnection) {
      event.stopPropagation();
      if (addWorkflowEdge(pendingConnection.sourceId, node.id, pendingConnection.sourcePort)) {
        setPendingConnection(null);
      }
      appShellRef.current?.focus();
      return;
    }
    if (isCompactCanvasTouchMode && compactCanvasMode === "connect") {
      event.stopPropagation();
      startCompactConnectionFromNode(node);
      return;
    }
    setNodeConnectionMenu(null);
    const additiveSelection = event.ctrlKey || event.metaKey;
    setProgrammaticSelection(additiveSelection ? [...selectedNodeIds, node.id] : [node.id], node.id);
    setSelectedEdgeId(null);
    appShellRef.current?.focus();
  }

  function handlePaneClick() {
    if (isTutorialMode) {
      if (pendingConnection || nodeConnectionMenu) {
        closeConnectionUi();
      }
      return;
    }
    if (pendingConnection || nodeConnectionMenu) {
      closeConnectionUi();
    }
    if (isInspectMode) {
      setSelectedNodeIds([]);
      setSelectedNodeId(null);
    }
    setSelectedEdgeId(null);
    setSnapGuides([]);
  }

  function handlePaneContextMenu(event: MouseEvent | React.MouseEvent) {
    if (!pendingConnection && !nodeConnectionMenu) return;
    event.preventDefault();
    closeConnectionUi();
  }

  function onConnect(connection: Connection) {
    if (!document || !connection.source || !connection.target) return;
    if (workflowMutationLocked && !(isTutorialMode && tutorialCurrentRequiredAction()?.kind === "connect")) return;
    addWorkflowEdge(connection.source, connection.target, connection.sourceHandle);
  }

  function onEdgesDelete(deletedEdges: Edge[]) {
    if (!document || workflowMutationLocked) return;
    const deleted = new Set(deletedEdges.map((edge) => edge.id));
    const nextConfig = cloneConfig(document.config);
    nextConfig.edges = nextConfig.edges.filter(
      (edge) => !deleted.has(edgeId(edge)),
    );
    patchDocument(nextConfig, cloneLayout(document.layout));
    setSelectedEdgeId(null);
  }

  function onNodesDelete(deletedNodes: Node[]) {
    if (workflowMutationLocked) return;
    deleteNodes(deletedNodes.map((node) => node.id));
  }

  return {
    closeConnectionUi,
    updatePendingConnectionCursor,
    startNodeTypeDrag,
    handleNodeTypeDragOver,
    handleNodeTypeDrop,
    handleWorkflowNodeClick,
    handleNodeContextMenu,
    handlePaneClick,
    handlePaneContextMenu,
    onConnect,
    onEdgesDelete,
    onNodesDelete,
    startPendingConnection,
  };
}
