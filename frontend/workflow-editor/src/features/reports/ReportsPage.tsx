import { FormEvent, useEffect, useMemo, useState } from "react";

import { fetchJson, formatTimestamp } from "../admin/api";

type WorkflowGraphActivityNode = {
  node_id: string;
  node_type: string | null;
  display_name: string | null;
  activity_count: number;
  status_counts: Record<string, number>;
  first_activity_at: string | null;
  latest_activity_at: string | null;
};

type WorkflowGraphActivityGraph = {
  graph_id: string;
  name: string;
  description: string | null;
  directory_paths: string[];
  user_ids: string[];
  node_ids: string[];
  node_count: number;
  run_count: number;
  activity_count: number;
  trigger_event_count: number;
  gate_execution_count: number;
  effect_execution_count: number;
  status_counts: Record<string, number>;
  first_activity_at: string | null;
  latest_activity_at: string | null;
  nodes: WorkflowGraphActivityNode[];
};

type WorkflowGraphActivityReport = {
  worker_id: string;
  generated_at: string;
  period_start: string;
  period_end: string;
  graphs: WorkflowGraphActivityGraph[];
};

const HOUR_MS = 60 * 60 * 1000;

function datetimeLocalValue(date: Date): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  return [
    date.getFullYear(),
    "-",
    pad(date.getMonth() + 1),
    "-",
    pad(date.getDate()),
    "T",
    pad(date.getHours()),
    ":",
    pad(date.getMinutes()),
  ].join("");
}

function isoFromLocalInput(value: string): string {
  return new Date(value).toISOString();
}

function statusSummary(counts: Record<string, number>): string {
  const items = Object.entries(counts).sort(([left], [right]) => left.localeCompare(right));
  if (items.length === 0) return "None";
  return items.map(([status, count]) => `${status} ${count}`).join(", ");
}

function periodPreset(hours: number): { start: string; end: string } {
  const end = new Date();
  const start = new Date(end.getTime() - hours * HOUR_MS);
  return {
    start: datetimeLocalValue(start),
    end: datetimeLocalValue(end),
  };
}

