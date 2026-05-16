import { FormEvent, useEffect, useMemo, useState } from "react";

import { fetchJson, formatTimestamp } from "../admin/api";
import {
  CONFORMANCE_REPORT_ENDPOINT,
  type ConformanceReportKind,
  type ConformanceReportRecord,
  type ConformanceReportsPayload,
  stateClassName,
  stateLabel,
  summarizeRequirementCounts,
} from "./conformanceReports";

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

type ReportView = "workflow-graph-activity" | "conformance";

const HOUR_MS = 60 * 60 * 1000;
const CONFORMANCE_REPORT_KIND_ORDER: ConformanceReportKind[] = ["trigger", "effect"];
const CONFORMANCE_REPORT_TITLES: Record<ConformanceReportKind, string> = {
  trigger: "Trigger",
  effect: "Effect",
};

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

function truncateText(value: string | null | undefined, maxLength = 500): string {
  const text = value?.trim();
  if (!text) {
    return "None";
  }
  if (text.length <= maxLength) {
    return text;
  }
  return `${text.slice(0, maxLength - 1).trimEnd()}…`;
}

function summarizeItems(items: string[] | null | undefined, limit = 5): string {
  if (!items || items.length === 0) {
    return "None";
  }
  const visible = items.slice(0, limit);
  const remaining = items.length - visible.length;
  return remaining > 0 ? `${visible.join(", ")} +${remaining} more` : visible.join(", ");
}

function formatRequirementCount(report: ConformanceReportRecord): string {
  return `${report.requirement_count} total`;
}

