import {
  type Dispatch,
  type ChangeEvent as ReactChangeEvent,
  type FormEvent as ReactFormEvent,
  type SetStateAction,
} from "react";
import {
  EditIcon,
  GenerateWorkflowIcon,
  HistoryIcon,
  PlusIcon,
} from "../../features/admin/PageActionIcons";
import {
  type WorkflowConfig,
  type WorkflowEditorDocument,
  type WorkflowPackDescriptor,
  type WorkflowPackHistoryDetail,
  type WorkflowPackHistoryEntry,
} from "../../types";
import {
  type WorkflowGeneratorState,
  type WorkflowPackFormState,
  type WorkflowPackModalMode,
} from "./editorTypes";
import { WorkflowGeneratorCanvasPanel } from "./WorkflowGeneratorCanvasPanel";
import { WorkflowPackHistory, WorkflowPackHistoryDetailPanel } from "./WorkflowPackHistory";

type PackPanelRenderersInputs = {
  workflowGeneratorOpen: boolean;
  config: WorkflowConfig;
  workflowGeneratorDescription: string;
  workflowGeneratorState: WorkflowGeneratorState;
  workflowEditLocked: boolean;
  workflowGeneratorReadyDescription: string | null;
  workflowGeneratorModel: string | null;
  handleWorkflowGeneratorDescriptionChange: (value: string) => void;
  workflowGeneratorError: string | null;
  workflowGeneratorWarnings: string[];
  generateWorkflowGeneratorDraft: () => Promise<void>;
  acceptWorkflowGeneratorDraft: () => void;
  cancelWorkflowGenerator: () => void;
  workflowPackModalMode: WorkflowPackModalMode | null;
  workflowPackModalTarget: WorkflowPackDescriptor | null;
  workflowPackOptions: WorkflowPackDescriptor[];
  closeWorkflowPackModal: () => void;
  workflowPackSubmitting: boolean;
  submitWorkflowPackModal: (event: ReactFormEvent<HTMLFormElement>) => Promise<void>;
  workflowPackForm: WorkflowPackFormState;
  setWorkflowPackForm: Dispatch<SetStateAction<WorkflowPackFormState>>;
  workflowPackError: string | null;
  removeSelectedWorkflowPack: (pack?: WorkflowPackDescriptor | null) => Promise<boolean>;
  setWorkflowPackModalMode: Dispatch<SetStateAction<WorkflowPackModalMode | null>>;
  setWorkflowPackModalTargetReference: Dispatch<SetStateAction<string | null>>;
  workflowPackManageLocked: boolean;
  workflowPackHistoryDetail: WorkflowPackHistoryDetail | null;
  setWorkflowPackHistoryDetail: Dispatch<SetStateAction<WorkflowPackHistoryDetail | null>>;
  selectedWorkflowPack: WorkflowPackDescriptor | null;
  workflowPackHistoryLoading: boolean;
  workflowPackHistoryLoadingMore: boolean;
  workflowPackHistoryError: string | null;
  workflowPackHistory: WorkflowPackHistoryEntry[];
  loadWorkflowPackHistoryDetail: (changeId: string) => Promise<void>;
  workflowPackHistoryDetailLoading: boolean;
  workflowPackHistoryNextCursor: string | null;
  loadWorkflowPackHistory: (workflowPack?: string | null, cursor?: string | null) => Promise<void>;
  selectedWorkflowPackReference: string;
  workflowReadLocked: boolean;
  document: WorkflowEditorDocument | null;
  isInspectMode: boolean;
  openWorkflowGenerator: () => void;
  handleWorkflowPackChange: (event: ReactChangeEvent<HTMLSelectElement>) => Promise<void>;
  isTutorialMode: boolean;
  canManageWorkflowPacks: boolean;
  workflowPackHistoryOpen: boolean;
  toggleWorkflowPackHistoryPanel: () => void;
  openEditWorkflowPackModal: (pack?: WorkflowPackDescriptor | null) => void;
  openCreateWorkflowPackModal: () => void;
};

