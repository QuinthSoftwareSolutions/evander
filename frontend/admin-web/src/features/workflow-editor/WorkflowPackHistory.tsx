import {
  Fragment,
  type Dispatch,
  type ReactNode,
  type SetStateAction,
} from "react";
import {
  type WorkflowPackDescriptor,
  type WorkflowPackHistoryDetail,
  type WorkflowPackHistoryEntry,
} from "../../types";
import { formatRuntimeTimestamp } from "./runDetailsModel";

export function workflowPackHistoryOperationLabel(operation: string): string {
  if (operation === "create") return "Created";
  if (operation === "update_metadata") return "Metadata";
  if (operation === "remove") return "Removed";
  if (operation === "save") return "Saved";
  if (operation === "runtime_pack_create") return "Runtime pack created";
  if (operation === "runtime_pack_configure") return "Runtime pack listed";
  if (operation === "runtime_pack_refresh") return "Runtime pack refreshed";
  return operation;
}

export function workflowPackHistorySummary(entry: WorkflowPackHistoryEntry): string {
  const summary = entry.summary ?? {};
  const numberValue = (key: string): number | null => (
    typeof summary[key] === "number" ? summary[key] as number : null
  );
  const nodesBefore = numberValue("nodes_before");
  const nodesAfter = numberValue("nodes_after");
  const graphsBefore = numberValue("graphs_before");
  const graphsAfter = numberValue("graphs_after");
  const packsBefore = numberValue("workflow_packs_before");
  const packsAfter = numberValue("workflow_packs_after");
  if ((entry.operation === "create" || entry.operation === "remove") && packsBefore !== null && packsAfter !== null) {
    return `${packsBefore} -> ${packsAfter} active packs`;
  }
  if (nodesBefore !== null && nodesAfter !== null && nodesBefore !== nodesAfter) {
    return `${nodesBefore} -> ${nodesAfter} nodes`;
  }
  if (graphsBefore !== null && graphsAfter !== null && graphsBefore !== graphsAfter) {
    return `${graphsBefore} -> ${graphsAfter} graphs`;
  }
  return entry.touched_paths.length === 1 ? "1 file" : `${entry.touched_paths.length} files`;
}

type WorkflowPackHistoryDetailProps = {
  workflowPackHistoryDetail: WorkflowPackHistoryDetail | null;
  setWorkflowPackHistoryDetail: Dispatch<SetStateAction<WorkflowPackHistoryDetail | null>>;
};

export function WorkflowPackHistoryDetailPanel({
  workflowPackHistoryDetail,
  setWorkflowPackHistoryDetail,
}: WorkflowPackHistoryDetailProps) {
  if (!workflowPackHistoryDetail) return null;
  const { entry, diffs } = workflowPackHistoryDetail;
  const changedDiffs = diffs.filter((diff) => diff.changes.length > 0);
  return (
    <div className="workflow-pack-history-detail">
      <div className="workflow-pack-history-detail-header">
        <strong>{workflowPackHistoryOperationLabel(entry.operation)}</strong>
        <button type="button" className="subtle-button" onClick={() => setWorkflowPackHistoryDetail(null)}>
          Close
        </button>
      </div>
      <dl className="workflow-pack-history-revisions">
        <div>
          <dt>Before</dt>
          <dd>{entry.before_revision ? entry.before_revision.slice(0, 12) : "none"}</dd>
        </div>
        <div>
          <dt>After</dt>
          <dd>{entry.after_revision ? entry.after_revision.slice(0, 12) : "none"}</dd>
        </div>
      </dl>
      {changedDiffs.length ? (
        <div className="workflow-pack-history-diffs">
          {changedDiffs.slice(0, 3).map((diff) => (
            <div key={diff.label} className="workflow-pack-history-diff">
              <strong>{diff.label}</strong>
              <ul>
                {diff.changes.slice(0, 8).map((change, index) => (
                  <li key={`${diff.label}-${index}`}>
                    <code>{String(change.path ?? "$")}</code>
                    <span>{String(change.change_type ?? "changed")}</span>
                  </li>
                ))}
              </ul>
              {diff.truncated || diff.changes.length > 8 ? (
                <small>{diff.changes.length > 8 ? `${diff.changes.length - 8} more changes` : "Diff truncated"}</small>
              ) : null}
            </div>
          ))}
        </div>
      ) : (
        <p className="workflow-pack-history-empty">No JSON field changes.</p>
      )}
    </div>
  );
}

