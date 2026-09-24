import { type ReactNode } from "react";
import {
  type WorkflowEditorNodeCatalogEntry,
  type WorkflowNodeConfig,
  type WorkflowNodeKind,
  type WorkflowRunDataAvailability,
  type WorkflowRunDetail,
  type WorkflowRunNodeDetail,
} from "../../types";
import { renderResolvedInputEditor, renderRunDetailJson } from "./RunDetailContent";
import { durationLabel, formatRuntimeTimestamp } from "./runDetailsModel";

type InspectedNodePanelProps = {
  runDetail: WorkflowRunDetail | null;
  selectedNodeId: string | null;
  selectedInspectGraphNode: (WorkflowNodeConfig & { kind: WorkflowNodeKind; }) | undefined;
  selectedInspectNodeDetail: WorkflowRunNodeDetail | undefined;
  renderRunDetailNodeLinks: (node: WorkflowRunNodeDetail, availability: WorkflowRunDataAvailability) => ReactNode | null;
  selectedInspectCatalogEntry: WorkflowEditorNodeCatalogEntry | undefined;
};

export function InspectedNodePanel({
  runDetail,
  selectedNodeId,
  selectedInspectGraphNode,
  selectedInspectNodeDetail,
  renderRunDetailNodeLinks,
  selectedInspectCatalogEntry,
}: InspectedNodePanelProps) {
  if (!runDetail) return null;
  const nodeId = selectedNodeId;
  if (!nodeId || !selectedInspectGraphNode) {
    return (
      <div className="sidebar-section inspect-node-panel">
        <div className="section-label">Node run</div>
        <div className="inspector-summary">
          <div>No node selected</div>
        </div>
      </div>
    );
  }
  const nodeTitle = selectedInspectGraphNode.display_name || selectedInspectGraphNode.id;
  if (!selectedInspectNodeDetail) {
    return (
      <div className="sidebar-section inspect-node-panel">
        <div className="section-label">Node run</div>
        <div className="inspector-summary">
          <div>{nodeTitle}</div>
          <div>{selectedInspectGraphNode.type}</div>
          <div>{selectedInspectGraphNode.id}</div>
        </div>
        <div className="run-detail-data-block run-detail-missing">
          <span>Status</span>
          <strong>Not visited in this run</strong>
        </div>
      </div>
    );
  }
  return (
    <div className="sidebar-section inspect-node-panel">
      <div className="section-label">Node run</div>
      <div className="run-detail-header">
        <div>
          <div className="run-detail-node-title">{nodeTitle}</div>
          <div className="runtime-run-event">{selectedInspectNodeDetail.node_id}</div>
        </div>
        <span className={`runtime-run-state state-${selectedInspectNodeDetail.status ?? "unknown"}`}>
          {selectedInspectNodeDetail.status ?? "not recorded"}
        </span>
      </div>
      <div className="run-detail-node-meta">
        <span>{selectedInspectNodeDetail.node_type ?? selectedInspectGraphNode.type}</span>
        <span>{durationLabel(selectedInspectNodeDetail.duration_ms)}</span>
        <span>
          {selectedInspectNodeDetail.started_at
            ? `Started ${formatRuntimeTimestamp(selectedInspectNodeDetail.started_at)}`
            : "Start missing"}
        </span>
        <span>
          {selectedInspectNodeDetail.finished_at
            ? `Finished ${formatRuntimeTimestamp(selectedInspectNodeDetail.finished_at)}`
            : "Finish missing"}
        </span>
      </div>
      {selectedInspectNodeDetail.error ? <div className="runtime-run-error">{selectedInspectNodeDetail.error}</div> : null}
      {selectedInspectNodeDetail.binding_error ? (
        <div className="runtime-run-error">Binding error: {selectedInspectNodeDetail.binding_error}</div>
      ) : null}
      {renderRunDetailNodeLinks(selectedInspectNodeDetail, runDetail.data_availability)}
      {renderResolvedInputEditor(selectedInspectNodeDetail, selectedInspectCatalogEntry)}
      {renderRunDetailJson("Outputs", selectedInspectNodeDetail.outputs, selectedInspectNodeDetail.missing_fields.includes("outputs"))}
      {renderRunDetailJson("Input payload", selectedInspectNodeDetail.input_payload, selectedInspectNodeDetail.missing_fields.includes("payload"))}
      {renderRunDetailJson("Inputs by source", selectedInspectNodeDetail.inputs_by_source, false)}
      {renderRunDetailJson("Binding trace", selectedInspectNodeDetail.binding_trace, selectedInspectNodeDetail.missing_fields.includes("binding_trace"))}
      {renderRunDetailJson("Raw records", selectedInspectNodeDetail.records, selectedInspectNodeDetail.records.length === 0)}
    </div>
  );
}