export function ReportsPage() {
  const initialPeriod = useMemo(() => periodPreset(24), []);
  const [selectedReportView, setSelectedReportView] =
    useState<ReportView>("workflow-graph-activity");
  const [startValue, setStartValue] = useState(initialPeriod.start);
  const [endValue, setEndValue] = useState(initialPeriod.end);
  const [report, setReport] = useState<WorkflowGraphActivityReport | null>(null);
  const [selectedGraphId, setSelectedGraphId] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [conformanceReport, setConformanceReport] =
    useState<ConformanceReportsPayload | null>(null);
  const [conformanceSelectedKind, setConformanceSelectedKind] =
    useState<ConformanceReportKind>("trigger");
  const [conformanceLoading, setConformanceLoading] = useState(true);
  const [conformanceError, setConformanceError] = useState<string | null>(null);

  const selectedGraph =
    report?.graphs.find((graph) => graph.graph_id === selectedGraphId) ?? report?.graphs[0] ?? null;
  const maxActivity = Math.max(1, ...(report?.graphs.map((graph) => graph.activity_count) ?? [0]));
  const conformanceReports = useMemo(() => {
    const reports = conformanceReport?.reports ?? [];
    return [...reports].sort(
      (left, right) =>
        CONFORMANCE_REPORT_KIND_ORDER.indexOf(left.kind) -
        CONFORMANCE_REPORT_KIND_ORDER.indexOf(right.kind),
    );
  }, [conformanceReport]);
  const selectedConformanceReport = useMemo(() => {
    return (
      conformanceReports.find((item) => item.kind === conformanceSelectedKind) ??
      conformanceReports[0] ??
      null
    );
  }, [conformanceReports, conformanceSelectedKind]);

  async function loadWorkflowReport(nextStart = startValue, nextEnd = endValue) {
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

  async function loadConformanceReport() {
    const payload = await fetchJson<ConformanceReportsPayload>(CONFORMANCE_REPORT_ENDPOINT);
    setConformanceReport(payload);
    setConformanceSelectedKind((current) =>
      payload.reports.some((reportItem) => reportItem.kind === current)
        ? current
        : payload.reports[0]?.kind ?? "trigger",
    );
  }

  useEffect(() => {
    let cancelled = false;
    async function refresh() {
      try {
        setLoading(true);
        await loadWorkflowReport(initialPeriod.start, initialPeriod.end);
        if (!cancelled) {
          setError(null);
        }
      } catch (reason: unknown) {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : String(reason));
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }
    void refresh();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function refresh() {
      try {
        setConformanceLoading(true);
        await loadConformanceReport();
        if (!cancelled) {
          setConformanceError(null);
        }
      } catch (reason: unknown) {
        if (!cancelled) {
          setConformanceError(reason instanceof Error ? reason.message : String(reason));
        }
      } finally {
        if (!cancelled) {
          setConformanceLoading(false);
        }
      }
    }
    void refresh();
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleReportViewChange(nextView: ReportView) {
    setSelectedReportView(nextView);
    if (nextView !== "conformance") {
      return;
    }

    try {
      setConformanceLoading(true);
      await loadConformanceReport();
      setConformanceError(null);
    } catch (reason: unknown) {
      setConformanceError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setConformanceLoading(false);
    }
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    try {
      setLoading(true);
      await loadWorkflowReport();
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
      await loadWorkflowReport(next.start, next.end);
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
          <button
            type="button"
            className={`feature-list-item ${
              selectedReportView === "workflow-graph-activity" ? "selected" : ""
            }`}
            onClick={() => void handleReportViewChange("workflow-graph-activity")}
          >
            <div className="feature-list-title-row">
              <strong>Workflow graph activity</strong>
              <span className="status-pill">{report?.graphs.length ?? 0}</span>
            </div>
            <div className="feature-list-meta">Graph runtime activity by selected period.</div>
          </button>
          <button
            type="button"
            className={`feature-list-item ${selectedReportView === "conformance" ? "selected" : ""}`}
            onClick={() => void handleReportViewChange("conformance")}
          >
            <div className="feature-list-title-row">
              <strong>Conformance</strong>
              <span className="status-pill">{conformanceReport ? conformanceReports.length : 2}</span>
            </div>
            <div className="feature-list-meta">Trigger and effect traceability snapshots.</div>
          </button>
        </div>
      </section>

      <section className="feature-content">
        {selectedReportView === "workflow-graph-activity" ? (
          <>
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
                              <span
                                style={{ width: `${Math.max(4, (graph.activity_count / maxActivity) * 100)}%` }}
                              />
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
          </>
        ) : (
          <>
            <div className="feature-toolbar">
              <div>
                <h2>Conformance</h2>
                <p className="muted-text">
                  Trigger and effect report snapshots from the configured runtime directory.
                </p>
              </div>
            </div>

            {conformanceError ? <div className="callout warning-callout">{conformanceError}</div> : null}

            <div className="admin-grid two-column report-grid">
              <article className="admin-panel">
                <div className="panel-heading">
                  <div className="eyebrow">Traceability snapshots</div>
                  <h2>Reports</h2>
                </div>
                <div className="table-wrap">
                  <table className="admin-table report-conformance-table">
                    <thead>
                      <tr>
                        <th>Report</th>
                        <th>State</th>
                        <th>Generated</th>
                        <th>Status</th>
                        <th>Requirements</th>
                        <th>Missing</th>
                        <th>Tasks</th>
                      </tr>
                    </thead>
                    <tbody>
                      {conformanceReports.map((item) => (
                        <tr
                          key={item.kind}
                          className={selectedConformanceReport?.kind === item.kind ? "selected" : ""}
                          onClick={() => setConformanceSelectedKind(item.kind)}
                        >
                          <td>
                            <strong>{CONFORMANCE_REPORT_TITLES[item.kind]}</strong>
                            <div className="feature-list-meta">{item.artifact_path}</div>
                          </td>
                          <td>
                            <span className={`status-pill ${stateClassName(item.state)}`}>
                              {stateLabel(item.state)}
                            </span>
                          </td>
                          <td>{formatTimestamp(item.generated_at)}</td>
                          <td>{item.status || "n/a"}</td>
                          <td>
                            <strong>{formatRequirementCount(item)}</strong>
                            <div className="feature-list-meta">
                              {summarizeRequirementCounts(item.requirement_counts)}
                            </div>
                          </td>
                          <td>{item.missing_coverage_count}</td>
                          <td>{item.traceability_task_count}</td>
                        </tr>
                      ))}
                      {conformanceLoading && conformanceReports.length === 0 ? (
                        <tr>
                          <td className="empty-state-cell" colSpan={7}>
                            Loading conformance reports...
                          </td>
                        </tr>
                      ) : null}
                      {!conformanceLoading && conformanceReports.length === 0 ? (
                        <tr>
                          <td className="empty-state-cell" colSpan={7}>
                            No conformance reports found.
                          </td>
                        </tr>
                      ) : null}
                    </tbody>
                  </table>
                </div>
              </article>

              <article className="admin-panel detail-panel">
                <div className="panel-heading">
                  <div className="eyebrow">Selected report</div>
                  <h2>{selectedConformanceReport?.label ?? "No report selected"}</h2>
                </div>
                {selectedConformanceReport ? (
                  <div className="panel-body report-detail">
                    <div className="detail-list">
                      <div>
                        <span>State</span>
                        <strong>{stateLabel(selectedConformanceReport.state)}</strong>
                      </div>
                      <div>
                        <span>Status</span>
                        <strong>{selectedConformanceReport.status || "n/a"}</strong>
                      </div>
                      <div>
                        <span>Generated</span>
                        <strong>{formatTimestamp(selectedConformanceReport.generated_at)}</strong>
                      </div>
                      <div>
                        <span>Requirements</span>
                        <strong>{selectedConformanceReport.requirement_count}</strong>
                      </div>
                    </div>

                    <div className="report-meta-block">
                      <span className="section-label">Artifact</span>
                      <p>{selectedConformanceReport.artifact_path}</p>
                    </div>

                    <div className="report-meta-block">
                      <span className="section-label">Requirement counts</span>
                      <p>{summarizeRequirementCounts(selectedConformanceReport.requirement_counts)}</p>
                    </div>

                    <div className="report-meta-block">
                      <span className="section-label">Coverage</span>
                      <div className="detail-list">
                        <div>
                          <span>Missing requirements</span>
                          <strong>{selectedConformanceReport.missing_coverage_count}</strong>
                        </div>
                        <div>
                          <span>Traceability tasks</span>
                          <strong>{selectedConformanceReport.traceability_task_count}</strong>
                        </div>
                      </div>
                    </div>

                    <div className="report-meta-block">
                      <span className="section-label">Missing coverage by task</span>
                      {Object.keys(selectedConformanceReport.missing_coverage).length > 0 ? (
                        <div className="report-task-list">
                          {Object.entries(selectedConformanceReport.missing_coverage).map(([task, requirementIds]) => (
                            <div key={task} className="report-task-item">
                              <div className="report-task-heading">
                                <strong>{task}</strong>
                                <span>{requirementIds.length} requirement{requirementIds.length === 1 ? "" : "s"}</span>
                              </div>
                              <p>{summarizeItems(requirementIds)}</p>
                            </div>
                          ))}
                        </div>
                      ) : (
                        <p>None</p>
                      )}
                    </div>

                    {selectedConformanceReport.failing_requirements.length > 0 ? (
                      <div className="report-meta-block">
                        <span className="section-label">Failing requirements</span>
                        <div className="report-issue-list">
                          {selectedConformanceReport.failing_requirements.slice(0, 10).map((item) => (
                            <div key={item.requirement_id} className="report-issue-item">
                              <div className="report-task-heading">
                                <strong>{item.requirement_id}</strong>
                                <span>{item.status}</span>
                              </div>
                              <p>{truncateText(item.details)}</p>
                            </div>
                          ))}
                        </div>
                      </div>
                    ) : null}

                    {selectedConformanceReport.remediation_snippets.length > 0 ? (
                      <div className="report-meta-block">
                        <span className="section-label">Remediation snippets</span>
                        <div className="report-issue-list">
                          {selectedConformanceReport.remediation_snippets.slice(0, 10).map((item) => (
                            <div key={item.requirement_id} className="report-issue-item">
                              <div className="report-task-heading">
                                <strong>{item.requirement_id}</strong>
                                <span>{item.status}</span>
                              </div>
                              <p>{truncateText(item.remediation)}</p>
                            </div>
                          ))}
                        </div>
                      </div>
                    ) : null}

                    {selectedConformanceReport.guidance ? (
                      <div className="report-meta-block">
                        <span className="section-label">Guidance</span>
                        <pre className="report-guidance">{truncateText(selectedConformanceReport.guidance)}</pre>
                      </div>
                    ) : null}

                    {selectedConformanceReport.error ? (
                      <div className="report-meta-block">
                        <span className="section-label">Error</span>
                        <p>{truncateText(selectedConformanceReport.error)}</p>
                      </div>
                    ) : null}
                  </div>
                ) : (
                  <div className="empty-state">
                    {conformanceLoading ? "Loading conformance reports..." : "Select a report to inspect details."}
                  </div>
                )}
              </article>
            </div>
          </>
        )}
      </section>
    </div>
  );
}