type WorkflowPackHistoryProps = {
  selectedWorkflowPack: WorkflowPackDescriptor | null;
  workflowPackHistoryLoading: boolean;
  workflowPackHistoryLoadingMore: boolean;
  workflowPackHistoryError: string | null;
  workflowPackHistory: WorkflowPackHistoryEntry[];
  workflowPackHistoryDetail: WorkflowPackHistoryDetail | null;
  loadWorkflowPackHistoryDetail: (changeId: string) => Promise<void>;
  workflowPackHistoryDetailLoading: boolean;
  workflowPackHistoryNextCursor: string | null;
  loadWorkflowPackHistory: (workflowPack?: string | null, cursor?: string | null) => Promise<void>;
  selectedWorkflowPackReference: string;
  workflowReadLocked: boolean;
  renderWorkflowPackHistoryDetail: () => ReactNode | null;
};

export function WorkflowPackHistory({
  selectedWorkflowPack,
  workflowPackHistoryLoading,
  workflowPackHistoryLoadingMore,
  workflowPackHistoryError,
  workflowPackHistory,
  workflowPackHistoryDetail,
  loadWorkflowPackHistoryDetail,
  workflowPackHistoryDetailLoading,
  workflowPackHistoryNextCursor,
  loadWorkflowPackHistory,
  selectedWorkflowPackReference,
  workflowReadLocked,
  renderWorkflowPackHistoryDetail,
}: WorkflowPackHistoryProps) {
  if (!selectedWorkflowPack) return null;
  const workflowPackHistoryBusy = workflowPackHistoryLoading || workflowPackHistoryLoadingMore;
  return (
    <div className="workflow-pack-history">
      {workflowPackHistoryError ? <div className="runtime-run-error">{workflowPackHistoryError}</div> : null}
      {workflowPackHistoryLoading ? (
        <div className="workflow-pack-history-empty">Loading history...</div>
      ) : workflowPackHistory.length === 0 ? (
        <div className="workflow-pack-history-empty">No recorded changes.</div>
      ) : (
        <div className="workflow-pack-history-list">
          {workflowPackHistory.map((entry) => (
            <Fragment key={entry.change_id}>
              <button
                type="button"
                className={`workflow-pack-history-row${workflowPackHistoryDetail?.entry.change_id === entry.change_id ? " selected" : ""}`}
                onClick={() => void loadWorkflowPackHistoryDetail(entry.change_id)}
                disabled={workflowPackHistoryDetailLoading}
              >
                <span>
                  <strong>{workflowPackHistoryOperationLabel(entry.operation)}</strong>
                  <small>{entry.actor.display_name || entry.actor.login} / {formatRuntimeTimestamp(entry.changed_at)}</small>
                </span>
                <span>{workflowPackHistorySummary(entry)}</span>
              </button>
              {workflowPackHistoryDetail?.entry.change_id === entry.change_id && !workflowPackHistoryDetailLoading
                ? renderWorkflowPackHistoryDetail()
                : null}
            </Fragment>
          ))}
          {workflowPackHistoryNextCursor ? (
            <button
              type="button"
              className="subtle-button workflow-pack-history-more"
              onClick={() => void loadWorkflowPackHistory(selectedWorkflowPackReference, workflowPackHistoryNextCursor)}
              disabled={workflowPackHistoryBusy || workflowReadLocked}
            >
              {workflowPackHistoryLoadingMore ? "Loading..." : "More"}
            </button>
          ) : null}
        </div>
      )}
      {workflowPackHistoryDetailLoading ? (
        <div className="workflow-pack-history-empty">Loading change...</div>
      ) : null}
    </div>
  );
}
