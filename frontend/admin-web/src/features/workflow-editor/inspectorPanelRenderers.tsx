import {
  type Dispatch,
  type ReactNode,
  type SetStateAction,
} from "react";
import {
  type WorkflowEdge,
  type WorkflowEditorFieldDefinition,
  type WorkflowNodeConfig,
  type WorkflowValidationDiagnostic,
} from "../../types";
import { type InspectorDraftSnapshot } from "./editorTypes";
import { NodeExecutionHistory } from "./NodeExecutionHistory";

type InspectorPanelRenderersInputs = {
  selectedNodeIds: string[];
  setProgrammaticSelection: (nextNodeIds: string[], preferredNodeId?: string | null) => void;
  workflowEditLocked: boolean;
  selectDownstreamBranch: () => void;
  selectedNodeId: string | null;
  deleteSelectedNode: () => void;
  workflowMutationLocked: boolean;
  nodeExecutionHistory: import("../../features/workflow-editor/useNodeExecutionHistory").WorkflowNodeExecutionHistoryState;
  selectedNode: WorkflowNodeConfig | undefined;
  canReadWorkflowRuns: boolean;
  inspectWorkflowRun: (eventId: string) => void;
  selectedNodeDiagnostics: WorkflowValidationDiagnostic[];
  renderSelectedTriggerToggle: () => ReactNode | null;
  manualEventTriggerButton: ReactNode;
  inspectorDisplayName: string;
  setInspectorDisplayName: Dispatch<SetStateAction<string>>;
  stageInspectorDraft: (nextDraft?: Partial<InspectorDraftSnapshot>) => void;
  renderEffectTargetField: () => ReactNode | null;
  inspectorConfigurationFields: WorkflowEditorFieldDefinition[];
  renderField: (field: WorkflowEditorFieldDefinition) => ReactNode | null;
  inspectorUnknownValues: Record<string, unknown>;
  rawJsonDraft: string;
  inspectorOutputFields: WorkflowEditorFieldDefinition[];
  renderSelectedNodeOutputContract: () => ReactNode | null;
  selectedNodeHasUnsavedChanges: boolean;
  resetSelectedNodeChanges: () => void;
  selectedEdge: WorkflowEdge | undefined;
  selectedEdgeRouteLabel: string | null;
  deleteSelectedEdge: () => void;
  isInspectMode: boolean;
  renderInspectedRunPanel: () => ReactNode;
  renderInspectedNodePanel: () => ReactNode;
  runDetailError: string | null;
  error: string | null;
};

