import {
  type Edge,
  type Node,
  type ReactFlowInstance,
} from "@xyflow/react";
import {
  type Dispatch,
  type MutableRefObject,
  type SetStateAction,
} from "react";
import {
  type WorkflowConfig,
  type WorkflowEdge,
  type WorkflowEditorCatalog,
  type WorkflowEditorDocument,
  type WorkflowGraphMetadata,
  type WorkflowLayout,
  type WorkflowNodeConfig,
  type WorkflowNodeKind,
  type WorkflowOutputSchemas,
  type WorkflowValidationDiagnostic,
} from "../../types";
import { rewriteWorkflowNodeConfigBindings } from "../../workflowBindings";
import { workflowNodeBounds as nodeBounds } from "../../workflowNodeSnap";
import { type WorkflowTutorialRequiredAction } from "../../workflowTutorials";
import { isEditableEventTarget } from "./canvasModel";
import {
  type CompactActivePanel,
  type CopiedWorkflowNode,
  type CopiedWorkflowSelection,
  type GraphNodeData,
  type NodeConnectionMenuState,
  type PendingConnectionState,
} from "./editorTypes";
import { getCatalogEntry } from "./editorValues";
import {
  cloneConfig,
  cloneLayout,
  cloneNodeConfig,
  copiedWorkflowSelectionCenter,
  edgeId,
  fallbackPosition,
  generateNodeId,
  kindCollectionKey,
  nodeKindFromType,
  workflowNodes,
} from "./graphModel";
import { createDefaultNode } from "./nodeDefaults";

type SelectionActionsInputs = {
  setSelectedNodeIds: Dispatch<SetStateAction<string[]>>;
  setSelectedNodeId: Dispatch<SetStateAction<string | null>>;
  setFlowNodes: Dispatch<SetStateAction<Node<GraphNodeData>[]>>;
  document: WorkflowEditorDocument | null;
  selectedEdgeId: string | null;
  workflowMutationLocked: boolean;
  patchDocument: (nextConfig: WorkflowConfig, nextLayout: WorkflowLayout, nextGraphs?: WorkflowGraphMetadata[], selectedNodeIdsAfterPatch?: string[], diagnosticsOverride?: WorkflowValidationDiagnostic[], outputSchemasOverride?: WorkflowOutputSchemas, revisionOverride?: string, options?: { recordUndo?: boolean; }) => void;
  setSelectedEdgeId: Dispatch<SetStateAction<string | null>>;
  setError: Dispatch<SetStateAction<string | null>>;
  setNodeEditOrigins: Dispatch<SetStateAction<Record<string, string>>>;
  setInspectorDraftError: Dispatch<SetStateAction<string | null>>;
  selectedNodeIds: string[];
  flowInstance: ReactFlowInstance<Node<GraphNodeData>, Edge> | null;
  flowNodes: Node<GraphNodeData>[];
  copiedSelectionRef: MutableRefObject<CopiedWorkflowSelection | null>;
  selectedNodeId: string | null;
  selectedWorkflowPackReference: string;
  flowPositionAtCanvasCursor: () => { x: number; y: number; } | null;
  flowPositionAtCanvasCenter: () => { x: number; y: number; } | null;
  isTutorialMode: boolean;
  exitWorkflowTutorial: (options?: { markCompleted?: boolean; }) => void;
  isInspectMode: boolean;
  exitInspectMode: () => void;
  pendingConnection: PendingConnectionState | null;
  nodeConnectionMenu: NodeConnectionMenuState | null;
  closeConnectionUi: () => void;
  compactActivePanel: CompactActivePanel;
  setCompactActivePanel: Dispatch<SetStateAction<CompactActivePanel>>;
  canUndoWorkflowChanges: boolean;
  undoWorkflowChange: () => void;
  activeWorkflowDocument: WorkflowEditorDocument | null;
  tutorialCurrentRequiredAction: () => WorkflowTutorialRequiredAction | null;
  setWorkflowTutorialHint: Dispatch<SetStateAction<string | null>>;
  tutorialActionHintFor: (requiredAction: WorkflowTutorialRequiredAction | null) => string;
  activeWorkflowCatalog: WorkflowEditorCatalog;
  nodePositionAtCanvasCenter: () => { x: number; y: number; } | null;
  patchWorkflowDocument: (nextConfig: WorkflowConfig, nextLayout: WorkflowLayout, nextGraphs?: WorkflowGraphMetadata[], selectedNodeIdsAfterPatch?: string[], diagnosticsOverride?: WorkflowValidationDiagnostic[], outputSchemasOverride?: WorkflowOutputSchemas, revisionOverride?: string) => void;
  completeTutorialAction: (action: WorkflowTutorialRequiredAction) => boolean;
  workflowEditLocked: boolean;
  setSelectedGraphId: Dispatch<SetStateAction<string | null>>;
  selectedGraph: WorkflowGraphMetadata | undefined;
  graphNameDraft: string;
  graphDirectoryDraft: string;
  graphUsersDraft: string;
  graphDescriptionDraft: string;
};

