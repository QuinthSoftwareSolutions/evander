import {
  type Edge,
  type Node,
  type ReactFlowInstance,
  type Viewport,
} from "@xyflow/react";
import {
  type Dispatch,
  type MutableRefObject,
  type SetStateAction,
} from "react";
import {
  type WorkflowEditorCatalog,
  type WorkflowEditorDocument,
  type WorkflowNodeConfig,
  type WorkflowNodeKind,
  type WorkflowRunDetail,
  type WorkflowRunSnapshot,
  type WorkflowValidationDiagnostic,
} from "../../types";
import { type SnapGuide } from "../../workflowNodeSnap";
import {
  createWorkflowTutorialSession,
  workflowTutorialDefinitionById,
  type WorkflowTutorialRequiredAction,
  workflowTutorialRequiredActionMatches,
  type WorkflowTutorialSession,
} from "../../workflowTutorials";
import {
  buildEdges,
  buildNodes,
  currentPositionsFromNodes,
} from "./canvasModel";
import { fetchJson } from "./editorApi";
import { WORKFLOW_TUTORIAL_PROGRESS_API_PATH } from "./editorConstants";
import {
  type GraphNodeData,
  type NodeConnectionMenuState,
  type PendingConnectionState,
  type TutorialEditorSnapshot,
  type WorkflowTutorialChooserStatus,
  type WorkflowTutorialProgress,
} from "./editorTypes";
import { dirtyWorkflowNodeIds, workflowNodeIds } from "./graphModel";
import {
  isMeaningfulViewport,
  normalizeWorkflowTutorialProgress,
  tutorialRequiredActionDescription,
  workflowTutorialProgressApiPayload,
} from "./tutorialModel";

type TutorialActionsInputs = {
  activeWorkflowTutorialStep: import("../../workflowTutorials").WorkflowTutorialStep | null;
  isTutorialMode: boolean;
  selectedNode: WorkflowNodeConfig | undefined;
  setWorkflowTutorialProgress: Dispatch<SetStateAction<WorkflowTutorialProgress>>;
  setError: Dispatch<SetStateAction<string | null>>;
  workflowTutorialProgress: WorkflowTutorialProgress;
  setWorkflowTutorialHint: Dispatch<SetStateAction<string | null>>;
  setWorkflowTutorialChooserOpen: Dispatch<SetStateAction<boolean>>;
  activeWorkflowTutorial: WorkflowTutorialSession | null;
  tutorialSnapshotRef: MutableRefObject<TutorialEditorSnapshot | null>;
  selectedNodeIds: string[];
  selectedNodeId: string | null;
  selectedEdgeId: string | null;
  selectedGraphId: string | null;
  runDetail: WorkflowRunDetail | null;
  canvasViewport: Viewport;
  setActiveWorkflowTutorial: Dispatch<SetStateAction<WorkflowTutorialSession | null>>;
  setRunDetail: Dispatch<SetStateAction<WorkflowRunDetail | null>>;
  setProgrammaticSelection: (nextNodeIds: string[], preferredNodeId?: string | null) => void;
  setSelectedEdgeId: Dispatch<SetStateAction<string | null>>;
  setSelectedGraphId: Dispatch<SetStateAction<string | null>>;
  setNodeConnectionMenu: Dispatch<SetStateAction<NodeConnectionMenuState | null>>;
  setPendingConnection: Dispatch<SetStateAction<PendingConnectionState | null>>;
  setSnapGuides: Dispatch<SetStateAction<SnapGuide[]>>;
  hasAutoFitRef: MutableRefObject<boolean>;
  workflowTutorialProgressSets: { completed: Set<string>; skipped: Set<string>; };
  document: WorkflowEditorDocument | null;
  savedDocument: WorkflowEditorDocument | null;
  nodeEditOrigins: Record<string, string>;
  setFlowNodes: Dispatch<SetStateAction<Node<GraphNodeData>[]>>;
  catalog: WorkflowEditorCatalog;
  workflowGeneratorOpen: boolean;
  runSnapshot: WorkflowRunSnapshot | null;
  validationDiagnostics: WorkflowValidationDiagnostic[];
  runtimePulseNodeIds: Set<string>;
  setFlowEdges: Dispatch<SetStateAction<Edge[]>>;
  setCanvasViewport: Dispatch<SetStateAction<Viewport>>;
  flowInstance: ReactFlowInstance<Node<GraphNodeData>, Edge> | null;
};

