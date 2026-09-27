import {
  type Dispatch,
  type ChangeEvent as ReactChangeEvent,
  type FormEvent as ReactFormEvent,
  type SetStateAction,
} from "react";
import { ApiError } from "../../features/admin/api";
import {
  type WorkflowEditorDocument,
  type WorkflowPackDescriptor,
  type WorkflowPackMutation,
  type WorkflowValidationDiagnostic,
} from "../../types";
import { fetchJson } from "./editorApi";
import {
  type NodeConnectionMenuState,
  type PendingConnectionState,
  type SaveState,
  type WorkflowPackFormState,
  type WorkflowPackModalMode,
  type WorkflowUndoEntry,
} from "./editorTypes";
import { normalizeWorkflowEditorDocument } from "./graphModel";

type WorkflowPackActionsInputs = {
  selectedWorkflowPackReference: string;
  hasUnsavedWorkflowChanges: boolean;
  setError: Dispatch<SetStateAction<string | null>>;
  loadWorkflowDocument: (workflowPack?: string | null) => Promise<void>;
  setWorkflowPackModalTargetReference: Dispatch<SetStateAction<string | null>>;
  setWorkflowPackForm: Dispatch<SetStateAction<WorkflowPackFormState>>;
  setWorkflowPackError: Dispatch<SetStateAction<string | null>>;
  setWorkflowPackModalMode: Dispatch<SetStateAction<WorkflowPackModalMode | null>>;
  selectedWorkflowPack: WorkflowPackDescriptor | null;
  workflowPackSubmitting: boolean;
  selectedDirectoryPath: string;
  setSavedDocument: Dispatch<SetStateAction<WorkflowEditorDocument | null>>;
  setUndoHistory: Dispatch<SetStateAction<WorkflowUndoEntry[]>>;
  setNodeEditOrigins: Dispatch<SetStateAction<Record<string, string>>>;
  setSelectedGraphId: Dispatch<SetStateAction<string | null>>;
  setSelectedEdgeId: Dispatch<SetStateAction<string | null>>;
  setPendingConnection: Dispatch<SetStateAction<PendingConnectionState | null>>;
  setNodeConnectionMenu: Dispatch<SetStateAction<NodeConnectionMenuState | null>>;
  setInspectorDraftError: Dispatch<SetStateAction<string | null>>;
  applyWorkflowDocumentState: (nextDocument: WorkflowEditorDocument, selectedNodeIdsAfterPatch?: string[], diagnosticsOverride?: WorkflowValidationDiagnostic[], options?: { autoFit?: boolean; markClean?: boolean; nodeEditOrigins?: Record<string, string>; }) => void;
  setSaveState: Dispatch<SetStateAction<SaveState>>;
  loadWorkflowPackHistory: (workflowPack?: string | null, cursor?: string | null) => Promise<void>;
  document: WorkflowEditorDocument | null;
  workflowPackManageLocked: boolean;
  workflowPackModalMode: WorkflowPackModalMode | null;
  workflowPackModalTarget: WorkflowPackDescriptor | null;
  workflowPackForm: WorkflowPackFormState;
  setWorkflowPackSubmitting: Dispatch<SetStateAction<boolean>>;
  workflowPackOptions: WorkflowPackDescriptor[];
  workflowReadLocked: boolean;
  workflowPackHistoryOpen: boolean;
  setWorkflowPackHistoryOpen: Dispatch<SetStateAction<boolean>>;
};

