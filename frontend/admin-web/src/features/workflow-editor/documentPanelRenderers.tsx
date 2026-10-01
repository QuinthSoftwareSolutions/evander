import { type WorkflowTutorialRequiredAction } from "../../workflowTutorials";
import {
  type Dispatch,
  type DragEvent as ReactDragEvent,
  type SetStateAction,
} from "react";
import { Tooltip } from "../../features/admin/Tooltip";
import {
  type WorkflowEditorDocument,
  type WorkflowEditorNodeCatalogEntry,
  type WorkflowGraphMetadata,
  type WorkflowNodeKind,
} from "../../types";
import { groupWorkflowNodeCatalogEntries } from "../../workflowCatalogGroups";
import { type CompactActivePanel, type SaveState } from "./editorTypes";
import { nodeTypeDescription } from "./fieldModels";

type DocumentPanelRenderersInputs = {
  isTutorialMode: boolean;
  createEntryCount: number;
  tutorialCurrentRequiredAction: () => WorkflowTutorialRequiredAction | null;
  compactCreateSearch: string;
  createEntriesByKind: { entries: WorkflowEditorNodeCatalogEntry[]; subgroups: import("../../workflowCatalogGroups").WorkflowNodeCatalogSubgroup[]; kind: WorkflowNodeKind; label: string; description: string; }[];
  setCompactCreateSearch: Dispatch<SetStateAction<string>>;
  tutorialAllowsNodeType: (kind: WorkflowNodeKind, type: string) => boolean;
  workflowMutationLocked: boolean;
  startNodeTypeDrag: (event: ReactDragEvent<HTMLButtonElement>, entry: WorkflowEditorNodeCatalogEntry) => void;
  addNode: (kind: WorkflowNodeKind, type: string, requestedPosition?: { x: number; y: number; } | null) => void;
  setCompactActivePanel: Dispatch<SetStateAction<CompactActivePanel>>;
  validateDocument: () => Promise<void>;
  workflowReadLocked: boolean;
  undoWorkflowChange: () => void;
  canUndoWorkflowChanges: boolean;
  saveDocument: () => Promise<void>;
  saveState: SaveState;
  savePanelStatusTone: "saved" | "dirty" | "clean";
  hasUnsavedWorkflowChanges: boolean;
  dirtyNodeSummary: string | null;
  error: string | null;
  document: WorkflowEditorDocument | null;
  visibleWorkflowGraphs: WorkflowGraphMetadata[];
  graphViewContext: string;
  selectedGraphId: string | null;
  selectWorkflowGraph: (graph: WorkflowGraphMetadata) => void;
  workflowEditLocked: boolean;
  selectedGraph: WorkflowGraphMetadata | undefined;
  graphNameDraft: string;
  setGraphNameDraft: Dispatch<SetStateAction<string>>;
  graphDescriptionDraft: string;
  setGraphDescriptionDraft: Dispatch<SetStateAction<string>>;
  graphDirectoryDraft: string;
  selectedDirectoryPath: string;
  setGraphDirectoryDraft: Dispatch<SetStateAction<string>>;
  graphUsersDraft: string;
  setGraphUsersDraft: Dispatch<SetStateAction<string>>;
  applyWorkflowGraphChanges: () => void;
};

