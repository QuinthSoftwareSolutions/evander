import {
  type Dispatch,
  type MutableRefObject,
  type SetStateAction,
} from "react";
import {
  type WorkflowEditorDocument,
  type WorkflowEditorGeneratedDraft,
  type WorkflowRunDetail,
  type WorkflowValidationDiagnostic,
} from "../../types";
import { fetchJson } from "./editorApi";
import { WORKFLOW_GENERATOR_MIN_DESCRIPTION_LENGTH } from "./editorConstants";
import {
  type NodeConnectionMenuState,
  type PendingConnectionState,
  type WorkflowGeneratorState,
} from "./editorTypes";
import {
  blankWorkflowGeneratorDocument,
  mergeGeneratedDraftIntoWorkflow,
  normalizeWorkflowEditorDocument,
  workflowNodeIds,
} from "./graphModel";

type GeneratorActionsInputs = {
  workflowEditLocked: boolean;
  workflowGeneratorOpen: boolean;
  hasUnsavedWorkflowChanges: boolean;
  workflowGeneratorAbortRef: MutableRefObject<AbortController | null>;
  workflowGeneratorRequestIdRef: MutableRefObject<number>;
  setWorkflowGeneratorOpen: Dispatch<SetStateAction<boolean>>;
  setWorkflowGeneratorDescription: Dispatch<SetStateAction<string>>;
  setWorkflowGeneratorState: Dispatch<SetStateAction<WorkflowGeneratorState>>;
  setWorkflowGeneratorError: Dispatch<SetStateAction<string | null>>;
  setWorkflowGeneratorWarnings: Dispatch<SetStateAction<string[]>>;
  setWorkflowGeneratorModel: Dispatch<SetStateAction<string | null>>;
  setWorkflowGeneratorReadyDescription: Dispatch<SetStateAction<string | null>>;
  setRunDetail: Dispatch<SetStateAction<WorkflowRunDetail | null>>;
  setNodeConnectionMenu: Dispatch<SetStateAction<NodeConnectionMenuState | null>>;
  setPendingConnection: Dispatch<SetStateAction<PendingConnectionState | null>>;
  setSelectedEdgeId: Dispatch<SetStateAction<string | null>>;
  setSelectedGraphId: Dispatch<SetStateAction<string | null>>;
  setNodeEditOrigins: Dispatch<SetStateAction<Record<string, string>>>;
  applyWorkflowDocumentState: (nextDocument: WorkflowEditorDocument, selectedNodeIdsAfterPatch?: string[], diagnosticsOverride?: WorkflowValidationDiagnostic[], options?: { autoFit?: boolean; markClean?: boolean; nodeEditOrigins?: Record<string, string>; }) => void;
  savedDocument: WorkflowEditorDocument | null;
  selectedDirectoryPath: string;
  document: WorkflowEditorDocument | null;
  workflowGeneratorState: WorkflowGeneratorState;
  workflowGeneratorReadyDescription: string | null;
  workflowGeneratorDescription: string;
};

