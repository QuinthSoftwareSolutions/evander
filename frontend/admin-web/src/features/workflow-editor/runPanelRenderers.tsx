import {
  type Dispatch,
  type FormEvent as ReactFormEvent,
  type SetStateAction,
} from "react";
import {
  type WorkflowEditorNodeCatalogEntry,
  type WorkflowNodeConfig,
  type WorkflowNodeKind,
  type WorkflowRunAgentLink,
  type WorkflowRunDataAvailability,
  type WorkflowRunDetail,
  type WorkflowRunNodeDetail,
  type WorkflowRunSearch,
  type WorkflowRunSearchItem,
  type WorkflowRunSummary,
} from "../../types";
import {
  type OperatorSteeringNote,
  type WorkflowRunDetailLookupKind,
  type WorkflowRunSearchScopeOption,
} from "./editorTypes";
import { InspectedNodePanel } from "./InspectedNodePanel";
import { InspectedRunPanel } from "./InspectedRunPanel";
import { OperatorSteeringPanel } from "./OperatorSteeringPanel";
import { RunDetailAgentLink } from "./RunDetailAgentLink";
import { renderRunDetailLinkGroup, renderRunDetailToolCallLink } from "./RunDetailContent";
import {
  formatRuntimeTimestamp,
  normalizeAvailabilityState,
  summarizeRunNode,
  summarizeRunSearchStatuses,
  workflowRunStateClassName,
  workflowRunStateLabel,
} from "./runDetailsModel";

type RunPanelRenderersInputs = {
  operatorSteeringNotesByKey: Record<string, OperatorSteeringNote[]>;
  operatorSteeringDraftsByKey: Record<string, string>;
  operatorSteeringLoadingByKey: Record<string, boolean>;
  operatorSteeringSubmittingByKey: Record<string, boolean>;
  operatorSteeringErrorsByKey: Record<string, string>;
  canControlWorkflowRuns: boolean;
  privilegedUnlocked: boolean;
  submitOperatorSteeringNote: (agent: WorkflowRunAgentLink) => Promise<void>;
  setOperatorSteeringDraftsByKey: Dispatch<SetStateAction<Record<string, string>>>;
  setOperatorSteeringError: (key: string, message: string | null) => void;
  loadOperatorSteeringNotes: (sessionId: string, dispatchId: string) => Promise<void>;
  operatorSteeringCancellingById: Record<string, boolean>;
  cancelOperatorSteeringNote: (note: OperatorSteeringNote) => Promise<void>;
  runDetail: WorkflowRunDetail | null;
  inspectVisitedNodeIds: Set<string>;
  inspectDisplayNodeIds: Set<string>;
  exitInspectMode: () => void;
  selectedNodeId: string | null;
  selectedInspectGraphNode: (WorkflowNodeConfig & { kind: WorkflowNodeKind; }) | undefined;
  selectedInspectNodeDetail: WorkflowRunNodeDetail | undefined;
  selectedInspectCatalogEntry: WorkflowEditorNodeCatalogEntry | undefined;
  inspectWorkflowRun: (eventId: string) => void;
  runDetailLoading: boolean;
  workflowRunReadLocked: boolean;
  canReadWorkflowRuns: boolean;
  visibleRuns: WorkflowRunSummary[];
  runSnapshotError: string | null;
  submitWorkflowRunDetailLookup: (event: ReactFormEvent<HTMLFormElement>) => void;
  runDetailLookupKind: WorkflowRunDetailLookupKind;
  setRunDetailLookupKind: Dispatch<SetStateAction<WorkflowRunDetailLookupKind>>;
  runDetailLookup: string;
  setRunDetailLookup: Dispatch<SetStateAction<string>>;
  runDetailError: string | null;
  runSearchResult: WorkflowRunSearch | null;
  submitWorkflowRunSearch: (event: ReactFormEvent<HTMLFormElement>) => void;
  runSearchScope: string;
  setRunSearchScope: Dispatch<SetStateAction<string>>;
  runSearchScopeOptions: WorkflowRunSearchScopeOption[];
  runSearchWithoutSession: boolean;
  setRunSearchWithoutSession: Dispatch<SetStateAction<boolean>>;
  runSearchIncludeCompleted: boolean;
  setRunSearchIncludeCompleted: Dispatch<SetStateAction<boolean>>;
  runSearchLoading: boolean;
  selectedRunSearchScope: WorkflowRunSearchScopeOption | undefined;
  runSearchError: string | null;
  resumeWorkflowRun: (eventId: string) => Promise<void>;
  resumingRunIds: Record<string, boolean>;
  workflowRunControlLocked: boolean;
  clearWorkflowRun: (eventId: string, runState?: string) => Promise<void>;
  clearingRunIds: Record<string, boolean>;
};

