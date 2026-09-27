import { type WorkflowNodeConfig, type WorkflowNodeExecutionFilter } from "../../types";
import { type WorkflowNodeExecutionFilterDraft } from "./editorTypes";
import { formatRuntimeTimestamp, workflowRunStateClassName } from "./runDetailsModel";
import { type WorkflowNodeExecutionHistoryState } from "./useNodeExecutionHistory";

type NodeExecutionHistoryProps = WorkflowNodeExecutionHistoryState & {
  selectedNode: WorkflowNodeConfig | undefined;
  canReadWorkflowRuns: boolean;
  inspectWorkflowRun: (eventId: string) => void;
};

export function NodeExecutionHistory({
  selectedNode,
  canReadWorkflowRuns,
  nodeExecutionSearch,
  loadNodeExecutionHistory,
  nodeExecutionStartedFrom,
  nodeExecutionWindowTouchedRef,
  setNodeExecutionStartedFrom,
  nodeExecutionStartedTo,
  setNodeExecutionStartedTo,
  nodeExecutionStatuses,
  setNodeExecutionStatuses,
  nodeExecutionFields,
  nodeExecutionFilters,
  updateNodeExecutionFilter,
  setNodeExecutionFilters,
  nodeExecutionLoading,
  nodeExecutionError,
  nodeExecutionDetailLoading,
  loadNodeExecutionDetail,
  inspectWorkflowRun,
  nodeExecutionDetail,
}: NodeExecutionHistoryProps) {
  if (!selectedNode || !canReadWorkflowRuns) return null;
  const availability = nodeExecutionSearch?.availability;
  return (
    <details className="node-execution-history">
      <summary>Execution history</summary>
      <p className="field-description">
        Query this node's inputs and outputs. Dates use your local timezone; the end is exclusive.
      </p>
      <form
        className="node-execution-history-form"
        onSubmit={(event) => {
          event.preventDefault();
          void loadNodeExecutionHistory();
        }}
      >
        <div className="node-execution-history-window">
          <label className="inspector-label">
            <span>From</span>
            <input
              type="datetime-local"
              step="0.001"
              value={nodeExecutionStartedFrom}
              onChange={(event) => {
                nodeExecutionWindowTouchedRef.current = true;
                setNodeExecutionStartedFrom(event.target.value);
              }}
            />
          </label>
          <label className="inspector-label">
            <span>To (exclusive)</span>
            <input
              type="datetime-local"
              step="0.001"
              value={nodeExecutionStartedTo}
              onChange={(event) => {
                nodeExecutionWindowTouchedRef.current = true;
                setNodeExecutionStartedTo(event.target.value);
              }}
            />
          </label>
        </div>
        <label className="inspector-label">
          <span>Statuses</span>
          <input
            value={nodeExecutionStatuses}
            onChange={(event) => setNodeExecutionStatuses(event.target.value)}
            placeholder="succeeded, failed (blank means all)"
          />
        </label>
        <datalist id="node-execution-field-paths">
          {(nodeExecutionFields?.fields ?? []).map((field) => (
            <option
              key={`${field.location}:${field.json_pointer}:${field.value_type}`}
              value={field.json_pointer}
            >
              {field.location} · {field.value_type} · {field.observed_count}
            </option>
          ))}
        </datalist>
        <div className="node-execution-filter-list">
          {nodeExecutionFilters.map((filter, index) => (
            <div className="node-execution-filter" key={index}>
              <select
                value={filter.location}
                aria-label={`Execution filter ${index + 1} location`}
                onChange={(event) => updateNodeExecutionFilter(index, {
                  location: event.target.value as "input" | "output",
                })}
              >
                <option value="input">Input</option>
                <option value="output">Output</option>
              </select>
              <input
                list="node-execution-field-paths"
                value={filter.path}
                aria-label={`Execution filter ${index + 1} JSON Pointer`}
                placeholder="/user/name"
                onChange={(event) => updateNodeExecutionFilter(index, { path: event.target.value })}
              />
              <select
                value={filter.operator}
                aria-label={`Execution filter ${index + 1} operator`}
                onChange={(event) => {
                  const operator = event.target.value as WorkflowNodeExecutionFilter["operator"];
                  updateNodeExecutionFilter(index, {
                    operator,
                    ...(operator === "gt" || operator === "gte" || operator === "lt" || operator === "lte"
                      ? { valueType: "number" }
                      : {}),
                  });
                }}
              >
                <option value="eq">equals</option>
                <option value="exists">exists</option>
                <option value="not_exists">does not exist</option>
                <option value="gt">greater than</option>
                <option value="gte">greater or equal</option>
                <option value="lt">less than</option>
                <option value="lte">less or equal</option>
              </select>
              {!(["exists", "not_exists"] as string[]).includes(filter.operator) ? (
                <>
                  <select
                    value={filter.valueType}
                    aria-label={`Execution filter ${index + 1} value type`}
                    disabled={["gt", "gte", "lt", "lte"].includes(filter.operator)}
                    onChange={(event) => updateNodeExecutionFilter(index, {
                      valueType: event.target.value as WorkflowNodeExecutionFilterDraft["valueType"],
                    })}
                  >
                    <option value="string">text</option>
                    <option value="number">number</option>
                    <option value="boolean">boolean</option>
                    <option value="null">null</option>
                  </select>
                  {filter.valueType !== "null" ? (
                    filter.valueType === "boolean" ? (
                      <select
                        value={filter.value}
                        aria-label={`Execution filter ${index + 1} value`}
                        onChange={(event) => updateNodeExecutionFilter(index, { value: event.target.value })}
                      >
                        <option value="true">true</option>
                        <option value="false">false</option>
                      </select>
                    ) : (
                      <input
                        type={filter.valueType === "number" ? "number" : "text"}
                        value={filter.value}
                        aria-label={`Execution filter ${index + 1} value`}
                        onChange={(event) => updateNodeExecutionFilter(index, { value: event.target.value })}
                      />
                    )
                  ) : null}
                </>
              ) : null}
              <button
                type="button"
                className="subtle-button"
                aria-label={`Remove execution filter ${index + 1}`}
                onClick={() => setNodeExecutionFilters((current) =>
                  current.filter((_, filterIndex) => filterIndex !== index))}
              >
                Remove
              </button>
            </div>
          ))}
        </div>
        <div className="runtime-run-actions">
          <button
            type="button"
            className="subtle-button"
            disabled={nodeExecutionFilters.length >= 8}
            onClick={() => setNodeExecutionFilters((current) => [
              ...current,
              { location: "output", path: "", operator: "eq", valueType: "string", value: "" },
            ])}
          >
            Add filter
          </button>
          <button type="submit" className="subtle-button" disabled={nodeExecutionLoading}>
            {nodeExecutionLoading ? "Querying..." : "Query history"}
          </button>
        </div>
      </form>
      {nodeExecutionError ? <div className="runtime-run-error">{nodeExecutionError}</div> : null}
      {availability ? (
        <div className="runtime-run-meta node-execution-availability">
          Full payloads: {availability.full_retention_days} days. Compact query history: {availability.compact_retention_days} days.
          {availability.backfill?.completed === true
            ? " Existing-record backfill is complete."
            : availability.backfill?.completed === false
              ? ` Existing-record backfill is in progress (${availability.backfill.archived ?? 0} copied).`
              : " Existing-record backfill has not started yet."}
          {availability.earliest_compact_execution
            ? ` Earliest available: ${formatRuntimeTimestamp(availability.earliest_compact_execution)}.`
            : " No compact executions are available yet."}
        </div>
      ) : null}
      {nodeExecutionSearch ? (
        <div className="runtime-run-meta">
          {nodeExecutionSearch.matched == null
            ? `Showing ${nodeExecutionSearch.executions.length}${nodeExecutionSearch.has_more ? "; more available" : ""}.`
            : `${nodeExecutionSearch.matched} matched; showing ${nodeExecutionSearch.executions.length}.`}
        </div>
      ) : null}
      <div className="runtime-runs node-execution-results">
        {(nodeExecutionSearch?.executions ?? []).map((execution) => {
          const durationMs = Math.max(
            0,
            new Date(execution.finished_at).getTime() - new Date(execution.started_at).getTime(),
          );
          return (
            <div className="runtime-run-card" key={execution.execution_key}>
              <div className="runtime-run-header">
                <div className="runtime-run-event">{formatRuntimeTimestamp(execution.started_at)}</div>
                <div className={`runtime-run-state state-${workflowRunStateClassName(execution.status)}`}>
                  {execution.status}
                </div>
              </div>
              <div className="runtime-run-meta">
                {execution.execution_kind} · {durationMs} ms · {execution.full_record_available ? "full payload" : "compact payload"}
              </div>
              {execution.config_hash ? (
                <div className="runtime-run-meta">Configuration hash: <code>{execution.config_hash}</code></div>
              ) : null}
              <code>{execution.execution_id}</code>
              {Object.keys(execution.matched_values).length > 0 ? (
                <pre>{JSON.stringify(execution.matched_values, null, 2)}</pre>
              ) : null}
              <div className="runtime-run-actions">
                <button
                  type="button"
                  className="subtle-button"
                  disabled={nodeExecutionDetailLoading}
                  onClick={() => void loadNodeExecutionDetail(execution.execution_key)}
                >
                  Details
                </button>
                <button
                  type="button"
                  className="subtle-button"
                  title={execution.full_record_available
                    ? "Inspect the surrounding workflow run"
                    : "Run inspection is unavailable after full run retention expires"}
                  disabled={!execution.full_record_available}
                  onClick={() => inspectWorkflowRun(execution.event_id)}
                >
                  Inspect run
                </button>
              </div>
            </div>
          );
        })}
      </div>
      {nodeExecutionSearch?.has_more && nodeExecutionSearch.next_cursor ? (
        <button
          type="button"
          className="subtle-button"
          disabled={nodeExecutionLoading}
          onClick={() => void loadNodeExecutionHistory(nodeExecutionSearch.next_cursor ?? null, true)}
        >
          {nodeExecutionLoading ? "Loading..." : "Load more"}
        </button>
      ) : null}
      {nodeExecutionDetail ? (
        <details className="raw-json-panel node-execution-detail" open>
          <summary>
            Execution detail · {nodeExecutionDetail.record_tier} · {nodeExecutionDetail.execution_id}
          </summary>
          {nodeExecutionDetail.config_hash ? (
            <div className="runtime-run-meta">Configuration hash: <code>{nodeExecutionDetail.config_hash}</code></div>
          ) : null}
          <div className="section-label">Input</div>
          <pre>{JSON.stringify(nodeExecutionDetail.input, null, 2)}</pre>
          <div className="section-label">Output</div>
          <pre>{JSON.stringify(nodeExecutionDetail.output, null, 2)}</pre>
          {nodeExecutionDetail.sources.length > 0 ? (
            <>
              <div className="section-label">Sources</div>
              <pre>{JSON.stringify(nodeExecutionDetail.sources, null, 2)}</pre>
            </>
          ) : null}
        </details>
      ) : null}
    </details>
  );
}