/** Binds pack panel renderers to the current render state. */
export function createPackPanelRenderers({
  workflowGeneratorOpen,
  config,
  workflowGeneratorDescription,
  workflowGeneratorState,
  workflowEditLocked,
  workflowGeneratorReadyDescription,
  workflowGeneratorModel,
  handleWorkflowGeneratorDescriptionChange,
  workflowGeneratorError,
  workflowGeneratorWarnings,
  generateWorkflowGeneratorDraft,
  acceptWorkflowGeneratorDraft,
  cancelWorkflowGenerator,
  workflowPackModalMode,
  workflowPackModalTarget,
  workflowPackOptions,
  closeWorkflowPackModal,
  workflowPackSubmitting,
  submitWorkflowPackModal,
  workflowPackForm,
  setWorkflowPackForm,
  workflowPackError,
  removeSelectedWorkflowPack,
  setWorkflowPackModalMode,
  setWorkflowPackModalTargetReference,
  workflowPackManageLocked,
  workflowPackHistoryDetail,
  setWorkflowPackHistoryDetail,
  selectedWorkflowPack,
  workflowPackHistoryLoading,
  workflowPackHistoryLoadingMore,
  workflowPackHistoryError,
  workflowPackHistory,
  loadWorkflowPackHistoryDetail,
  workflowPackHistoryDetailLoading,
  workflowPackHistoryNextCursor,
  loadWorkflowPackHistory,
  selectedWorkflowPackReference,
  workflowReadLocked,
  document,
  isInspectMode,
  openWorkflowGenerator,
  handleWorkflowPackChange,
  isTutorialMode,
  canManageWorkflowPacks,
  workflowPackHistoryOpen,
  toggleWorkflowPackHistoryPanel,
  openEditWorkflowPackModal,
  openCreateWorkflowPackModal,
}: PackPanelRenderersInputs) {
  function renderWorkflowGeneratorCanvasPanel() {
    return (
      <WorkflowGeneratorCanvasPanel
        workflowGeneratorOpen={workflowGeneratorOpen}
        config={config}
        workflowGeneratorDescription={workflowGeneratorDescription}
        workflowGeneratorState={workflowGeneratorState}
        workflowEditLocked={workflowEditLocked}
        workflowGeneratorReadyDescription={workflowGeneratorReadyDescription}
        workflowGeneratorModel={workflowGeneratorModel}
        handleWorkflowGeneratorDescriptionChange={handleWorkflowGeneratorDescriptionChange}
        workflowGeneratorError={workflowGeneratorError}
        workflowGeneratorWarnings={workflowGeneratorWarnings}
        generateWorkflowGeneratorDraft={generateWorkflowGeneratorDraft}
        acceptWorkflowGeneratorDraft={acceptWorkflowGeneratorDraft}
        cancelWorkflowGenerator={cancelWorkflowGenerator}
      />
    );
  }

  function renderWorkflowPackModal() {
    if (!workflowPackModalMode) return null;
    const editingWorkflowPack = workflowPackModalMode === "edit" ? workflowPackModalTarget : null;
    const canRemoveEditingWorkflowPack = Boolean(editingWorkflowPack && workflowPackOptions.length > 1);
    const title = workflowPackModalMode === "create" ? "Add workflow pack" : "Edit workflow pack";
    const submitLabel = workflowPackModalMode === "create" ? "Add pack" : "Save metadata";
    return (
      <div
        className="modal-backdrop workflow-pack-modal-backdrop"
        role="presentation"
        onClick={(event) => {
          if (event.target === event.currentTarget) {
            closeWorkflowPackModal();
          }
        }}
      >
        <div className="modal-card workflow-pack-modal" role="dialog" aria-modal="true" aria-label={title}>
          <div className="modal-header">
            <div>
              <div className="section-label">Workflow pack</div>
              <h2>{title}</h2>
              {editingWorkflowPack ? (
                <p>{editingWorkflowPack.reference}</p>
              ) : null}
            </div>
            <button type="button" className="subtle-button" onClick={closeWorkflowPackModal} disabled={workflowPackSubmitting}>
              Close
            </button>
          </div>
          <form className="modal-body workflow-pack-form" onSubmit={submitWorkflowPackModal}>
            <label className="inspector-label">
              <span>Pack ID</span>
              <input
                value={workflowPackForm.packId}
                onChange={(event) => setWorkflowPackForm((current) => ({ ...current, packId: event.target.value }))}
                disabled={workflowPackSubmitting}
                autoFocus
              />
            </label>
            <label className="inspector-label">
              <span>Version</span>
              <input
                value={workflowPackForm.packVersion}
                onChange={(event) => setWorkflowPackForm((current) => ({ ...current, packVersion: event.target.value }))}
                disabled={workflowPackSubmitting}
              />
            </label>
            <label className="inspector-label">
              <span>Description</span>
              <textarea
                value={workflowPackForm.description}
                onChange={(event) => setWorkflowPackForm((current) => ({ ...current, description: event.target.value }))}
                rows={4}
                disabled={workflowPackSubmitting}
              />
            </label>
            {workflowPackError ? <div className="runtime-run-error">{workflowPackError}</div> : null}
            <div className="workflow-pack-form-actions">
              <div>
                {canRemoveEditingWorkflowPack ? (
                  <button
                    type="button"
                    className="danger"
                    onClick={async () => {
                      if (!editingWorkflowPack) return;
                      const removed = await removeSelectedWorkflowPack(editingWorkflowPack);
                      if (removed) {
                        setWorkflowPackModalMode(null);
                        setWorkflowPackModalTargetReference(null);
                      }
                    }}
                    disabled={workflowPackSubmitting || workflowPackManageLocked}
                  >
                    Remove
                  </button>
                ) : null}
              </div>
              <div className="workflow-pack-form-primary-actions">
                <button type="button" className="subtle-button" onClick={closeWorkflowPackModal} disabled={workflowPackSubmitting}>
                  Cancel
                </button>
                <button type="submit" className="primary" disabled={workflowPackSubmitting || workflowPackManageLocked}>
                  {workflowPackSubmitting ? "Saving..." : submitLabel}
                </button>
              </div>
            </div>
          </form>
        </div>
      </div>
    );
  }

  function renderWorkflowPackHistoryDetail() {
    return (
      <WorkflowPackHistoryDetailPanel
        workflowPackHistoryDetail={workflowPackHistoryDetail}
        setWorkflowPackHistoryDetail={setWorkflowPackHistoryDetail}
      />
    );
  }

  function renderWorkflowPackHistory() {
    return (
      <WorkflowPackHistory
        selectedWorkflowPack={selectedWorkflowPack}
        workflowPackHistoryLoading={workflowPackHistoryLoading}
        workflowPackHistoryLoadingMore={workflowPackHistoryLoadingMore}
        workflowPackHistoryError={workflowPackHistoryError}
        workflowPackHistory={workflowPackHistory}
        workflowPackHistoryDetail={workflowPackHistoryDetail}
        loadWorkflowPackHistoryDetail={loadWorkflowPackHistoryDetail}
        workflowPackHistoryDetailLoading={workflowPackHistoryDetailLoading}
        workflowPackHistoryNextCursor={workflowPackHistoryNextCursor}
        loadWorkflowPackHistory={loadWorkflowPackHistory}
        selectedWorkflowPackReference={selectedWorkflowPackReference}
        workflowReadLocked={workflowReadLocked}
        renderWorkflowPackHistoryDetail={renderWorkflowPackHistoryDetail}
      />
    );
  }

  function renderWorkflowGeneratorCanvasControl() {
    if (!document || isInspectMode) return null;
    return (
      <div className="workflow-generator-canvas-control">
        <button
          type="button"
          className={`workflow-generator-canvas-button${workflowGeneratorOpen ? " active" : ""}`}
          onClick={openWorkflowGenerator}
          disabled={workflowEditLocked}
          aria-label="Generate workflow"
          aria-haspopup="dialog"
          aria-expanded={workflowGeneratorOpen}
          title="Generate workflow"
        >
          <GenerateWorkflowIcon />
        </button>
      </div>
    );
  }

  function renderWorkflowPackCanvasControl() {
    if (!document) return null;
    return (
      <div className="workflow-pack-canvas-control" aria-label="Workflow pack selector">
        <select
          value={selectedWorkflowPackReference}
          onChange={(event) => void handleWorkflowPackChange(event)}
          disabled={workflowReadLocked || isTutorialMode || workflowPackOptions.length <= 1}
          aria-label="Workflow pack"
        >
          {workflowPackOptions.length === 0 ? (
            <option value="">Inline config</option>
          ) : (
            workflowPackOptions.map((pack) => (
              <option key={pack.reference} value={pack.reference}>
                {pack.display_name || pack.reference}
              </option>
            ))
          )}
        </select>
        {selectedWorkflowPack || canManageWorkflowPacks ? (
          <div className="workflow-pack-canvas-actions">
            {selectedWorkflowPack ? (
              <button
                type="button"
                className={`workflow-pack-canvas-button${workflowPackHistoryOpen ? " active" : ""}`}
                onClick={toggleWorkflowPackHistoryPanel}
                disabled={workflowReadLocked}
                aria-label={`Show history for ${selectedWorkflowPack.display_name || selectedWorkflowPack.reference}`}
                aria-expanded={workflowPackHistoryOpen}
                title="Workflow pack history"
              >
                <HistoryIcon />
              </button>
            ) : null}
            {canManageWorkflowPacks ? (
              <>
                {selectedWorkflowPack ? (
                  <button
                    type="button"
                    className="workflow-pack-canvas-button"
                    onClick={() => openEditWorkflowPackModal(selectedWorkflowPack)}
                    disabled={workflowPackManageLocked || !selectedWorkflowPack.editable || workflowPackSubmitting}
                    aria-label={`Edit ${selectedWorkflowPack.display_name || selectedWorkflowPack.reference}`}
                    title="Edit workflow pack"
                  >
                    <EditIcon />
                  </button>
                ) : null}
                <button
                  type="button"
                  className="workflow-pack-canvas-button"
                  onClick={openCreateWorkflowPackModal}
                  disabled={workflowPackManageLocked || workflowPackSubmitting}
                  aria-label="Add workflow pack"
                  title="Add workflow pack"
                >
                  <PlusIcon />
                </button>
              </>
            ) : null}
          </div>
        ) : null}
        {workflowPackError ? <div className="runtime-run-error">{workflowPackError}</div> : null}
      </div>
    );
  }

  function renderWorkflowPackHistoryCanvasPanel() {
    if (!workflowPackHistoryOpen || !selectedWorkflowPack) return null;
    const selectedPackLabel = selectedWorkflowPack.display_name || selectedWorkflowPack.reference;
    return (
      <section className="workflow-pack-history-popover" aria-label="Workflow pack history">
        <div className="workflow-pack-history-popover-header">
          <span className="section-label">History</span>
          <strong className="workflow-pack-history-pack-name">{selectedPackLabel}</strong>
        </div>
        {renderWorkflowPackHistory()}
      </section>
    );
  }

  return {
    renderWorkflowPackModal,
    renderWorkflowGeneratorCanvasControl,
    renderWorkflowGeneratorCanvasPanel,
    renderWorkflowPackCanvasControl,
    renderWorkflowPackHistoryCanvasPanel,
  };
}