/** Binds generator actions to the current render state. */
export function createGeneratorActions({
  workflowEditLocked,
  workflowGeneratorOpen,
  hasUnsavedWorkflowChanges,
  workflowGeneratorAbortRef,
  workflowGeneratorRequestIdRef,
  setWorkflowGeneratorOpen,
  setWorkflowGeneratorDescription,
  setWorkflowGeneratorState,
  setWorkflowGeneratorError,
  setWorkflowGeneratorWarnings,
  setWorkflowGeneratorModel,
  setWorkflowGeneratorReadyDescription,
  setRunDetail,
  setNodeConnectionMenu,
  setPendingConnection,
  setSelectedEdgeId,
  setSelectedGraphId,
  setNodeEditOrigins,
  applyWorkflowDocumentState,
  savedDocument,
  selectedDirectoryPath,
  document,
  workflowGeneratorState,
  workflowGeneratorReadyDescription,
  workflowGeneratorDescription,
}: GeneratorActionsInputs) {
  function openWorkflowGenerator() {
    if (workflowEditLocked) return;
    if (workflowGeneratorOpen) return;
    if (hasUnsavedWorkflowChanges) {
      const confirmed = window.confirm(
        "Opening the workflow generator will discard unsaved workflow edits. Continue?",
      );
      if (!confirmed) return;
    }
    workflowGeneratorAbortRef.current?.abort();
    workflowGeneratorRequestIdRef.current += 1;
    setWorkflowGeneratorOpen(true);
    setWorkflowGeneratorDescription("");
    setWorkflowGeneratorState("idle");
    setWorkflowGeneratorError(null);
    setWorkflowGeneratorWarnings([]);
    setWorkflowGeneratorModel(null);
    setWorkflowGeneratorReadyDescription(null);
    setRunDetail(null);
    setNodeConnectionMenu(null);
    setPendingConnection(null);
    setSelectedEdgeId(null);
    setSelectedGraphId(null);
    setNodeEditOrigins({});
    applyWorkflowDocumentState(
      blankWorkflowGeneratorDocument(savedDocument, selectedDirectoryPath),
      [],
      [],
      { autoFit: true },
    );
  }

  function cancelWorkflowGenerator() {
    workflowGeneratorAbortRef.current?.abort();
    workflowGeneratorRequestIdRef.current += 1;
    setWorkflowGeneratorOpen(false);
    setWorkflowGeneratorDescription("");
    setWorkflowGeneratorState("idle");
    setWorkflowGeneratorError(null);
    setWorkflowGeneratorWarnings([]);
    setWorkflowGeneratorModel(null);
    setWorkflowGeneratorReadyDescription(null);
    setSelectedEdgeId(null);
    setSelectedGraphId(null);
    setNodeEditOrigins({});
    if (savedDocument) {
      applyWorkflowDocumentState(
        savedDocument,
        [],
        [],
        { autoFit: true },
      );
    }
  }

  function acceptWorkflowGeneratorDraft() {
    if (workflowEditLocked || !workflowGeneratorOpen || !document || !savedDocument) return;
    if (workflowGeneratorState !== "ready") {
      setWorkflowGeneratorError("Generate a ready draft before accepting it.");
      return;
    }
    if (workflowGeneratorReadyDescription !== workflowGeneratorDescription.trim()) {
      setWorkflowGeneratorError("Generate a ready draft for the current description before accepting it.");
      return;
    }
    const generatedNodeIds = workflowNodeIds(document.config);
    if (generatedNodeIds.length === 0) {
      setWorkflowGeneratorError("Generate a workflow before accepting the draft.");
      return;
    }
    const merged = mergeGeneratedDraftIntoWorkflow(savedDocument, document, selectedDirectoryPath);
    workflowGeneratorAbortRef.current?.abort();
    workflowGeneratorRequestIdRef.current += 1;
    setWorkflowGeneratorOpen(false);
    setWorkflowGeneratorDescription("");
    setWorkflowGeneratorState("idle");
    setWorkflowGeneratorError(null);
    setWorkflowGeneratorWarnings([]);
    setWorkflowGeneratorModel(null);
    setWorkflowGeneratorReadyDescription(null);
    setNodeEditOrigins({});
    applyWorkflowDocumentState(merged.document, merged.importedNodeIds, undefined, { autoFit: true });
    const selectedGeneratedGraph = merged.document.graphs.find((graph) =>
      graph.node_ids.some((nodeId) => merged.importedNodeIds.includes(nodeId)),
    );
    setSelectedGraphId(selectedGeneratedGraph?.id ?? null);
  }

  function resetWorkflowGeneratorPreview(nextDescription: string) {
    workflowGeneratorAbortRef.current?.abort();
    workflowGeneratorRequestIdRef.current += 1;
    setWorkflowGeneratorState(nextDescription.trim() ? "draftable" : "idle");
    setWorkflowGeneratorError(null);
    setWorkflowGeneratorWarnings([]);
    setWorkflowGeneratorModel(null);
    setWorkflowGeneratorReadyDescription(null);
    applyWorkflowDocumentState(
      blankWorkflowGeneratorDocument(savedDocument, selectedDirectoryPath),
      [],
      [],
      { autoFit: true },
    );
  }

  function handleWorkflowGeneratorDescriptionChange(value: string) {
    setWorkflowGeneratorDescription(value);
    resetWorkflowGeneratorPreview(value);
  }

  async function generateWorkflowGeneratorDraft() {
    if (workflowEditLocked || !workflowGeneratorOpen) return;
    const description = workflowGeneratorDescription.trim();
    workflowGeneratorAbortRef.current?.abort();
    workflowGeneratorRequestIdRef.current += 1;
    setWorkflowGeneratorReadyDescription(null);
    if (!description) {
      resetWorkflowGeneratorPreview(description);
      return;
    }
    if (description.length < WORKFLOW_GENERATOR_MIN_DESCRIPTION_LENGTH) {
      setWorkflowGeneratorState("draftable");
      setWorkflowGeneratorError(
        `Describe the workflow in at least ${WORKFLOW_GENERATOR_MIN_DESCRIPTION_LENGTH} characters before generating.`,
      );
      return;
    }

    const requestId = workflowGeneratorRequestIdRef.current;
    const controller = new AbortController();
    workflowGeneratorAbortRef.current = controller;
    setWorkflowGeneratorState("generating");
    setWorkflowGeneratorError(null);
    setWorkflowGeneratorWarnings([]);
    setWorkflowGeneratorModel(null);
    try {
      const draft = await fetchJson<WorkflowEditorGeneratedDraft>("/api/workflow-editor/generate-draft", {
        method: "POST",
        signal: controller.signal,
        body: JSON.stringify({
          description,
          directory_path: selectedDirectoryPath,
          include_current_packs: true,
        }),
      });
      if (requestId !== workflowGeneratorRequestIdRef.current) return;
      const draftDocument = normalizeWorkflowEditorDocument(
        {
          config_path: savedDocument?.config_path ?? "",
          layout_path: savedDocument?.layout_path ?? "",
          config_exists: Boolean(savedDocument?.config_exists),
          layout_exists: Boolean(savedDocument?.layout_exists),
          config: draft.config,
          layout: draft.layout,
          graphs: draft.graphs,
          output_schemas: draft.output_schemas,
          validation_diagnostics: draft.validation_diagnostics,
          revision: savedDocument?.revision ?? "generator-draft",
        },
        selectedDirectoryPath,
      );
      applyWorkflowDocumentState(
        draftDocument,
        workflowNodeIds(draftDocument.config),
        draft.validation_diagnostics,
        { autoFit: true },
      );
      setWorkflowGeneratorWarnings(draft.warnings ?? []);
      setWorkflowGeneratorModel(draft.model);
      setWorkflowGeneratorReadyDescription(description);
      setWorkflowGeneratorState("ready");
      setWorkflowGeneratorError(null);
    } catch (reason: unknown) {
      if (requestId !== workflowGeneratorRequestIdRef.current) return;
      if (reason instanceof DOMException && reason.name === "AbortError") return;
      setWorkflowGeneratorState("error");
      setWorkflowGeneratorError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      if (workflowGeneratorAbortRef.current === controller) {
        workflowGeneratorAbortRef.current = null;
      }
    }
  }

  return {
    handleWorkflowGeneratorDescriptionChange,
    generateWorkflowGeneratorDraft,
    acceptWorkflowGeneratorDraft,
    cancelWorkflowGenerator,
    openWorkflowGenerator,
  };
}