/** Binds inspector panel renderers to the current render state. */
export function createInspectorPanelRenderers({
  selectedNodeIds,
  setProgrammaticSelection,
  workflowEditLocked,
  selectDownstreamBranch,
  selectedNodeId,
  deleteSelectedNode,
  workflowMutationLocked,
  nodeExecutionHistory,
  selectedNode,
  canReadWorkflowRuns,
  inspectWorkflowRun,
  selectedNodeDiagnostics,
  renderSelectedTriggerToggle,
  manualEventTriggerButton,
  inspectorDisplayName,
  setInspectorDisplayName,
  stageInspectorDraft,
  renderEffectTargetField,
  inspectorConfigurationFields,
  renderField,
  inspectorUnknownValues,
  rawJsonDraft,
  inspectorOutputFields,
  renderSelectedNodeOutputContract,
  selectedNodeHasUnsavedChanges,
  resetSelectedNodeChanges,
  selectedEdge,
  selectedEdgeRouteLabel,
  deleteSelectedEdge,
  isInspectMode,
  renderInspectedRunPanel,
  renderInspectedNodePanel,
  runDetailError,
  error,
}: InspectorPanelRenderersInputs) {
  function renderSelectionInspectorPanel() {
    if (selectedNodeIds.length <= 1) return null;
    return (
      <div className="sidebar-section compact-panel-section inspector">
        <div className="section-label">Selection</div>
        <div className="inspector-summary">
          <div>{selectedNodeIds.length} nodes selected</div>
        </div>
        <button onClick={() => setProgrammaticSelection([])} disabled={workflowEditLocked}>
          Clear selection
        </button>
        <button onClick={selectDownstreamBranch} disabled={workflowEditLocked || !selectedNodeId}>
          Select downstream branch
        </button>
        <button className="danger" onClick={deleteSelectedNode} disabled={workflowMutationLocked}>
          Delete selected nodes
        </button>
      </div>
    );
  }

  function renderNodeExecutionHistory() {
    return (
      <NodeExecutionHistory
        {...nodeExecutionHistory}
        selectedNode={selectedNode}
        canReadWorkflowRuns={canReadWorkflowRuns}
        inspectWorkflowRun={inspectWorkflowRun}
      />
    );
  }

  function renderEditableNodeInspectorPanel() {
    if (!selectedNode) return null;
    return (
      <div className="sidebar-section compact-panel-section inspector">
        <div className="section-label">Inspector</div>
        <div className="inspector-summary">
          <div>{selectedNode.type}</div>
          <div>{selectedNode.id}</div>
        </div>
        {selectedNodeDiagnostics.length > 0 ? (
          <div className="problems-panel">
            <div className="problems-heading">
              <span>Problems</span>
              <span>{selectedNodeDiagnostics.length}</span>
            </div>
            <div className="problems-list">
              {selectedNodeDiagnostics.map((diagnostic, index) => (
                <div className="problem-item" key={`${diagnostic.code}-${diagnostic.field_path ?? ""}-${diagnostic.reference ?? ""}-${index}`}>
                  <div className="problem-message">{diagnostic.message}</div>
                  <div className="problem-meta">
                    {diagnostic.field_path ? <span>{diagnostic.field_path}</span> : null}
                    {diagnostic.reference ? <span>{diagnostic.reference}</span> : null}
                  </div>
                </div>
              ))}
            </div>
          </div>
        ) : null}
        <button onClick={selectDownstreamBranch} disabled={workflowEditLocked}>
          Select downstream branch
        </button>
        {renderSelectedTriggerToggle()}
        {manualEventTriggerButton}
        <div className="inspector-metadata-field">
          <span>Node ID</span>
          <code>{selectedNode.id}</code>
        </div>
        <label className="inspector-label">
          <span>Display name</span>
          <input
            value={inspectorDisplayName}
            onChange={(event) => {
              const nextValue = event.target.value;
              setInspectorDisplayName(nextValue);
              stageInspectorDraft({ displayName: nextValue });
            }}
            disabled={workflowMutationLocked}
          />
        </label>

        {renderEffectTargetField()}
        {inspectorConfigurationFields.map((field) => renderField(field))}

        {Object.keys(inspectorUnknownValues).length > 0 ? (
          <details className="raw-json-panel">
            <summary>Additional raw JSON</summary>
            <p className="field-description">These keys are preserved because they are not in the current catalog.</p>
            <textarea value={rawJsonDraft} rows={8} readOnly />
          </details>
        ) : null}

        {inspectorOutputFields.map((field) => renderField(field))}

        {renderSelectedNodeOutputContract()}

        {renderNodeExecutionHistory()}

        {selectedNodeHasUnsavedChanges ? (
          <button className="primary" onClick={resetSelectedNodeChanges} disabled={workflowMutationLocked}>
            Reset changes
          </button>
        ) : null}

        <button className="danger" onClick={deleteSelectedNode} disabled={workflowMutationLocked}>
          Delete node
        </button>
      </div>
    );
  }

  function renderEditableEdgeInspectorPanel() {
    if (!selectedEdge) return null;
    return (
      <div className="sidebar-section compact-panel-section inspector">
        <div className="section-label">Edge</div>
        <div className="inspector-summary">
          <div>{selectedEdge.source_id}</div>
          <div>Route: {selectedEdgeRouteLabel}</div>
          <div>{selectedEdge.target_id}</div>
          {selectedEdge.allow_cycle ? <div>Cycle return</div> : null}
        </div>
        <button className="danger" onClick={deleteSelectedEdge} disabled={workflowMutationLocked}>
          Delete edge
        </button>
      </div>
    );
  }

  function renderCompactInspectorPanelContent() {
    if (isInspectMode) {
      return (
        <>
          {renderInspectedRunPanel()}
          {renderInspectedNodePanel()}
          {runDetailError ? <div className="runtime-run-error">{runDetailError}</div> : null}
          {error ? <div className="error-box">{error}</div> : null}
        </>
      );
    }
    if (selectedNodeIds.length > 1) return renderSelectionInspectorPanel();
    if (selectedNode) return renderEditableNodeInspectorPanel();
    if (selectedEdge) return renderEditableEdgeInspectorPanel();
    return (
      <div className="sidebar-section compact-panel-section inspector">
        <div className="section-label">Inspector</div>
        <div className="inspector-summary">
          <div>No node or edge selected</div>
        </div>
      </div>
    );
  }

    function renderDesktopSelectionInspectorPanel() {
    return (selectedNodeIds.length > 1 ? (
              <div className="sidebar-section inspector">
                <div className="section-label">Selection</div>
                <div className="inspector-summary">
                  <div>{selectedNodeIds.length} nodes selected</div>
                </div>
                <button onClick={() => setProgrammaticSelection([])} disabled={workflowEditLocked}>
                  Clear selection
                </button>
                <button onClick={selectDownstreamBranch} disabled={workflowEditLocked || !selectedNodeId}>
                  Select downstream branch
                </button>
                <button className="danger" onClick={deleteSelectedNode} disabled={workflowMutationLocked}>
                  Delete selected nodes
                </button>
              </div>
            ) : null);
  }

  function renderDesktopNodeInspectorPanel() {
    return (selectedNode ? (
              <div className="sidebar-section inspector">
                <div className="section-label">Inspector</div>
                <div className="inspector-summary">
                  <div>{selectedNode.type}</div>
                  <div>{selectedNode.id}</div>
                </div>
                {selectedNodeDiagnostics.length > 0 ? (
                  <div className="problems-panel">
                    <div className="problems-heading">
                      <span>Problems</span>
                      <span>{selectedNodeDiagnostics.length}</span>
                    </div>
                    <div className="problems-list">
                      {selectedNodeDiagnostics.map((diagnostic, index) => (
                        <div className="problem-item" key={`${diagnostic.code}-${diagnostic.field_path ?? ""}-${diagnostic.reference ?? ""}-${index}`}>
                          <div className="problem-message">{diagnostic.message}</div>
                          <div className="problem-meta">
                            {diagnostic.field_path ? <span>{diagnostic.field_path}</span> : null}
                            {diagnostic.reference ? <span>{diagnostic.reference}</span> : null}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null}
                <button onClick={selectDownstreamBranch} disabled={workflowEditLocked}>
                  Select downstream branch
                </button>
                {renderSelectedTriggerToggle()}
                {manualEventTriggerButton}
                <div className="inspector-metadata-field">
                  <span>Node ID</span>
                  <code>{selectedNode.id}</code>
                </div>
                <label className="inspector-label">
                  <span>Display name</span>
                  <input
                    value={inspectorDisplayName}
                    onChange={(event) => {
                      const nextValue = event.target.value;
                      setInspectorDisplayName(nextValue);
                      stageInspectorDraft({ displayName: nextValue });
                    }}
                    disabled={workflowMutationLocked}
                  />
                </label>

                {renderEffectTargetField()}
                {inspectorConfigurationFields.map((field) => renderField(field))}

                {Object.keys(inspectorUnknownValues).length > 0 ? (
                  <details className="raw-json-panel">
                    <summary>Additional raw JSON</summary>
                    <p className="field-description">These keys are preserved because they are not in the current catalog.</p>
                    <textarea value={rawJsonDraft} rows={8} readOnly />
                  </details>
                ) : null}

                {inspectorOutputFields.map((field) => renderField(field))}

                {renderSelectedNodeOutputContract()}

                {renderNodeExecutionHistory()}

                {selectedNodeHasUnsavedChanges ? (
                  <button className="primary" onClick={resetSelectedNodeChanges} disabled={workflowMutationLocked}>
                    Reset changes
                  </button>
                ) : null}

                <button className="danger" onClick={deleteSelectedNode} disabled={workflowMutationLocked}>
                  Delete node
                </button>
              </div>
            ) : null);
  }

  function renderDesktopEdgeInspectorPanel() {
    return (selectedEdge ? (
              <div className="sidebar-section inspector">
                <div className="section-label">Edge</div>
                <div className="inspector-summary">
                  <div>{selectedEdge.source_id}</div>
                  <div>Route: {selectedEdgeRouteLabel}</div>
                  <div>{selectedEdge.target_id}</div>
                  {selectedEdge.allow_cycle ? <div>Cycle return</div> : null}
                </div>
                <button className="danger" onClick={deleteSelectedEdge} disabled={workflowMutationLocked}>
                  Delete edge
                </button>
              </div>
            ) : null);
  }

  return {
    renderCompactInspectorPanelContent,
    renderNodeExecutionHistory,
    renderDesktopSelectionInspectorPanel,
    renderDesktopNodeInspectorPanel,
    renderDesktopEdgeInspectorPanel,
  };
}
