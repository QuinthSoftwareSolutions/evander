import {
  type Edge,
  type Node,
  type NodeChange,
  type ReactFlowInstance,
  type Viewport,
  applyEdgeChanges,
  applyNodeChanges,
} from "@xyflow/react";
import {
  type Dispatch,
  type MutableRefObject,
  type PointerEvent as ReactPointerEvent,
  type Touch as ReactTouch,
  type TouchEvent as ReactTouchEvent,
  type SetStateAction,
} from "react";
import { type WorkflowEditorDocument } from "../../types";
import {
  FALLBACK_NODE_HEIGHT,
  FALLBACK_NODE_WIDTH,
  type SnapGuide,
  workflowNodeBounds as nodeBounds,
  snapDraggedNodes,
} from "../../workflowNodeSnap";
import { eventTargetInsideWorkflowNodeDragSurface } from "./canvasModel";
import {
  type CanvasPoint,
  type CompactActivePanel,
  type CompactCanvasMode,
  type CompactCanvasTouchMode,
  type CompactPanePanState,
  type GraphNodeData,
  type PendingConnectionState,
  type SaveState,
  type ScreenPoint,
} from "./editorTypes";
import { cloneLayout, workflowUndoDocumentsEqual } from "./graphModel";

type CanvasViewportActionsInputs = {
  setFlowNodes: Dispatch<SetStateAction<Node<GraphNodeData>[]>>;
  visibleGraphNodeIds: Set<string>;
  setSnapGuides: Dispatch<SetStateAction<SnapGuide[]>>;
  setFlowEdges: Dispatch<SetStateAction<Edge[]>>;
  setFlowInstance: Dispatch<SetStateAction<ReactFlowInstance<Node<GraphNodeData>, Edge> | null>>;
  setCanvasViewport: Dispatch<SetStateAction<Viewport>>;
  flowInstance: ReactFlowInstance<Node<GraphNodeData>, Edge> | null;
  setCompactCanvasMode: Dispatch<SetStateAction<CompactCanvasMode>>;
  compactCanvasTouchModeRef: MutableRefObject<CompactCanvasTouchMode>;
  compactPanePanRef: MutableRefObject<CompactPanePanState | null>;
  closeConnectionUi: () => void;
  setCompactActivePanel: Dispatch<SetStateAction<CompactActivePanel>>;
  canvasShellRef: MutableRefObject<HTMLElement | null>;
  canvasViewport: Viewport;
  canvasCursorPointRef: MutableRefObject<ScreenPoint | null>;
  pendingConnection: PendingConnectionState | null;
  updatePendingConnectionCursor: (clientX: number, clientY: number) => void;
  isCompactCanvasTouchMode: boolean;
  compactCanvasMode: CompactCanvasMode;
  workflowMutationLocked: boolean;
  document: WorkflowEditorDocument | null;
  flowNodes: Node<GraphNodeData>[];
  rememberWorkflowUndoSnapshot: (snapshot: WorkflowEditorDocument) => void;
  setDocument: Dispatch<SetStateAction<WorkflowEditorDocument | null>>;
  setSaveState: Dispatch<SetStateAction<SaveState>>;
};