/** Binds workflow pack actions to the current render state. */
export function createWorkflowPackActions({
  selectedWorkflowPackReference,
  hasUnsavedWorkflowChanges,
  setError,
  loadWorkflowDocument,
  setWorkflowPackModalTargetReference,
  setWorkflowPackForm,
  setWorkflowPackError,
  setWorkflowPackModalMode,
  selectedWorkflowPack,
  workflowPackSubmitting,
  selectedDirectoryPath,
  setSavedDocument,
  setUndoHistory,
  setNodeEditOrigins,
  setSelectedGraphId,
  setSelectedEdgeId,
  setPendingConnection,
  setNodeConnectionMenu,
  setInspectorDraftError,
  applyWorkflowDocumentState,
  setSaveState,
  loadWorkflowPackHistory,
  document,
  workflowPackManageLocked,
  workflowPackModalMode,
  workflowPackModalTarget,
  workflowPackForm,
  setWorkflowPackSubmitting,
  workflowPackOptions,
  workflowReadLocked,
  workflowPackHistoryOpen,
  setWorkflowPackHistoryOpen,
}: WorkflowPackActionsInputs) {
  async function switchWorkflowPack(nextWorkflowPack: string) {
    if (!nextWorkflowPack || nextWorkflowPack === selectedWorkflowPackReference) return;
    if (hasUnsavedWorkflowChanges) {
      const confirmed = window.confirm("Discard unsaved workflow changes and switch workflow packs?");
      if (!confirmed) return;
    }
    setError(null);
    try {
      await loadWorkflowDocument(nextWorkflowPack);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  async function handleWorkflowPackChange(event: ReactChangeEvent<HTMLSelectElement>) {
    await switchWorkflowPack(event.target.value);
  }

  function confirmDiscardUnsavedWorkflowPackAction(): boolean {
    if (!hasUnsavedWorkflowChanges) return true;
    return window.confirm("Discard unsaved workflow changes before managing workflow packs?");
  }

  function workflowPackFormFor(pack: WorkflowPackDescriptor | null): WorkflowPackFormState {
    return {
      packId: pack?.id ?? "",
      packVersion: pack?.version ?? "v1",
      description: pack?.description ?? "",
    };
  }

  function openCreateWorkflowPackModal() {
    if (!confirmDiscardUnsavedWorkflowPackAction()) return;
    setWorkflowPackModalTargetReference(null);
    setWorkflowPackForm({ packId: "", packVersion: "v1", description: "" });
    setWorkflowPackError(null);
    setWorkflowPackModalMode("create");
  }

  function openEditWorkflowPackModal(pack: WorkflowPackDescriptor | null = selectedWorkflowPack) {
    if (!pack || !confirmDiscardUnsavedWorkflowPackAction()) return;
    setWorkflowPackModalTargetReference(pack.reference);
    setWorkflowPackForm(workflowPackFormFor(pack));
    setWorkflowPackError(null);
    setWorkflowPackModalMode("edit");
  }

  function closeWorkflowPackModal() {
    if (workflowPackSubmitting) return;
    setWorkflowPackModalMode(null);
    setWorkflowPackModalTargetReference(null);
    setWorkflowPackError(null);
  }

  function applyWorkflowPackMutation(mutation: WorkflowPackMutation) {
    const nextDocument = normalizeWorkflowEditorDocument(mutation.document, selectedDirectoryPath);
    setSavedDocument(nextDocument);
    setUndoHistory([]);
    setNodeEditOrigins({});
    setSelectedGraphId(null);
    setSelectedEdgeId(null);
    setPendingConnection(null);
    setNodeConnectionMenu(null);
    setInspectorDraftError(null);
    applyWorkflowDocumentState(
      nextDocument,
      [],
      nextDocument.validation_diagnostics ?? [],
      { autoFit: true, markClean: true, nodeEditOrigins: {} },
    );
    setSaveState("saved");
    void loadWorkflowPackHistory(nextDocument.selected_workflow_pack?.reference ?? "");
  }

  async function submitWorkflowPackModal(event: ReactFormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!document || workflowPackManageLocked || !workflowPackModalMode) return;
    const editingWorkflowPack = workflowPackModalMode === "edit" ? workflowPackModalTarget : null;
    if (workflowPackModalMode === "edit" && !editingWorkflowPack) {
      setWorkflowPackError("Workflow pack metadata is stale. Reload before editing again.");
      return;
    }
    const packId = workflowPackForm.packId.trim();
    const packVersion = workflowPackForm.packVersion.trim() || "v1";
    const description = workflowPackForm.description.trim();
    if (!packId) {
      setWorkflowPackError("Pack ID is required.");
      return;
    }
    if (!packVersion) {
      setWorkflowPackError("Version is required.");
      return;
    }
    setWorkflowPackSubmitting(true);
    setWorkflowPackError(null);
    try {
      const mutation = await fetchJson<WorkflowPackMutation>(
        "/api/workflow-editor/packs",
        {
          method: workflowPackModalMode === "create" ? "POST" : "PUT",
          body: JSON.stringify({
            workflow_pack: workflowPackModalMode === "edit" ? editingWorkflowPack?.reference : undefined,
            directory_path: selectedDirectoryPath,
            pack_id: packId,
            pack_version: packVersion,
            description,
            expected_revision:
              workflowPackModalMode === "create"
                ? document.pack_list_revision
                : editingWorkflowPack?.revision,
          }),
        },
      );
      applyWorkflowPackMutation(mutation);
      setWorkflowPackModalMode(null);
      setWorkflowPackModalTargetReference(null);
    } catch (reason) {
      setWorkflowPackError(
        reason instanceof ApiError && reason.status === 409
          ? "This workflow pack changed on the server. Reload before saving again."
          : reason instanceof Error
            ? reason.message
            : String(reason),
      );
    } finally {
      setWorkflowPackSubmitting(false);
    }
  }

  async function removeSelectedWorkflowPack(pack: WorkflowPackDescriptor | null = selectedWorkflowPack): Promise<boolean> {
    if (!document || workflowPackManageLocked || !pack) return false;
    if (workflowPackOptions.length <= 1) return false;
    if (!confirmDiscardUnsavedWorkflowPackAction()) return false;
    const confirmed = window.confirm(`Remove ${pack.display_name || pack.reference} from active workflow packs?`);
    if (!confirmed) return false;
    setWorkflowPackSubmitting(true);
    setWorkflowPackError(null);
    try {
      const mutation = await fetchJson<WorkflowPackMutation>(
        "/api/workflow-editor/packs",
        {
          method: "DELETE",
          body: JSON.stringify({
            workflow_pack: pack.reference,
            directory_path: selectedDirectoryPath,
            expected_revision: document.pack_list_revision,
          }),
        },
      );
      applyWorkflowPackMutation(mutation);
      return true;
    } catch (reason) {
      setWorkflowPackError(
        reason instanceof ApiError && reason.status === 409
          ? "The workflow pack list changed on the server. Reload before saving again."
          : reason instanceof Error
            ? reason.message
            : String(reason),
      );
      return false;
    } finally {
      setWorkflowPackSubmitting(false);
    }
  }

  function toggleWorkflowPackHistoryPanel() {
    if (!selectedWorkflowPack || workflowReadLocked) return;
    const nextOpen = !workflowPackHistoryOpen;
    setWorkflowPackHistoryOpen(nextOpen);
    if (nextOpen) {
      void loadWorkflowPackHistory(selectedWorkflowPackReference);
    }
  }

  return {
    closeWorkflowPackModal,
    submitWorkflowPackModal,
    removeSelectedWorkflowPack,
    handleWorkflowPackChange,
    toggleWorkflowPackHistoryPanel,
    openEditWorkflowPackModal,
    openCreateWorkflowPackModal,
  };
}