export function ReportsPage() {
  const initialPeriod = useMemo(() => periodPreset(24), []);
  const [startValue, setStartValue] = useState(initialPeriod.start);
  const [endValue, setEndValue] = useState(initialPeriod.end);
  const [report, setReport] = useState<WorkflowGraphActivityReport | null>(null);
  const [selectedGraphId, setSelectedGraphId] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const selectedGraph =
    report?.graphs.find((graph) => graph.graph_id === selectedGraphId) ?? report?.graphs[0] ?? null;
  const maxActivity = Math.max(1, ...(report?.graphs.map((graph) => graph.activity_count) ?? [0]));

  async function loadReport(nextStart = startValue, nextEnd = endValue) {
    const params = new URLSearchParams({
      start: isoFromLocalInput(nextStart),
      end: isoFromLocalInput(nextEnd),
      limit: "100",
    });
    const payload = await fetchJson<WorkflowGraphActivityReport>(
      `/api/admin/reports/workflow-graph-activity?${params.toString()}`,
    );
    setReport(payload);
    setSelectedGraphId((current) =>
      current && payload.graphs.some((graph) => graph.graph_id === current)
        ? current
        : payload.graphs[0]?.graph_id ?? "",
    );
  }

  useEffect(() => {
    let cancelled = false;
    async function refresh() {
      try {
        setLoading(true);
        await loadReport(initialPeriod.start, initialPeriod.end);
        if (!cancelled) setError(null);
      } catch (reason: unknown) {
        if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void refresh();
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    try {
      setLoading(true);
      await loadReport();
      setError(null);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  }

  async function applyPreset(hours: number) {
    const next = periodPreset(hours);
    setStartValue(next.start);
    setEndValue(next.end);
    try {
      setLoading(true);
      await loadReport(next.start, next.end);
      setError(null);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="feature-page reports-page">
      <section className="feature-sidebar">
        <div className="feature-sidebar-header">
          <div>
            <div className="eyebrow">Reporting</div>
            <h1>Reports</h1>
          </div>
        </div>
        <div className="feature-list">
          <button type="button" className="feature-list-item selected">
            <div className="feature-list-title-row">
              <strong>Workflow graph activity</strong>
              <span className="status-pill">{report?.graphs.length ?? 0}</span>
            </div>
            <div className="feature-list-meta">Graph runtime activity by selected period.</div>
          </button>
        </div>
      </section>

      <section className="feature-content">
        <div className="feature-toolbar">
          <div>
            <h2>Workflow graph activity</h2>
            <p className="muted-text">
              {report
                ? `${formatTimestamp(report.period_start)} to ${formatTimestamp(report.period_end)}`
                : "Trigger, gate, and effect records grouped by workflow graph."}
            </p>
          </div>
          <div className="inline-actions compact-actions">
            <button type="button" onClick={() => void applyPreset(24)} disabled={loading}>
              24h
            </button>
            <button type="button" onClick={() => void applyPreset(24 * 7)} disabled={loading}>
              7d
            </button>
            <button type="button" onClick={() => void applyPreset(24 * 30)} disabled={loading}>
              30d
            </button>
          </div>
        </div>

        <form className="admin-toolbar report-period-form" onSubmit={(event) => void handleSubmit(event)}>
          <label className="field-stack">
            <span>Start</span>
            <input
              type="datetime-local"
              value={startValue}
              onChange={(event) => setStartValue(event.target.value)}
              required
            />
          </label>
          <label className="field-stack">
            <span>End</span>
            <input
              type="datetime-local"
              value={endValue}
              onChange={(event) => setEndValue(event.target.value)}
              required
            />
          </label>
          <div className="inline-actions compact-actions">
            <button type="submit" className="primary" disabled={loading}>
              {loading ? "Loading..." : "Run report"}
            </button>
          </div>
        </form>

        {error ? <div className="callout warning-callout">{error}</div> : null}

        <div className="admin-grid two-column report-grid">
          <article className="admin-panel">
            <div className="panel-heading">
              <div className="eyebrow">Ranked by activity</div>
              <h2>Graphs</h2>
            </div>
            <div className="table-wrap">
              <table className="admin-table report-activity-table">
                <thead>
                  <tr>
                    <th>Graph</th>
                    <th>Activity</th>
                    <th>Runs</th>
                    <th>Trigger</th>
                    <th>Gate</th>
                    <th>Effect</th>
                    <th>Latest</th>
                  </tr>
                </thead>
                <tbody>
                  {report?.graphs.map((graph) => (
                    <tr
                      key={graph.graph_id}
                      className={selectedGraph?.graph_id === graph.graph_id ? "selected" : ""}
                      onClick={() => setSelectedGraphId(graph.graph_id)}
                    >
                      <td>
                        <strong>{graph.name}</strong>
                        <div className="feature-list-meta">{graph.directory_paths.join(", ") || "root"}</div>
                        <div className="activity-bar" aria-hidden="true">
                          <span style={{ width: `${Math.max(4, (graph.activity_count / maxActivity) * 100)}%` }} />
                        </div>
                      </td>
                      <td>{graph.activity_count}</td>
                      <td>{graph.run_count}</td>
                      <td>{graph.trigger_event_count}</td>
                      <td>{graph.gate_execution_count}</td>
                      <td>{graph.effect_execution_count}</td>
                      <td>{formatTimestamp(graph.latest_activity_at)}</td>
                    </tr>
                  ))}
                  {!loading && report?.graphs.length === 0 ? (
                    <tr>
                      <td className="empty-state-cell" colSpan={7}>
                        No workflow graphs found.
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          </article>

          <article className="admin-panel detail-panel">
            <div className="panel-heading">
              <div className="eyebrow">Selected graph</div>
              <h2>{selectedGraph?.name ?? "No graph selected"}</h2>
            </div>
            {selectedGraph ? (
              <div className="panel-body report-detail">
                <div className="detail-list">
                  <div>
                    <span>Activity</span>
                    <strong>{selectedGraph.activity_count}</strong>
                  </div>
                  <div>
                    <span>Runs</span>
                    <strong>{selectedGraph.run_count}</strong>
                  </div>
                  <div>
                    <span>Nodes</span>
                    <strong>{selectedGraph.node_count}</strong>
                  </div>
                  <div>
                    <span>Latest</span>
                    <strong>{formatTimestamp(selectedGraph.latest_activity_at)}</strong>
                  </div>
                </div>
                <div className="report-meta-block">
                  <span className="section-label">Statuses</span>
                  <p>{statusSummary(selectedGraph.status_counts)}</p>
                </div>
                <div className="report-meta-block">
                  <span className="section-label">User IDs</span>
                  <p>{selectedGraph.user_ids.length > 0 ? selectedGraph.user_ids.join(", ") : "None"}</p>
                </div>
                <div className="report-meta-block">
                  <span className="section-label">Active nodes</span>
                  {selectedGraph.nodes.length > 0 ? (
                    <div className="node-activity-list">
                      {selectedGraph.nodes.map((node) => (
                        <div key={node.node_id} className="node-activity-item">
                          <div>
                            <strong>{node.display_name ?? node.node_id}</strong>
                            <span>{node.node_type ?? node.node_id}</span>
                          </div>
                          <div>
                            <strong>{node.activity_count}</strong>
                            <span>{statusSummary(node.status_counts)}</span>
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p>No activity in this period.</p>
                  )}
                </div>
              </div>
            ) : (
              <div className="empty-state">Run the report to inspect workflow graph activity.</div>
            )}
          </article>
        </div>
      </section>
    </div>
  );
}
