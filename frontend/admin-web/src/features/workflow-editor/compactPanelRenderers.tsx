import {
  type Edge,
  type Node,
  type ReactFlowInstance,
} from "@xyflow/react";
import {
  type Dispatch,
  type ReactNode,
  type SetStateAction,
} from "react";
import { type CompactActivePanel, type CompactCanvasMode, type GraphNodeData, type SaveState } from "./editorTypes";

type CompactPanelRenderersInputs = {
  compactCanvasMode: CompactCanvasMode;
  selectCompactCanvasMode: (nextMode: CompactCanvasMode) => void;
  compactActivePanel: CompactActivePanel;
  hasUnsavedWorkflowChanges: boolean;
  toggleCompactPanel: (panel: Exclude<CompactActivePanel, null>) => void;
  fitWorkflowCanvas: () => void;
  setCompactActivePanel: Dispatch<SetStateAction<CompactActivePanel>>;
  flowInstance: ReactFlowInstance<Node<GraphNodeData>, Edge> | null;
  undoWorkflowChange: () => void;
  canUndoWorkflowChanges: boolean;
  workflowMutationLocked: boolean;
  saveDocument: () => Promise<void>;
  saveState: SaveState;
  workflowRunReadLocked: boolean;
  createEntryCount: number;
  renderCreatePanelBody: (compact?: boolean) => ReactNode;
  renderCompactInspectorPanelContent: () => ReactNode | null;
  renderRunsPanelContent: () => ReactNode;
  renderGraphsPanelContent: () => ReactNode;
  renderSavePanelContent: () => ReactNode;
};

/** Binds compact panel renderers to the current render state. */
export function createCompactPanelRenderers({
  compactCanvasMode,
  selectCompactCanvasMode,
  compactActivePanel,
  hasUnsavedWorkflowChanges,
  toggleCompactPanel,
  fitWorkflowCanvas,
  setCompactActivePanel,
  flowInstance,
  undoWorkflowChange,
  canUndoWorkflowChanges,
  workflowMutationLocked,
  saveDocument,
  saveState,
  workflowRunReadLocked,
  createEntryCount,
  renderCreatePanelBody,
  renderCompactInspectorPanelContent,
  renderRunsPanelContent,
  renderGraphsPanelContent,
  renderSavePanelContent,
}: CompactPanelRenderersInputs) {
  function renderCompactToolbar() {
    const modeButton = (mode: CompactCanvasMode, label: string) => (
      <button
        type="button"
        className={`compact-workflow-toolbar-button${compactCanvasMode === mode ? " active" : ""}`}
        aria-pressed={compactCanvasMode === mode}
        onClick={() => selectCompactCanvasMode(mode)}
      >
        {label}
      </button>
    );
    const panelButton = (panel: Exclude<CompactActivePanel, null>, label: string, disabled = false) => (
      <button
        type="button"
        className={`compact-workflow-toolbar-button${compactActivePanel === panel ? " active" : ""}${panel === "save" && hasUnsavedWorkflowChanges ? " dirty" : ""}`}
        aria-pressed={compactActivePanel === panel}
        disabled={disabled}
        onClick={() => toggleCompactPanel(panel)}
      >
        {label}
      </button>
    );

    return (
      <div className="compact-workflow-toolbar" aria-label="Workflow tools">
        <button
          type="button"
          className="compact-workflow-toolbar-button"
          onClick={() => {
            fitWorkflowCanvas();
            setCompactActivePanel(null);
          }}
          disabled={!flowInstance}
        >
          Fit
        </button>
        {modeButton("pan", "Pan")}
        {modeButton("select", "Select")}
        {modeButton("connect", "Connect")}
        <button
          type="button"
          className="compact-workflow-toolbar-button"
          onClick={undoWorkflowChange}
          disabled={!canUndoWorkflowChanges}
        >
          Undo
        </button>
        {panelButton("create", "Create", workflowMutationLocked)}
        {panelButton("inspector", "Inspector")}
        {panelButton("runs", "Runs", workflowRunReadLocked)}
        {panelButton("graphs", "Graphs")}
        <button
          type="button"
          className={`compact-workflow-toolbar-button${hasUnsavedWorkflowChanges ? " dirty" : ""}`}
          onClick={() => void saveDocument()}
          disabled={workflowMutationLocked || saveState === "saving"}
          aria-label="Save workflow"
        >
          {saveState === "saving" ? "Saving..." : "Save"}
        </button>
        {panelButton("save", "Checks")}
      </div>
    );
  }

  function renderCompactBottomSheet() {
    if (!compactActivePanel) return null;
    const titleByPanel: Record<Exclude<CompactActivePanel, null>, string> = {
      create: "Create",
      inspector: "Inspector",
      runs: "Runs",
      graphs: "Graphs",
      save: "Workflow checks",
    };
    const contentByPanel: Record<Exclude<CompactActivePanel, null>, ReactNode> = {
      create: (
        <div className="sidebar-section compact-panel-section">
          <div className="compact-section-heading">
            <span className="section-label">Create</span>
            <span className="collapsible-sidebar-meta">
              {createEntryCount} {createEntryCount === 1 ? "node" : "nodes"}
            </span>
          </div>
          {renderCreatePanelBody(true)}
        </div>
      ),
      inspector: renderCompactInspectorPanelContent(),
      runs: renderRunsPanelContent(),
      graphs: renderGraphsPanelContent(),
      save: renderSavePanelContent(),
    };

    return (
      <div
        className="compact-workflow-sheet-backdrop"
        role="presentation"
        onClick={(event) => {
          if (event.target === event.currentTarget) {
            setCompactActivePanel(null);
          }
        }}
      >
        <section
          className={`compact-workflow-sheet panel-${compactActivePanel}`}
          role="dialog"
          aria-modal="false"
          aria-label={titleByPanel[compactActivePanel]}
        >
          <div className="compact-workflow-sheet-header">
            <div>
              <div className="section-label">Workflow</div>
              <h2>{titleByPanel[compactActivePanel]}</h2>
            </div>
            <button type="button" className="subtle-button" onClick={() => setCompactActivePanel(null)}>
              Close
            </button>
          </div>
          <div className="compact-workflow-sheet-body">
            {contentByPanel[compactActivePanel]}
          </div>
        </section>
      </div>
    );
  }

  return {
    renderCompactToolbar,
    renderCompactBottomSheet,
  };
}