/** Binds canvas viewport actions to the current render state. */
export function createCanvasViewportActions({
  setFlowNodes,
  visibleGraphNodeIds,
  setSnapGuides,
  setFlowEdges,
  setFlowInstance,
  setCanvasViewport,
  flowInstance,
  setCompactCanvasMode,
  compactCanvasTouchModeRef,
  compactPanePanRef,
  closeConnectionUi,
  setCompactActivePanel,
  canvasShellRef,
  canvasViewport,
  canvasCursorPointRef,
  pendingConnection,
  updatePendingConnectionCursor,
  isCompactCanvasTouchMode,
  compactCanvasMode,
  workflowMutationLocked,
  document,
  flowNodes,
  rememberWorkflowUndoSnapshot,
  setDocument,
  setSaveState,
}: CanvasViewportActionsInputs) {
  function updateNodePositions(changes: NodeChange[]) {
    setFlowNodes((currentNodes) => applyNodeChanges(changes, currentNodes) as Node<GraphNodeData>[]);
  }

  function snapVisibleDraggedNodes(
    activeNode: Node<GraphNodeData>,
    draggedNodes: Node<GraphNodeData>[],
    currentNodes: Node<GraphNodeData>[],
  ) {
    const nodesToSnap = draggedNodes.some((node) => node.id === activeNode.id)
      ? draggedNodes
      : [activeNode, ...draggedNodes];
    const draggedById = new Map(nodesToSnap.map((node) => [node.id, node]));
    const visibleCurrentNodes = currentNodes
      .filter((node) => visibleGraphNodeIds.has(node.id))
      .map((node) => draggedById.get(node.id) ?? node);
    return snapDraggedNodes(activeNode, nodesToSnap, visibleCurrentNodes);
  }

  function updateDraggedNodePositions(activeNode: Node<GraphNodeData>, draggedNodes: Node<GraphNodeData>[]) {
    setFlowNodes((currentNodes) => {
      const { snappedNodes, guides } = snapVisibleDraggedNodes(activeNode, draggedNodes, currentNodes);
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

  function fitWorkflowCanvas() {
    if (!flowInstance) return;
    void flowInstance.fitView({ padding: 0.18, duration: 180 });
  }

  function selectCompactCanvasMode(nextMode: CompactCanvasMode) {
    setCompactCanvasMode(nextMode);
    compactCanvasTouchModeRef.current = "idle";
    compactPanePanRef.current = null;
    if (nextMode !== "connect") {
      closeConnectionUi();
    }
  }

  function toggleCompactPanel(panel: Exclude<CompactActivePanel, null>) {
    setCompactActivePanel((current) => current === panel ? null : panel);
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

  function screenPointIsOverCanvas(point: ScreenPoint): boolean {
    const canvasRect = canvasShellRef.current?.getBoundingClientRect();
    if (!canvasRect) return false;
    return (
      point.clientX >= canvasRect.left &&
      point.clientX <= canvasRect.right &&
      point.clientY >= canvasRect.top &&
      point.clientY <= canvasRect.bottom
    );
  }

  function updateCanvasCursorPoint(clientX: number, clientY: number) {
    canvasCursorPointRef.current = { clientX, clientY };
  }

  function flowPositionAtCanvasCursor(): { x: number; y: number } | null {
    const cursorPoint = canvasCursorPointRef.current;
    if (!cursorPoint || !screenPointIsOverCanvas(cursorPoint)) return null;
    return flowPositionFromScreenPoint(cursorPoint.clientX, cursorPoint.clientY);
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

  function flowPositionAtCanvasCenter(): { x: number; y: number } | null {
    const canvasRect = canvasShellRef.current?.getBoundingClientRect();
    if (!canvasRect) return null;
    return flowPositionFromScreenPoint(
      canvasRect.left + canvasRect.width / 2,
      canvasRect.top + canvasRect.height / 2,
    );
  }

  function nodePositionAtCanvasCenter(): { x: number; y: number } | null {
    const flowPosition = flowPositionAtCanvasCenter();
    return flowPosition ? nodePositionCenteredOnFlowPoint(flowPosition) : null;
  }

  function nodeCenterCanvasPoint(node: Node<GraphNodeData>): CanvasPoint {
    const bounds = nodeBounds(node);
    return {
      x: (bounds.centerX * canvasViewport.zoom) + canvasViewport.x,
      y: (bounds.centerY * canvasViewport.zoom) + canvasViewport.y,
    };
  }

  function handleCanvasPointerEnter(event: ReactPointerEvent<HTMLElement>) {
    updateCanvasCursorPoint(event.clientX, event.clientY);
  }

  function handleCanvasPointerMove(event: ReactPointerEvent<HTMLElement>) {
    updateCanvasCursorPoint(event.clientX, event.clientY);
    if (!pendingConnection) return;
    updatePendingConnectionCursor(event.clientX, event.clientY);
  }

  function handleCanvasPointerLeave() {
    canvasCursorPointRef.current = null;
  }

  function compactPanePanTouch(event: ReactTouchEvent<HTMLElement>): ReactTouch | null {
    const panState = compactPanePanRef.current;
    if (!panState) return event.touches[0] ?? null;
    return Array.from(event.touches).find((touch) => touch.identifier === panState.touchId) ?? null;
  }

  function updateCompactPanePan(event: ReactTouchEvent<HTMLElement>) {
    const panState = compactPanePanRef.current;
    const touch = compactPanePanTouch(event);
    if (!panState || !touch || !flowInstance) return;
    const nextViewport = {
      ...panState.viewport,
      x: panState.viewport.x + (touch.clientX - panState.clientX),
      y: panState.viewport.y + (touch.clientY - panState.clientY),
    };
    flowInstance.setViewport(nextViewport);
    setCanvasViewport(nextViewport);
  }

  function handleCanvasTouchStartCapture(event: ReactTouchEvent<HTMLElement>) {
    if (!isCompactCanvasTouchMode) return;
    if (event.touches.length >= 2) {
      compactPanePanRef.current = null;
      compactCanvasTouchModeRef.current = "multi-touch-pan";
      return;
    }
    if (compactCanvasMode === "pan") {
      compactCanvasTouchModeRef.current = "pane-pan";
      const touch = event.touches[0];
      compactPanePanRef.current = touch
        ? {
            touchId: touch.identifier,
            clientX: touch.clientX,
            clientY: touch.clientY,
            viewport: flowInstance?.getViewport() ?? canvasViewport,
          }
        : null;
      event.stopPropagation();
      return;
    }
    if (!workflowMutationLocked && eventTargetInsideWorkflowNodeDragSurface(event.target)) {
      compactCanvasTouchModeRef.current = "node-drag";
      return;
    }
    compactPanePanRef.current = null;
    compactCanvasTouchModeRef.current = "pane-scroll";
    event.stopPropagation();
  }

  function handleCanvasTouchMoveCapture(event: ReactTouchEvent<HTMLElement>) {
    if (!isCompactCanvasTouchMode) return;
    if (event.touches.length >= 2) {
      compactPanePanRef.current = null;
      compactCanvasTouchModeRef.current = "multi-touch-pan";
      return;
    }
    if (compactCanvasTouchModeRef.current === "node-drag") return;
    if (compactCanvasTouchModeRef.current === "pane-pan") {
      updateCompactPanePan(event);
      event.stopPropagation();
      event.nativeEvent.stopImmediatePropagation();
      return;
    }
    if (compactCanvasTouchModeRef.current === "multi-touch-pan") {
      event.stopPropagation();
      event.nativeEvent.stopImmediatePropagation();
      return;
    }
    compactCanvasTouchModeRef.current = "pane-scroll";
    event.stopPropagation();
    event.nativeEvent.stopImmediatePropagation();
  }

  function handleCanvasTouchEndCapture(event: ReactTouchEvent<HTMLElement>) {
    if (!isCompactCanvasTouchMode) return;
    if (event.touches.length === 0) {
      compactPanePanRef.current = null;
      compactCanvasTouchModeRef.current = "idle";
      return;
    }
    if (compactCanvasTouchModeRef.current === "node-drag") return;
    if (compactCanvasTouchModeRef.current === "multi-touch-pan") {
      event.stopPropagation();
      event.nativeEvent.stopImmediatePropagation();
      return;
    }
    event.stopPropagation();
  }

  function handleCanvasTouchCancelCapture(event: ReactTouchEvent<HTMLElement>) {
    if (!isCompactCanvasTouchMode) return;
    if (event.touches.length === 0) {
      compactPanePanRef.current = null;
      compactCanvasTouchModeRef.current = "idle";
      return;
    }
    if (compactCanvasTouchModeRef.current === "node-drag") return;
    if (compactCanvasTouchModeRef.current === "multi-touch-pan") {
      event.stopPropagation();
      event.nativeEvent.stopImmediatePropagation();
      return;
    }
    event.stopPropagation();
  }

  function persistNodePositions(activeNode: Node<GraphNodeData>, draggedNodes: Node<GraphNodeData>[]) {
    if (!document || workflowMutationLocked) return;
    setSnapGuides([]);
    const currentNodes = flowInstance?.getNodes() as Node<GraphNodeData>[] | undefined;
    const { snappedNodes } = snapVisibleDraggedNodes(
      activeNode,
      draggedNodes,
      currentNodes && currentNodes.length > 0 ? currentNodes : flowNodes,
    );
    const nextLayout = cloneLayout(document.layout);
    for (const node of snappedNodes) {
      nextLayout.nodes[node.id] = node.position;
    }
    const snappedById = new Map(snappedNodes.map((node) => [node.id, node]));
    setFlowNodes((nodes) => nodes.map((node) => snappedById.get(node.id) ?? node));
    const nextDocument = {
      ...document,
      layout: nextLayout,
    };
    if (!workflowUndoDocumentsEqual(document, nextDocument)) {
      rememberWorkflowUndoSnapshot(document);
    }
    setDocument(nextDocument);
    setSaveState("idle");
  }

  return {
    nodeCenterCanvasPoint,
    canvasPointFromScreenPoint,
    nodePositionFromScreenPoint,
    flowPositionAtCanvasCursor,
    flowPositionAtCanvasCenter,
    nodePositionAtCanvasCenter,
    selectCompactCanvasMode,
    toggleCompactPanel,
    fitWorkflowCanvas,
    handleCanvasPointerEnter,
    handleCanvasPointerMove,
    handleCanvasPointerLeave,
    handleCanvasTouchStartCapture,
    handleCanvasTouchMoveCapture,
    handleCanvasTouchEndCapture,
    handleCanvasTouchCancelCapture,
    handleFlowInit,
    updateNodePositions,
    updateEdgeChanges,
    updateDraggedNodePositions,
    persistNodePositions,
  };
}