/** Binds document panel renderers to the current render state. */
export function createDocumentPanelRenderers({
  isTutorialMode,
  createEntryCount,
  tutorialCurrentRequiredAction,
  compactCreateSearch,
  createEntriesByKind,
  setCompactCreateSearch,
  tutorialAllowsNodeType,
  workflowMutationLocked,
  startNodeTypeDrag,
  addNode,
  setCompactActivePanel,
  validateDocument,
  workflowReadLocked,
  undoWorkflowChange,
  canUndoWorkflowChanges,
  saveDocument,
  saveState,
  savePanelStatusTone,
  hasUnsavedWorkflowChanges,
  dirtyNodeSummary,
  error,
  document,
  visibleWorkflowGraphs,
  graphViewContext,
  selectedGraphId,
  selectWorkflowGraph,
  workflowEditLocked,
  selectedGraph,
  graphNameDraft,
  setGraphNameDraft,
  graphDescriptionDraft,
  setGraphDescriptionDraft,
  graphDirectoryDraft,
  selectedDirectoryPath,
  setGraphDirectoryDraft,
  graphUsersDraft,
  setGraphUsersDraft,
  applyWorkflowGraphChanges,
}: DocumentPanelRenderersInputs) {
  function renderCreatePanelBody(compact = false) {
    const normalizedSearch = compactCreateSearch.trim().toLowerCase();
    const groups = createEntriesByKind
      .map((group) => {
        const entries = normalizedSearch
          ? group.entries.filter((entry) => {
              const haystack = [
                entry.label,
                entry.type,
                entry.kind,
                nodeTypeDescription(entry),
              ].join(" ").toLowerCase();
              return haystack.includes(normalizedSearch);
            })
          : group.entries;
        return {
          ...group,
          entries,
          subgroups: groupWorkflowNodeCatalogEntries(entries, group.kind),
        };
      })
      .filter((group) => group.entries.length > 0);

    const renderNodeButton = (entry: WorkflowEditorNodeCatalogEntry) => (
      <Tooltip key={entry.type} content={nodeTypeDescription(entry)} inline={false}>
        <button
          type="button"
          draggable={!workflowMutationLocked || tutorialAllowsNodeType(entry.kind, entry.type)}
          onDragStart={(event) => startNodeTypeDrag(event, entry)}
          onClick={() => {
            addNode(entry.kind, entry.type);
            if (compact) setCompactActivePanel(null);
          }}
          disabled={workflowMutationLocked && !tutorialAllowsNodeType(entry.kind, entry.type)}
        >
          {entry.label}
        </button>
      </Tooltip>
    );

    return (
      <>
        <label className="inspector-label workflow-node-search">
          <span>Search nodes</span>
          <input
            type="search"
            value={compactCreateSearch}
            onChange={(event) => setCompactCreateSearch(event.target.value)}
            placeholder="Trigger, gate, effect, query"
          />
        </label>
        {groups.length === 0 ? (
          <div className="empty-state">No matching nodes.</div>
        ) : null}
        {normalizedSearch ? (
          <div className="create-group-actions workflow-node-search-results">
            {groups.flatMap((group) => group.entries).map(renderNodeButton)}
          </div>
        ) : groups.map((group) => {
          const hasTutorialAllowedNodeType = group.entries.some((entry) =>
            tutorialAllowsNodeType(entry.kind, entry.type),
          );
          return (
            <details
              key={group.kind}
              className="create-group"
              open={hasTutorialAllowedNodeType || undefined}
            >
              <summary className="create-group-summary">
                <Tooltip content={group.description}>
                  <span>{group.label}</span>
                </Tooltip>
                <span className="create-group-count">{group.entries.length}</span>
              </summary>
              <div className="create-subgroups">
                {group.subgroups.map((subgroup) => (
                    <section
                      key={subgroup.key}
                      className="workflow-node-family"
                    >
                      <div className="create-subgroup-summary">
                        <Tooltip content={subgroup.description}>
                          <span>{subgroup.label}</span>
                        </Tooltip>
                        <span className="create-subgroup-count">{subgroup.entries.length}</span>
                      </div>
                      <div className="create-group-actions create-subgroup-actions">
                        {subgroup.entries.map(renderNodeButton)}
                      </div>
                    </section>
                ))}
              </div>
            </details>
          );
        })}
      </>
    );
  }

  function renderSavePanelContent() {
    return (
      <div className="sidebar-section compact-panel-section save-panel-section">
        <div className="section-label">Save</div>
        <button onClick={() => void validateDocument()} disabled={workflowReadLocked}>
          Validate
        </button>
        <button onClick={undoWorkflowChange} disabled={!canUndoWorkflowChanges}>
          Undo
        </button>
        <button className="primary" onClick={() => void saveDocument()} disabled={workflowMutationLocked}>
          {saveState === "saving" ? "Saving..." : "Save workflow"}
        </button>
        <div className={`save-status save-status-${savePanelStatusTone}`} role="status" aria-live="polite">
          <strong>
            {hasUnsavedWorkflowChanges
              ? "Unsaved workflow changes"
              : saveState === "saved"
                ? "Workflow saved"
                : "No unsaved workflow changes"}
          </strong>
          {hasUnsavedWorkflowChanges && dirtyNodeSummary ? <span>{dirtyNodeSummary}</span> : null}
        </div>
        {error ? <div className="error-box save-error-box">{error}</div> : null}
        {document ? (
          <div className="path-note">
            <div>{document.config_path}</div>
            <div>{document.layout_path}</div>
          </div>
        ) : null}
      </div>
    );
  }

  function renderGraphsPanelContent() {
    return (
      <div className="sidebar-section compact-panel-section workflow-graphs-section">
        <div className="collapsible-sidebar-summary compact-section-heading">
          <span className="section-label">Graphs</span>
          <span className="collapsible-sidebar-meta">
            {visibleWorkflowGraphs.length} {visibleWorkflowGraphs.length === 1 ? "graph" : "graphs"}
          </span>
        </div>
        <p className="runtime-summary">{graphViewContext}</p>
        <div className="workflow-graph-list">
          {visibleWorkflowGraphs.length === 0 ? (
            <p className="runtime-summary">No graphs associated with this directory.</p>
          ) : (
            visibleWorkflowGraphs.map((graph) => (
              <button
                type="button"
                key={graph.id}
                className={`workflow-graph-item${selectedGraphId === graph.id ? " selected" : ""}`}
                onClick={() => selectWorkflowGraph(graph)}
                disabled={workflowEditLocked}
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
                disabled={workflowMutationLocked}
              />
            </label>
            <label className="inspector-label">
              <span>Description</span>
              <textarea
                value={graphDescriptionDraft}
                rows={3}
                onChange={(event) => setGraphDescriptionDraft(event.target.value)}
                disabled={workflowMutationLocked}
              />
            </label>
            <label className="inspector-label">
              <span>Directory path</span>
              <p className="field-description">
                Single canonical directory scope for this graph. If omitted in older data, the editor falls back to the selected directory or root.
              </p>
              <input
                value={graphDirectoryDraft}
                placeholder={selectedDirectoryPath}
                onChange={(event) => setGraphDirectoryDraft(event.target.value)}
                disabled={workflowMutationLocked}
              />
            </label>
            <label className="inspector-label">
              <span>User IDs</span>
              <p className="field-description">
                Ownership and audit metadata only. These IDs do not grant visibility or change who can run the graph.
              </p>
              <textarea
                value={graphUsersDraft}
                rows={3}
                onChange={(event) => setGraphUsersDraft(event.target.value)}
                disabled={workflowMutationLocked}
              />
            </label>
            <button
              type="button"
              className="primary"
              onClick={applyWorkflowGraphChanges}
              disabled={workflowMutationLocked}
            >
              Apply graph details
            </button>
          </div>
        ) : null}
      </div>
    );
  }

    function renderDesktopGraphsPanel() {
    return (<details className="sidebar-section workflow-graphs-section collapsible-sidebar-section">
              <summary className="collapsible-sidebar-summary">
                <span className="section-label">Graphs</span>
                <span className="collapsible-sidebar-meta">
                  {visibleWorkflowGraphs.length} {visibleWorkflowGraphs.length === 1 ? "graph" : "graphs"}
                </span>
              </summary>
              <p className="runtime-summary">{graphViewContext}</p>
              <div className="workflow-graph-list">
                {visibleWorkflowGraphs.length === 0 ? (
                  <p className="runtime-summary">No graphs associated with this directory.</p>
                ) : (
                  visibleWorkflowGraphs.map((graph) => (
                    <button
                      type="button"
                      key={graph.id}
                      className={`workflow-graph-item${selectedGraphId === graph.id ? " selected" : ""}`}
                      onClick={() => selectWorkflowGraph(graph)}
                      disabled={workflowEditLocked}
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
                      disabled={workflowMutationLocked}
                    />
                  </label>
                  <label className="inspector-label">
                    <span>Description</span>
                    <textarea
                      value={graphDescriptionDraft}
                      rows={3}
                      onChange={(event) => setGraphDescriptionDraft(event.target.value)}
                      disabled={workflowMutationLocked}
                    />
                  </label>
                  <label className="inspector-label">
                    <span>Directory path</span>
                    <p className="field-description">
                      Single canonical directory scope for this graph. If omitted in older data, the editor falls back to the selected directory or root.
                    </p>
                    <input
                      value={graphDirectoryDraft}
                      placeholder={selectedDirectoryPath}
                      onChange={(event) => setGraphDirectoryDraft(event.target.value)}
                      disabled={workflowMutationLocked}
                    />
                  </label>
                  <label className="inspector-label">
                    <span>User IDs</span>
                    <p className="field-description">
                      Ownership and audit metadata only. These IDs do not grant visibility or change who can run the graph.
                    </p>
                    <textarea
                      value={graphUsersDraft}
                      rows={3}
                      onChange={(event) => setGraphUsersDraft(event.target.value)}
                      disabled={workflowMutationLocked}
                    />
                  </label>
                  <button
                    type="button"
                    className="primary"
                    onClick={applyWorkflowGraphChanges}
                    disabled={workflowMutationLocked}
                  >
                    Apply graph details
                  </button>
                </div>
              ) : null}
            </details>);
  }

  function renderDesktopCreatePanel() {
    return (
      <details
        className="sidebar-section collapsible-sidebar-section workflow-create-section"
        open={!isTutorialMode || tutorialCurrentRequiredAction()?.kind === "add-node"}
      >
        <summary className="collapsible-sidebar-summary">
          <span className="section-label">Create</span>
          <span className="collapsible-sidebar-meta">
            {createEntryCount} {createEntryCount === 1 ? "node" : "nodes"}
          </span>
        </summary>
        {renderCreatePanelBody()}
      </details>
    );
  }

  function renderDesktopSourceFilesPanel() {
    return document ? (
      <details className="sidebar-section workflow-source-files">
        <summary>Source files</summary>
        <div className="path-note">
          <div>{document.config_path}</div>
          <div>{document.layout_path}</div>
        </div>
      </details>
    ) : null;
  }

  return {
    renderCreatePanelBody,
    renderGraphsPanelContent,
    renderSavePanelContent,
    renderDesktopGraphsPanel,
    renderDesktopCreatePanel,
    renderDesktopSourceFilesPanel,
  };
}
