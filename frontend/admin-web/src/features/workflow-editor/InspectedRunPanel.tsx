import { type WorkflowRunDetail } from "../../types";
import {
  renderRunDetailLinkGroup,
  renderRunDetailMissingBlock,
  renderRunDetailToolCallLink,
} from "./RunDetailContent";
import {
  availabilitySummary,
  formatRuntimeTimestamp,
  normalizeAvailabilityState,
  workflowRunStateClassName,
  workflowRunStateLabel,
} from "./runDetailsModel";

type InspectedRunPanelProps = {
  runDetail: WorkflowRunDetail | null;
  inspectVisitedNodeIds: Set<string>;
  inspectDisplayNodeIds: Set<string>;
  exitInspectMode: () => void;
};

export function InspectedRunPanel({
  runDetail,
  inspectVisitedNodeIds,
  inspectDisplayNodeIds,
  exitInspectMode,
}: InspectedRunPanelProps) {
  if (!runDetail) return null;
  const availabilityItems = [
    ["Tool calls", runDetail.data_availability.tool_calls],
    ["Agent links", runDetail.data_availability.agent_links],
    ["Communication artifacts", runDetail.data_availability.communication_artifacts],
  ] as const;
  return (
    <div className="sidebar-section inspected-run-panel">
      <div className="run-detail-header">
        <div>
          <div className="section-label">Inspected run</div>
          <div className="runtime-run-event">{runDetail.event_id}</div>
        </div>
        <span
          className={`runtime-run-state state-${workflowRunStateClassName(runDetail.run_state, runDetail.delivery_state)}`}
        >
          {workflowRunStateLabel(runDetail.run_state, runDetail.delivery_state)}
        </span>
      </div>
      <div className="run-detail-meta-grid">
        <span>Started {formatRuntimeTimestamp(runDetail.occurred_at)}</span>
        <span>Latest {formatRuntimeTimestamp(runDetail.latest_activity_at ?? runDetail.occurred_at)}</span>
        <span>{runDetail.graph.snapshot ? "Historical graph snapshot" : "Current graph fallback"}</span>
        <span>
          {inspectVisitedNodeIds.size} visited / {inspectDisplayNodeIds.size} shown
        </span>
      </div>
      <details className="run-detail-link-identifiers">
        <summary>Run identifiers</summary>
        <div className="run-detail-meta-grid">
          <span>Worker {runDetail.worker_id}</span>
          <span>Trace {runDetail.trace_id ?? "missing"}</span>
        </div>
      </details>
      <div className="run-detail-availability">
        {availabilityItems.map(([label, value]) => (
          <span key={label} className={normalizeAvailabilityState(value) === "available" ? "" : "missing"}>
            {label} {availabilitySummary(value)}
          </span>
        ))}
      </div>
      {runDetail.tool_call_links?.length
        ? renderRunDetailLinkGroup(
            "Run-level tool calls",
            availabilitySummary(runDetail.data_availability.tool_calls, "Available", "No persisted tool-call records"),
            runDetail.tool_call_links.map((link, index) => renderRunDetailToolCallLink(link, "run", index)),
          )
        : runDetail.data_availability.tool_calls === "available"
          ? null
          : renderRunDetailMissingBlock(
              "Run-level tool calls",
              availabilitySummary(runDetail.data_availability.tool_calls, "Available", "No persisted tool-call records"),
              "run-tool-calls-missing",
            )}
      {runDetail.error ? <div className="runtime-run-error">{runDetail.error}</div> : null}
      <button type="button" className="primary" onClick={exitInspectMode}>
        Exit inspect mode
      </button>
    </div>
  );
}