/** Binds selection actions to the current render state. */
export function createSelectionActions({
  setSelectedNodeIds,
  setSelectedNodeId,
  setFlowNodes,
  document,
  selectedEdgeId,
  workflowMutationLocked,
  patchDocument,
  setSelectedEdgeId,
  setError,
  setNodeEditOrigins,
  setInspectorDraftError,
  selectedNodeIds,
  flowInstance,
  flowNodes,
  copiedSelectionRef,
  selectedNodeId,
  selectedWorkflowPackReference,
  flowPositionAtCanvasCursor,
  flowPositionAtCanvasCenter,
  isTutorialMode,
  exitWorkflowTutorial,
  isInspectMode,
  exitInspectMode,
  pendingConnection,
  nodeConnectionMenu,
  closeConnectionUi,
  compactActivePanel,
  setCompactActivePanel,
  canUndoWorkflowChanges,
  undoWorkflowChange,
  activeWorkflowDocument,
  tutorialCurrentRequiredAction,
  setWorkflowTutorialHint,
  tutorialActionHintFor,
  activeWorkflowCatalog,
  nodePositionAtCanvasCenter,
  patchWorkflowDocument,
  completeTutorialAction,
  workflowEditLocked,
  setSelectedGraphId,
  selectedGraph,
  graphNameDraft,
  graphDirectoryDraft,
  graphUsersDraft,
  graphDescriptionDraft,
}: SelectionActionsInputs) {
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

  function deleteSelectedEdge() {
    if (!document || !selectedEdgeId || workflowMutationLocked) return;
    const nextConfig = cloneConfig(document.config);
    nextConfig.edges = nextConfig.edges.filter(
      (edge) => edgeId(edge) !== selectedEdgeId,
    );
    patchDocument(nextConfig, cloneLayout(document.layout));
    setSelectedEdgeId(null);
    setError(null);
  }

  function deleteNodes(nodeIds: string[]) {
    if (!document || nodeIds.length === 0 || workflowMutationLocked) return;
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
    setNodeEditOrigins((current) => {
      const next = { ...current };
      for (const nodeId of deletedNodeIds) {
        delete next[nodeId];
      }
      return next;
    });
    setInspectorDraftError(null);
    setError(null);
  }

  function copySelectedNodes() {
    if (!document || selectedNodeIds.length === 0) return false;
    const selectedNodeIdSet = new Set(selectedNodeIds);
    const nodesById = new Map(workflowNodes(document.config).map((node) => [node.id, node]));
    const currentFlowNodes = flowInstance?.getNodes() as Node<GraphNodeData>[] | undefined;
    const flowNodesById = new Map(
      (currentFlowNodes && currentFlowNodes.length > 0 ? currentFlowNodes : flowNodes).map((node) => [node.id, node]),
    );
    const copiedNodes = selectedNodeIds
      .map((nodeId, index) => {
        const node = nodesById.get(nodeId);
        if (!node) return null;
        const flowNode = flowNodesById.get(nodeId);
        const position = flowNode?.position ?? document.layout.nodes[nodeId] ?? fallbackPosition(index);
        const bounds = nodeBounds(flowNode ? { ...flowNode, position } : { id: node.id, position });
        return {
          node: cloneNodeConfig(node),
          position,
          width: bounds.right - bounds.left,
          height: bounds.bottom - bounds.top,
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
    if (!document || !copiedSelection || copiedSelection.nodes.length === 0 || workflowMutationLocked) return false;
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
      const nextId = generateNodeId(
        nextConfig,
        templateKind,
        templateNode.type,
        templateNode.display_name,
        selectedWorkflowPackReference,
      );
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
    const selectionCenter = copiedWorkflowSelectionCenter(copiedSelection.nodes);
    const pasteTarget = flowPositionAtCanvasCursor() ?? flowPositionAtCanvasCenter();
    const pasteDelta = selectionCenter && pasteTarget
      ? {
          x: pasteTarget.x - selectionCenter.x,
          y: pasteTarget.y - selectionCenter.y,
        }
      : { x: 40, y: 40 };
    for (const copiedNode of copiedSelection.nodes) {
      const nextId = idMap.get(copiedNode.node.id);
      if (!nextId) continue;
      nextLayout.nodes[nextId] = {
        x: copiedNode.position.x + pasteDelta.x,
        y: copiedNode.position.y + pasteDelta.y,
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
    if (isTutorialMode) {
      if (event.key === "Escape") {
        event.preventDefault();
        exitWorkflowTutorial();
      }
      return;
    }
    if (isInspectMode) {
      if (event.key === "Escape") {
        event.preventDefault();
        exitInspectMode();
      }
      return;
    }
    if (event.key === "Escape" && (pendingConnection || nodeConnectionMenu)) {
      event.preventDefault();
      closeConnectionUi();
      return;
    }
    if (event.key === "Escape" && compactActivePanel) {
      event.preventDefault();
      setCompactActivePanel(null);
      return;
    }
    const isCopy = (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "c";
    const isPaste = (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "v";
    const isUndo = (event.ctrlKey || event.metaKey) && !event.shiftKey && event.key.toLowerCase() === "z";
    if (isUndo) {
      if (canUndoWorkflowChanges) {
        event.preventDefault();
        undoWorkflowChange();
      }
      return;
    }
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

  function addNode(kind: WorkflowNodeKind, type: string, requestedPosition?: { x: number; y: number } | null) {
    const workflowDocument = activeWorkflowDocument;
    if (!workflowDocument) return;
    const expectedAction = tutorialCurrentRequiredAction();
    const isTutorialAddNode =
      isTutorialMode &&
      expectedAction?.kind === "add-node" &&
      expectedAction.nodeKind === kind &&
      expectedAction.nodeType === type;
    const tutorialAddNodeAction = isTutorialAddNode ? expectedAction : null;
    if (workflowMutationLocked && !isTutorialAddNode) return;
    if (isTutorialMode && expectedAction?.kind === "add-node" && !isTutorialAddNode) {
      setWorkflowTutorialHint(tutorialActionHintFor(expectedAction));
      return;
    }
    const nextConfig = cloneConfig(workflowDocument.config);
    nextConfig.triggers ??= [];
    nextConfig.gates ??= [];
    nextConfig.effects ??= [];
    nextConfig.queries ??= [];
    const catalogEntry = getCatalogEntry(activeWorkflowCatalog, type);
    const nodeId = tutorialAddNodeAction
      ? tutorialAddNodeAction.nodeId
      : generateNodeId(nextConfig, kind, type, catalogEntry?.label, selectedWorkflowPackReference);
    const node = createDefaultNode(kind, type, nodeId);
    nextConfig[kindCollectionKey(kind)].push(node);
    const nextLayout = cloneLayout(workflowDocument.layout);
    const nextNodeIndex =
      nextConfig.triggers.length +
        nextConfig.gates.length +
        nextConfig.effects.length +
        nextConfig.queries.length -
        1;
    nextLayout.nodes[node.id] =
      (tutorialAddNodeAction ? tutorialAddNodeAction.position : requestedPosition) ?? nodePositionAtCanvasCenter() ?? fallbackPosition(nextNodeIndex);
    patchWorkflowDocument(nextConfig, nextLayout, undefined, [node.id]);
    setProgrammaticSelection([node.id], node.id);
    setSelectedEdgeId(null);
    setInspectorDraftError(null);
    if (tutorialAddNodeAction) {
      completeTutorialAction({
        kind: "add-node",
        nodeKind: tutorialAddNodeAction.nodeKind,
        nodeType: tutorialAddNodeAction.nodeType,
        nodeId: tutorialAddNodeAction.nodeId,
        position: tutorialAddNodeAction.position,
      });
    }
    setError(null);
  }

  function deleteSelectedNode() {
    deleteNodes(selectedNodeIds);
  }

  function selectDownstreamBranch() {
    if (!document || !selectedNodeId || workflowEditLocked) return;
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
    if (workflowEditLocked) return;
    setSelectedGraphId(graph.id);
    setProgrammaticSelection(graph.node_ids, graph.node_ids[0] ?? null);
    setSelectedEdgeId(null);
    setError(null);
  }

  function applyWorkflowGraphChanges() {
    if (!document || !selectedGraph || workflowMutationLocked) return;
    const nextName = graphNameDraft.trim();
    if (!nextName) {
      setError("Graph name is required.");
      return;
    }
    const nextDirectoryPath = graphDirectoryDraft.trim();
    if (!nextDirectoryPath) {
      setError("Graph directory is required.");
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
            directory_path: nextDirectoryPath,
            user_ids: nextUserIds,
          }
        : graph,
    );
    patchDocument(document.config, document.layout, nextGraphs);
    setError(null);
  }

  return {
    setProgrammaticSelection,
    addNode,
    deleteNodes,
    selectWorkflowGraph,
    applyWorkflowGraphChanges,
    selectDownstreamBranch,
    deleteSelectedNode,
    deleteSelectedEdge,
    handleAppShellKeyDown,
  };
}