/** Binds tutorial actions to the current render state. */
export function createTutorialActions({
  activeWorkflowTutorialStep,
  isTutorialMode,
  selectedNode,
  setWorkflowTutorialProgress,
  setError,
  workflowTutorialProgress,
  setWorkflowTutorialHint,
  setWorkflowTutorialChooserOpen,
  activeWorkflowTutorial,
  tutorialSnapshotRef,
  selectedNodeIds,
  selectedNodeId,
  selectedEdgeId,
  selectedGraphId,
  runDetail,
  canvasViewport,
  setActiveWorkflowTutorial,
  setRunDetail,
  setProgrammaticSelection,
  setSelectedEdgeId,
  setSelectedGraphId,
  setNodeConnectionMenu,
  setPendingConnection,
  setSnapGuides,
  hasAutoFitRef,
  workflowTutorialProgressSets,
  document,
  savedDocument,
  nodeEditOrigins,
  setFlowNodes,
  catalog,
  workflowGeneratorOpen,
  runSnapshot,
  validationDiagnostics,
  runtimePulseNodeIds,
  setFlowEdges,
  setCanvasViewport,
  flowInstance,
}: TutorialActionsInputs) {
  function tutorialCurrentRequiredAction(): WorkflowTutorialRequiredAction | null {
    return activeWorkflowTutorialStep?.requiredAction ?? null;
  }

  function tutorialActionMatchesCurrentStep(action: WorkflowTutorialRequiredAction): boolean {
    const requiredAction = tutorialCurrentRequiredAction();
    return requiredAction ? workflowTutorialRequiredActionMatches(requiredAction, action) : false;
  }

  function tutorialActionHintFor(requiredAction: WorkflowTutorialRequiredAction | null): string {
    if (!requiredAction) return "";
    return `Follow this step: ${tutorialRequiredActionDescription(requiredAction)}`;
  }

  function tutorialAllowsAction(action: WorkflowTutorialRequiredAction): boolean {
    if (!isTutorialMode) return true;
    return tutorialActionMatchesCurrentStep(action);
  }

  function tutorialAllowsNodeType(kind: WorkflowNodeKind, type: string): boolean {
    const requiredAction = tutorialCurrentRequiredAction();
    return Boolean(
      isTutorialMode &&
      requiredAction?.kind === "add-node" &&
      requiredAction.nodeKind === kind &&
      requiredAction.nodeType === type,
    );
  }

  function tutorialAllowsInspectorFieldEdit(fieldKey: string): boolean {
    const requiredAction = tutorialCurrentRequiredAction();
    return Boolean(
      isTutorialMode &&
      requiredAction?.kind === "bind-field" &&
      selectedNode?.id === requiredAction.nodeId &&
      requiredAction.fieldKey === fieldKey,
    );
  }

  async function saveWorkflowTutorialProgress(nextProgress: WorkflowTutorialProgress) {
    try {
      const payload = await fetchJson<unknown>(WORKFLOW_TUTORIAL_PROGRESS_API_PATH, {
        method: "PUT",
        body: JSON.stringify(workflowTutorialProgressApiPayload(nextProgress)),
      });
      setWorkflowTutorialProgress(normalizeWorkflowTutorialProgress(payload));
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  function workflowTutorialProgressWithStatus(
    current: WorkflowTutorialProgress,
    tutorialId: string,
    status: "completed" | "skipped" | null,
  ): WorkflowTutorialProgress {
    const completedTutorialIds = current.completedTutorialIds.filter((id) => id !== tutorialId);
    const skippedTutorialIds = current.skippedTutorialIds.filter((id) => id !== tutorialId);
    if (status === "completed") {
      completedTutorialIds.push(tutorialId);
    } else if (status === "skipped") {
      skippedTutorialIds.push(tutorialId);
    }
    return { completedTutorialIds, skippedTutorialIds };
  }

  function updateWorkflowTutorialProgressStatus(
    tutorialId: string,
    status: "completed" | "skipped" | null,
  ) {
    const nextProgress = workflowTutorialProgressWithStatus(workflowTutorialProgress, tutorialId, status);
    setWorkflowTutorialProgress(nextProgress);
    void saveWorkflowTutorialProgress(nextProgress);
  }

  function clearWorkflowTutorialProgressStatus(tutorialId: string) {
    updateWorkflowTutorialProgressStatus(tutorialId, null);
  }

  function completeTutorialAction(action: WorkflowTutorialRequiredAction): boolean {
    if (!isTutorialMode) return true;
    const requiredAction = tutorialCurrentRequiredAction();
    if (!requiredAction || !workflowTutorialRequiredActionMatches(requiredAction, action)) {
      setWorkflowTutorialHint(tutorialActionHintFor(requiredAction));
      return false;
    }
    setWorkflowTutorialHint(null);
    if (requiredAction.kind === "reset") {
      resetWorkflowTutorial();
      return true;
    }
    advanceWorkflowTutorial();
    return true;
  }

  function startWorkflowTutorial(tutorialId = "first-steps") {
    setWorkflowTutorialChooserOpen(false);
    if (activeWorkflowTutorial) return;
    const definition = workflowTutorialDefinitionById(tutorialId);
    if (!definition) return;
    tutorialSnapshotRef.current = {
      selectedNodeIds,
      selectedNodeId,
      selectedEdgeId,
      selectedGraphId,
      runDetail,
      canvasViewport,
      viewportWasMeaningful: isMeaningfulViewport(canvasViewport),
    };
    setWorkflowTutorialHint(null);
    setActiveWorkflowTutorial(createWorkflowTutorialSession(definition.id));
    setRunDetail(null);
    setProgrammaticSelection([]);
    setSelectedEdgeId(null);
    setSelectedGraphId(null);
    setNodeConnectionMenu(null);
    setPendingConnection(null);
    setSnapGuides([]);
    hasAutoFitRef.current = false;
    setError(null);
  }

  function restartWorkflowTutorial(tutorialId = "first-steps") {
    const definition = workflowTutorialDefinitionById(tutorialId);
    if (!definition) return;
    setWorkflowTutorialChooserOpen(false);
    if (activeWorkflowTutorial?.tutorialId === definition.id) {
      resetWorkflowTutorial();
      return;
    }
    startWorkflowTutorial(definition.id);
  }

  function skipWorkflowTutorial(tutorialId = "first-steps") {
    const definition = workflowTutorialDefinitionById(tutorialId);
    if (!definition) return;
    updateWorkflowTutorialProgressStatus(definition.id, "skipped");
    setWorkflowTutorialChooserOpen(false);
    if (activeWorkflowTutorial?.tutorialId === definition.id) {
      exitWorkflowTutorial({ markCompleted: false });
    }
  }

  function workflowTutorialChooserStatusFor(tutorialId: string): WorkflowTutorialChooserStatus {
    if (activeWorkflowTutorial?.tutorialId === tutorialId) return "in-progress";
    if (workflowTutorialProgressSets.completed.has(tutorialId)) return "completed";
    if (workflowTutorialProgressSets.skipped.has(tutorialId)) return "skipped";
    return "available";
  }

  function workflowTutorialChooserStatusLabel(status: WorkflowTutorialChooserStatus): string {
    switch (status) {
      case "completed":
        return "Completed";
      case "skipped":
        return "Skipped";
      case "in-progress":
        return "In progress";
      case "available":
      default:
        return "Ready";
    }
  }

  function workflowTutorialIsAtFinalStep(tutorial: WorkflowTutorialSession | null): boolean {
    if (!tutorial) return false;
    const definition = workflowTutorialDefinitionById(tutorial.tutorialId);
    if (!definition) return false;
    return tutorial.stepIndex >= definition.steps.length - 1;
  }

  function markActiveWorkflowTutorialCompletedIfFinished() {
    if (!activeWorkflowTutorial || !workflowTutorialIsAtFinalStep(activeWorkflowTutorial)) return;
    updateWorkflowTutorialProgressStatus(activeWorkflowTutorial.tutorialId, "completed");
  }

  function advanceWorkflowTutorial() {
    const currentTutorial = activeWorkflowTutorial;
    const currentDefinition = currentTutorial ? workflowTutorialDefinitionById(currentTutorial.tutorialId) : null;
    const currentNextStepIndex = currentTutorial && currentDefinition
      ? Math.min(currentTutorial.stepIndex + 1, currentDefinition.steps.length - 1)
      : null;
    const reachedFinalStep = Boolean(
      currentTutorial &&
      currentDefinition &&
      currentNextStepIndex !== null &&
      currentNextStepIndex !== currentTutorial.stepIndex &&
      currentNextStepIndex === currentDefinition.steps.length - 1,
    );
    setActiveWorkflowTutorial((current) => {
      if (!current) return current;
      const definition = workflowTutorialDefinitionById(current.tutorialId);
      if (!definition) return current;
      const nextStepIndex = Math.min(current.stepIndex + 1, definition.steps.length - 1);
      if (nextStepIndex === current.stepIndex) return current;
      hasAutoFitRef.current = false;
      setWorkflowTutorialHint(null);
      return {
        ...current,
        stepIndex: nextStepIndex,
      };
    });
    if (reachedFinalStep && currentTutorial) {
      updateWorkflowTutorialProgressStatus(currentTutorial.tutorialId, "completed");
    }
  }

  function resetWorkflowTutorial() {
    markActiveWorkflowTutorialCompletedIfFinished();
    setActiveWorkflowTutorial((current) => {
      if (!current) return current;
      const definition = workflowTutorialDefinitionById(current.tutorialId);
      if (!definition) return current;
      hasAutoFitRef.current = false;
      setWorkflowTutorialHint(null);
      return createWorkflowTutorialSession(definition.id);
    });
    setProgrammaticSelection([]);
    setSelectedEdgeId(null);
    setSelectedGraphId(null);
    setNodeConnectionMenu(null);
    setPendingConnection(null);
    setSnapGuides([]);
  }

  function exitWorkflowTutorial(options: { markCompleted?: boolean } = {}) {
    if (options.markCompleted !== false) {
      markActiveWorkflowTutorialCompletedIfFinished();
    }
    const snapshot = tutorialSnapshotRef.current;
    setActiveWorkflowTutorial(null);
    setWorkflowTutorialHint(null);
    setNodeConnectionMenu(null);
    setPendingConnection(null);
    setSnapGuides([]);
    if (!snapshot) {
      return;
    }
    if (document) {
      const validNodeIds = new Set(workflowNodeIds(document.config));
      const restoredSelectedNodeIds = snapshot.selectedNodeIds.filter((nodeId) => validNodeIds.has(nodeId));
      const restoredDirtyNodeIds = dirtyWorkflowNodeIds(document, savedDocument, nodeEditOrigins);
      setFlowNodes((currentNodes) =>
        buildNodes(
          document.config,
          document.layout,
          catalog,
          workflowGeneratorOpen ? {} : runSnapshot?.node_activity ?? {},
          validationDiagnostics,
          currentPositionsFromNodes(currentNodes),
          restoredSelectedNodeIds,
          new Set(restoredDirtyNodeIds),
          new Set<string>(),
          workflowGeneratorOpen ? new Set<string>() : runtimePulseNodeIds,
        ),
      );
      setFlowEdges(buildEdges(document.config, catalog));
    }
    setProgrammaticSelection(snapshot.selectedNodeIds, snapshot.selectedNodeId);
    setSelectedEdgeId(snapshot.selectedEdgeId);
    setSelectedGraphId(snapshot.selectedGraphId);
    setRunDetail(snapshot.runDetail);
    setCanvasViewport(snapshot.canvasViewport);
    if (flowInstance) {
      void flowInstance.setViewport(snapshot.canvasViewport);
    }
    hasAutoFitRef.current = snapshot.viewportWasMeaningful;
  }

  return {
    tutorialCurrentRequiredAction,
    tutorialActionHintFor,
    tutorialAllowsAction,
    completeTutorialAction,
    exitWorkflowTutorial,
    tutorialAllowsInspectorFieldEdit,
    workflowTutorialChooserStatusFor,
    startWorkflowTutorial,
    restartWorkflowTutorial,
    skipWorkflowTutorial,
    clearWorkflowTutorialProgressStatus,
    workflowTutorialChooserStatusLabel,
    tutorialAllowsNodeType,
    advanceWorkflowTutorial,
    resetWorkflowTutorial,
  };
}