/** Binds run panel renderers to the current render state. */
export function createRunPanelRenderers({
  operatorSteeringNotesByKey,
  operatorSteeringDraftsByKey,
  operatorSteeringLoadingByKey,
  operatorSteeringSubmittingByKey,
  operatorSteeringErrorsByKey,
  canControlWorkflowRuns,
  privilegedUnlocked,
  submitOperatorSteeringNote,
  setOperatorSteeringDraftsByKey,
  setOperatorSteeringError,
  loadOperatorSteeringNotes,
  operatorSteeringCancellingById,
  cancelOperatorSteeringNote,
  runDetail,
  inspectVisitedNodeIds,
  inspectDisplayNodeIds,
  exitInspectMode,
  selectedNodeId,
  selectedInspectGraphNode,
  selectedInspectNodeDetail,
  selectedInspectCatalogEntry,
  inspectWorkflowRun,
  runDetailLoading,
  workflowRunReadLocked,
  canReadWorkflowRuns,
  visibleRuns,
  runSnapshotError,
  submitWorkflowRunDetailLookup,
  runDetailLookupKind,
  setRunDetailLookupKind,
  runDetailLookup,
  setRunDetailLookup,
  runDetailError,
  runSearchResult,
  submitWorkflowRunSearch,
  runSearchScope,
  setRunSearchScope,
  runSearchScopeOptions,
  runSearchWithoutSession,
  setRunSearchWithoutSession,
  runSearchIncludeCompleted,
  setRunSearchIncludeCompleted,
  runSearchLoading,
  selectedRunSearchScope,
  runSearchError,
  resumeWorkflowRun,
  resumingRunIds,
  workflowRunControlLocked,
  clearWorkflowRun,
  clearingRunIds,
}: RunPanelRenderersInputs) {
  function renderOperatorSteeringPanel(agent: WorkflowRunAgentLink) {
    return (
      <OperatorSteeringPanel
        agent={agent}
        operatorSteeringNotesByKey={operatorSteeringNotesByKey}
        operatorSteeringDraftsByKey={operatorSteeringDraftsByKey}
        operatorSteeringLoadingByKey={operatorSteeringLoadingByKey}
        operatorSteeringSubmittingByKey={operatorSteeringSubmittingByKey}
        operatorSteeringErrorsByKey={operatorSteeringErrorsByKey}
        canControlWorkflowRuns={canControlWorkflowRuns}
        privilegedUnlocked={privilegedUnlocked}
        submitOperatorSteeringNote={submitOperatorSteeringNote}
        setOperatorSteeringDraftsByKey={setOperatorSteeringDraftsByKey}
        setOperatorSteeringError={setOperatorSteeringError}
        loadOperatorSteeringNotes={loadOperatorSteeringNotes}
        operatorSteeringCancellingById={operatorSteeringCancellingById}
        cancelOperatorSteeringNote={cancelOperatorSteeringNote}
      />
    );
  }

  function renderRunDetailAgentLink(agent: WorkflowRunAgentLink, index: number) {
    return (
      <RunDetailAgentLink key={`${agent.node_id}-${agent.session_id ?? "session-missing"}-${agent.execution_id ?? index}`}
        agent={agent}
        index={index}
        renderOperatorSteeringPanel={renderOperatorSteeringPanel}
      />
    );
  }

  function renderInspectedRunPanel() {
    return (
      <InspectedRunPanel
        runDetail={runDetail}
        inspectVisitedNodeIds={inspectVisitedNodeIds}
        inspectDisplayNodeIds={inspectDisplayNodeIds}
        exitInspectMode={exitInspectMode}
      />
    );
  }

  function renderInspectedNodePanel() {
    return (
      <InspectedNodePanel
        runDetail={runDetail}
        selectedNodeId={selectedNodeId}
        selectedInspectGraphNode={selectedInspectGraphNode}
        selectedInspectNodeDetail={selectedInspectNodeDetail}
        renderRunDetailNodeLinks={renderRunDetailNodeLinks}
        selectedInspectCatalogEntry={selectedInspectCatalogEntry}
      />
    );
  }

  function renderRunDetailNodeLinks(node: WorkflowRunNodeDetail, availability: WorkflowRunDataAvailability) {
    const toolCallAvailable = normalizeAvailabilityState(availability.tool_calls) === "available";
    const agentLinkAvailable = normalizeAvailabilityState(availability.agent_links) === "available";
    const artifactAvailable = normalizeAvailabilityState(availability.communication_artifacts) === "available";
    const toolCallItems = node.tool_call_links.map((link, index) => renderRunDetailToolCallLink(link, "node", index));
    const approvalItems = node.approval_links.map((approval) => (
      <div className="run-detail-link-item" key={approval.request_id}>
        <strong>Approval {approval.request_id}</strong>
        <span>{approval.action_type}{approval.tool_name ? ` / ${approval.tool_name}` : ""}</span>
        <span>Boundary {approval.directory_path}</span>
        <span>{approval.status}</span>
        <span>Payload {approval.payload_digest}</span>
        {approval.human_summary ? <span>{approval.human_summary}</span> : null}
        {approval.approver ? <span>Approver {approval.approver}</span> : null}
        {approval.decision_at ? <span>Decision {formatRuntimeTimestamp(approval.decision_at)}</span> : null}
      </div>
    ));
    const agentItems = node.agent_links.map((agent, index) => renderRunDetailAgentLink(agent, index));
    const artifactItems = node.communication_artifact_links.map((artifact) => (
      <div className="run-detail-link-item" key={artifact.artifact_id}>
        <strong>Artifact {artifact.artifact_id}</strong>
        <span>{artifact.filename ?? artifact.original_filename ?? "filename missing"}</span>
        <span>{artifact.provider ?? "provider missing"}</span>
        {artifact.sha256 ? <span>SHA-256 {artifact.sha256}</span> : null}
        {artifact.status ? <span>{artifact.status}</span> : null}
      </div>
    ));
    if (
      toolCallItems.length === 0 &&
      approvalItems.length === 0 &&
      agentItems.length === 0 &&
      artifactItems.length === 0 &&
      toolCallAvailable &&
      agentLinkAvailable &&
      artifactAvailable
    ) {
      return null;
    }
    return (
      <div className="run-detail-node-links">
        {toolCallItems.length > 0 || !toolCallAvailable ? renderRunDetailLinkGroup("Tool calls", "No tool-call audit records", toolCallItems) : null}
        {approvalItems.length > 0 ? renderRunDetailLinkGroup("Approvals", "No approval records", approvalItems) : null}
        {agentItems.length > 0 || !agentLinkAvailable ? renderRunDetailLinkGroup("Agents", "No agent session records", agentItems) : null}
        {artifactItems.length > 0 || !artifactAvailable ? renderRunDetailLinkGroup("Communication artifacts", "No communication artifact records", artifactItems) : null}
      </div>
    );
  }

  function renderRunSearchResult(run: WorkflowRunSearchItem) {
    const stateClass = workflowRunStateClassName(run.run_state, run.delivery_state);
    const sessionLabel = run.has_agent_session
      ? `Session ${run.session_ids.slice(0, 2).join(", ")}${run.session_ids.length > 2 ? ` +${run.session_ids.length - 2}` : ""}`
      : "No agent session";
    return (
      <div key={run.event_id} className="runtime-run-card run-search-card">
        <div className="runtime-run-header">
          <div className="runtime-run-event">{run.event_id}</div>
          <div className={`runtime-run-state state-${stateClass}`}>{workflowRunStateLabel(run.run_state, run.delivery_state)}</div>
        </div>
        <div className="runtime-run-meta">
          Latest {formatRuntimeTimestamp(run.latest_activity_at ?? run.occurred_at)}
        </div>
        <div className="runtime-run-meta">
          {sessionLabel}
          {run.trace_id ? ` - Trace ${run.trace_id}` : ""}
        </div>
        <div className="runtime-run-meta">
          Matched {run.matched_node_ids.join(", ") || "None"} - {summarizeRunSearchStatuses(run.status_counts)}
        </div>
        {run.error ? <div className="runtime-run-error">{run.error}</div> : null}
        <div className="runtime-run-nodes">
          {run.nodes.slice(0, 6).map((activity, index) => (
            <div key={`${run.event_id}-${activity.node_id}-${activity.status}-${index}`} className="runtime-run-node">
              {summarizeRunNode(activity)}
            </div>
          ))}
          {run.nodes.length > 6 ? (
            <div className="runtime-run-node">+{run.nodes.length - 6} more</div>
          ) : null}
        </div>
        <div className="runtime-run-actions">
                      <button
                        type="button"
                        className="subtle-button"
                        onClick={() => inspectWorkflowRun(run.event_id)}
                        disabled={runDetailLoading || workflowRunReadLocked}
                      >
                        Inspect
                      </button>
        </div>
      </div>
    );
  }

  function renderRunsPanelContent() {
    if (!canReadWorkflowRuns) {
      return (
        <div className="sidebar-section compact-panel-section">
          <div className="section-label">Runs</div>
          <p className="runtime-summary">Permission required: workflow_runs.read.</p>
        </div>
      );
    }

    return (
      <div className="sidebar-section compact-panel-section current-runs-section">
        <div className="current-runs-summary compact-section-heading">
          <span className="section-label current-runs-label">Current runs</span>
          <span className="current-runs-count">{visibleRuns.length}</span>
        </div>
        <p className="runtime-summary">
          {visibleRuns.length === 0
            ? "No active or errored workflow runs."
            : `${visibleRuns.length} ${visibleRuns.length === 1 ? "run" : "runs"} visible on the graph.`}
        </p>
        {runSnapshotError ? <div className="runtime-run-error">{runSnapshotError}</div> : null}
        <form className="run-detail-lookup" onSubmit={submitWorkflowRunDetailLookup}>
          <select
            value={runDetailLookupKind}
            onChange={(event) => setRunDetailLookupKind(event.target.value as WorkflowRunDetailLookupKind)}
            aria-label="Run lookup key"
          >
            <option value="auto">Auto</option>
            <option value="run_id">Run ID</option>
            <option value="event_id">Event ID</option>
            <option value="trigger_event_id">Trigger event</option>
            <option value="trace_id">Trace ID</option>
            <option value="session_id">Session ID</option>
            <option value="artifact_id">Artifact ID</option>
          </select>
          <input
            value={runDetailLookup}
            onChange={(event) => setRunDetailLookup(event.target.value)}
            placeholder="Lookup id"
            aria-label="Workflow run lookup id"
          />
          <button
            type="submit"
            className="subtle-button"
            disabled={runDetailLoading || !runDetailLookup.trim() || workflowRunReadLocked}
          >
            {runDetailLoading ? "Loading..." : "Inspect"}
          </button>
        </form>
        {runDetailError ? <div className="runtime-run-error">{runDetailError}</div> : null}
        <div className="run-search-panel">
          <div className="run-search-header">
            <span className="section-label">Latest runs</span>
            {runSearchResult ? (
              <span className="current-runs-count">{runSearchResult.returned}</span>
            ) : null}
          </div>
          <form className="run-search-form" onSubmit={submitWorkflowRunSearch}>
            <select
              value={runSearchScope}
              onChange={(event) => setRunSearchScope(event.target.value)}
              aria-label="Run search scope"
            >
              <optgroup label="Graphs">
                {runSearchScopeOptions
                  .filter((option) => option.kind === "graph")
                  .map((option) => (
                    <option value={option.value} key={option.value}>
                      {option.label}
                    </option>
                  ))}
              </optgroup>
              <optgroup label="Nodes">
                {runSearchScopeOptions
                  .filter((option) => option.kind === "node")
                  .map((option) => (
                    <option value={option.value} key={option.value}>
                      {option.label}
                    </option>
                  ))}
              </optgroup>
            </select>
            <label className="checkbox-row run-search-checkbox">
              <input
                type="checkbox"
                checked={runSearchWithoutSession}
                onChange={(event) => setRunSearchWithoutSession(event.target.checked)}
              />
              <span>No agent session</span>
            </label>
            <label className="checkbox-row run-search-checkbox">
              <input
                type="checkbox"
                checked={runSearchIncludeCompleted}
                onChange={(event) => setRunSearchIncludeCompleted(event.target.checked)}
              />
              <span>Completed</span>
            </label>
            <button
              type="submit"
              className="subtle-button"
              disabled={runSearchLoading || !selectedRunSearchScope || workflowRunReadLocked}
            >
              {runSearchLoading ? "Loading..." : "Find"}
            </button>
          </form>
          {runSearchError ? <div className="runtime-run-error">{runSearchError}</div> : null}
          {runSearchResult ? (
            <div className="runtime-run-meta">
              {runSearchResult.matched} matched {runSearchResult.filters.graph_name ?? selectedRunSearchScope?.label ?? ""}
              {runSearchResult.has_more ? `; showing ${runSearchResult.returned}` : ""}
            </div>
          ) : null}
          {runSearchResult && runSearchResult.runs.length === 0 ? (
            <div className="runtime-run-meta">No matching runs.</div>
          ) : null}
          {runSearchResult && runSearchResult.runs.length > 0 ? (
            <div className="runtime-runs run-search-results">
              {runSearchResult.runs.map((run) => renderRunSearchResult(run))}
            </div>
          ) : null}
        </div>
        <div className="runtime-runs">
          {visibleRuns.map((run: WorkflowRunSummary) => (
            <div key={run.event_id} className="runtime-run-card">
              <div className="runtime-run-header">
                <div className="runtime-run-event">{run.event_id}</div>
                <div
                  className={`runtime-run-state state-${workflowRunStateClassName(run.run_state, run.delivery_state)}`}
                >
                  {workflowRunStateLabel(run.run_state, run.delivery_state)}
                </div>
              </div>
              <div className="runtime-run-meta">
                Last activity {formatRuntimeTimestamp(run.latest_activity_at ?? run.occurred_at)}
              </div>
              {run.run_state === "errored" ? (
                <div className="runtime-run-error">
                  Failed at <strong>{run.failed_node_id ?? "unknown node"}</strong>
                  {run.error ? `: ${run.error}` : ""}
                </div>
              ) : null}
              <div className="runtime-run-nodes">
                {run.active_nodes.map((activity, index) => (
                  <div key={`${run.event_id}-${activity.node_id}-${activity.status}-${index}`} className="runtime-run-node">
                    {summarizeRunNode(activity)}
                  </div>
                ))}
              </div>
              <div className="runtime-run-actions">
                <button
                  type="button"
                  className="subtle-button"
                  onClick={() => inspectWorkflowRun(run.event_id)}
                  disabled={runDetailLoading || workflowRunReadLocked}
                >
                  Inspect
                </button>
                {run.run_state === "errored" && run.resumable ? (
                  <button
                    type="button"
                    className="subtle-button"
                    onClick={() => void resumeWorkflowRun(run.event_id)}
                    disabled={Boolean(resumingRunIds[run.event_id]) || workflowRunControlLocked}
                  >
                    {resumingRunIds[run.event_id] ? "Resuming..." : "Resume from failure"}
                  </button>
                ) : null}
                <button
                  type="button"
                  className="subtle-button runtime-run-clear"
                  onClick={() => void clearWorkflowRun(run.event_id, run.run_state)}
                  disabled={Boolean(clearingRunIds[run.event_id]) || workflowRunControlLocked}
                >
                  {clearingRunIds[run.event_id]
                    ? run.run_state === "errored"
                      ? "Dismissing..."
                      : "Clearing..."
                    : run.run_state === "errored"
                      ? "Dismiss run"
                      : "Clear run"}
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  }

    function renderDesktopRunsPanel() {
    return (canReadWorkflowRuns ? (
              <details className="sidebar-section current-runs-section">
                <summary className="current-runs-summary">
                  <span className="section-label current-runs-label">Current runs</span>
                  <span className="current-runs-count">{visibleRuns.length}</span>
                </summary>
                <p className="runtime-summary">
                  {visibleRuns.length === 0
                    ? "No active or errored workflow runs."
                    : `${visibleRuns.length} ${visibleRuns.length === 1 ? "run" : "runs"} visible on the graph.`}
                </p>
                {runSnapshotError ? <div className="runtime-run-error">{runSnapshotError}</div> : null}
                <form className="run-detail-lookup" onSubmit={submitWorkflowRunDetailLookup}>
                  <select
                    value={runDetailLookupKind}
                    onChange={(event) => setRunDetailLookupKind(event.target.value as WorkflowRunDetailLookupKind)}
                    aria-label="Run lookup key"
                  >
                    <option value="auto">Auto</option>
                    <option value="run_id">Run ID</option>
                    <option value="event_id">Event ID</option>
                    <option value="trigger_event_id">Trigger event</option>
                    <option value="trace_id">Trace ID</option>
                    <option value="session_id">Session ID</option>
                    <option value="artifact_id">Artifact ID</option>
                  </select>
                  <input
                    value={runDetailLookup}
                    onChange={(event) => setRunDetailLookup(event.target.value)}
                    placeholder="Lookup id"
                    aria-label="Workflow run lookup id"
                  />
                  <button
                    type="submit"
                    className="subtle-button"
                    disabled={runDetailLoading || !runDetailLookup.trim() || workflowRunReadLocked}
                  >
                    {runDetailLoading ? "Loading..." : "Inspect"}
                  </button>
                </form>
                {runDetailError ? <div className="runtime-run-error">{runDetailError}</div> : null}
                <div className="run-search-panel">
                  <div className="run-search-header">
                    <span className="section-label">Latest runs</span>
                    {runSearchResult ? (
                      <span className="current-runs-count">{runSearchResult.returned}</span>
                    ) : null}
                  </div>
                  <form className="run-search-form" onSubmit={submitWorkflowRunSearch}>
                    <select
                      value={runSearchScope}
                      onChange={(event) => setRunSearchScope(event.target.value)}
                      aria-label="Run search scope"
                    >
                      <optgroup label="Graphs">
                        {runSearchScopeOptions
                          .filter((option) => option.kind === "graph")
                          .map((option) => (
                            <option value={option.value} key={option.value}>
                              {option.label}
                            </option>
                          ))}
                      </optgroup>
                      <optgroup label="Nodes">
                        {runSearchScopeOptions
                          .filter((option) => option.kind === "node")
                          .map((option) => (
                            <option value={option.value} key={option.value}>
                              {option.label}
                            </option>
                          ))}
                      </optgroup>
                    </select>
                    <label className="checkbox-row run-search-checkbox">
                      <input
                        type="checkbox"
                        checked={runSearchWithoutSession}
                        onChange={(event) => setRunSearchWithoutSession(event.target.checked)}
                      />
                      <span>No agent session</span>
                    </label>
                    <label className="checkbox-row run-search-checkbox">
                      <input
                        type="checkbox"
                        checked={runSearchIncludeCompleted}
                        onChange={(event) => setRunSearchIncludeCompleted(event.target.checked)}
                      />
                      <span>Completed</span>
                    </label>
                    <button
                      type="submit"
                      className="subtle-button"
                      disabled={runSearchLoading || !selectedRunSearchScope || workflowRunReadLocked}
                    >
                      {runSearchLoading ? "Loading..." : "Find"}
                    </button>
                  </form>
                  {runSearchError ? <div className="runtime-run-error">{runSearchError}</div> : null}
                  {runSearchResult ? (
                    <div className="runtime-run-meta">
                      {runSearchResult.matched} matched {runSearchResult.filters.graph_name ?? selectedRunSearchScope?.label ?? ""}
                      {runSearchResult.has_more ? `; showing ${runSearchResult.returned}` : ""}
                    </div>
                  ) : null}
                  {runSearchResult && runSearchResult.runs.length === 0 ? (
                    <div className="runtime-run-meta">No matching runs.</div>
                  ) : null}
                  {runSearchResult && runSearchResult.runs.length > 0 ? (
                    <div className="runtime-runs run-search-results">
                      {runSearchResult.runs.map((run) => renderRunSearchResult(run))}
                    </div>
                  ) : null}
                </div>
                <div className="runtime-runs">
                  {visibleRuns.map((run: WorkflowRunSummary) => (
                    <div key={run.event_id} className="runtime-run-card">
                <div className="runtime-run-header">
                  <div className="runtime-run-event">{run.event_id}</div>
                        <div
                          className={`runtime-run-state state-${workflowRunStateClassName(run.run_state, run.delivery_state)}`}
                        >
                          {workflowRunStateLabel(run.run_state, run.delivery_state)}
                        </div>
                      </div>
                      <div className="runtime-run-meta">
                        Last activity {formatRuntimeTimestamp(run.latest_activity_at ?? run.occurred_at)}
                      </div>
                      {run.run_state === "errored" ? (
                        <div className="runtime-run-error">
                          Failed at <strong>{run.failed_node_id ?? "unknown node"}</strong>
                          {run.error ? `: ${run.error}` : ""}
                        </div>
                      ) : null}
                      <div className="runtime-run-nodes">
                        {run.active_nodes.map((activity, index) => (
                          <div key={`${run.event_id}-${activity.node_id}-${activity.status}-${index}`} className="runtime-run-node">
                            {summarizeRunNode(activity)}
                          </div>
                        ))}
                      </div>
                      <div className="runtime-run-actions">
                        <button
                          type="button"
                          className="subtle-button"
                          onClick={() => inspectWorkflowRun(run.event_id)}
                          disabled={runDetailLoading || workflowRunReadLocked}
                        >
                          Inspect
                        </button>
                        {run.run_state === "errored" && run.resumable ? (
                          <button
                            type="button"
                            className="subtle-button"
                            onClick={() => void resumeWorkflowRun(run.event_id)}
                            disabled={Boolean(resumingRunIds[run.event_id]) || workflowRunControlLocked}
                          >
                            {resumingRunIds[run.event_id] ? "Resuming..." : "Resume from failure"}
                          </button>
                        ) : null}
                        <button
                          type="button"
                          className="subtle-button runtime-run-clear"
                          onClick={() => void clearWorkflowRun(run.event_id, run.run_state)}
                          disabled={Boolean(clearingRunIds[run.event_id]) || workflowRunControlLocked}
                        >
                          {clearingRunIds[run.event_id]
                            ? run.run_state === "errored"
                              ? "Dismissing..."
                              : "Clearing..."
                            : run.run_state === "errored"
                              ? "Dismiss run"
                              : "Clear run"}
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              </details>
              ) : null);
  }

  return {
    renderInspectedRunPanel,
    renderInspectedNodePanel,
    renderRunsPanelContent,
    renderRunSearchResult,
    renderDesktopRunsPanel,
  };
}
