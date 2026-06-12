import { FormEvent, useEffect, useMemo, useState } from "react";

import { fetchJson, formatTimestamp } from "../admin/api";
import { RefreshIcon } from "../admin/PageActionIcons";
import {
  CONFORMANCE_REPORT_ENDPOINT,
  conformanceReportRunEndpoint,
  type ConformanceReportKind,
  type ConformanceReportRecord,
  type ConformanceReportRunPayload,
  type ConformanceReportsPayload,
  stateClassName,
  stateLabel,
  summarizeRequirementCounts,
} from "./conformanceReports";
import {
  stateClassName as workflowFailureClusterStateClassName,
  stateLabel as workflowFailureClusterStateLabel,
  selectWorkflowFailureCluster,
  workflowFailureClusterNodeType,
  workflowFailureClusterRepresentativeRunHref,
  workflowFailureClusterRepresentativeRunLabel,
  WORKFLOW_FAILURE_CLUSTERS_REPORT_ENDPOINT,
  type WorkflowFailureClusterReportPayload,
} from "./workflowFailureClusters";

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

type ReportView = "workflow-graph-activity" | "workflow-failure-clusters" | "conformance";

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
  const [failureClustersReport, setFailureClustersReport] =
    useState<WorkflowFailureClusterReportPayload | null>(null);
  const [selectedFailureClusterId, setSelectedFailureClusterId] = useState<string>("");
  const [failureClustersLoading, setFailureClustersLoading] = useState(false);
  const [failureClustersError, setFailureClustersError] = useState<string | null>(null);
  const [conformanceReport, setConformanceReport] =
    useState<ConformanceReportsPayload | null>(null);
  const [conformanceSelectedKind, setConformanceSelectedKind] =
    useState<ConformanceReportKind>("trigger");
  const [conformanceLoading, setConformanceLoading] = useState(true);
  const [conformanceError, setConformanceError] = useState<string | null>(null);
  const [conformanceRunKind, setConformanceRunKind] = useState<ConformanceReportKind | null>(null);

  const selectedGraph =
    report?.graphs.find((graph) => graph.graph_id === selectedGraphId) ?? report?.graphs[0] ?? null;
  const selectedFailureCluster = useMemo(() => {
    return selectWorkflowFailureCluster(
      failureClustersReport?.clusters ?? [],
      selectedFailureClusterId,
    );
  }, [failureClustersReport, selectedFailureClusterId]);
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

  async function loadFailureClustersReport(nextStart = startValue, nextEnd = endValue) {
    const params = new URLSearchParams({
      start: isoFromLocalInput(nextStart),
      end: isoFromLocalInput(nextEnd),
      limit: "100",
    });
    const payload = await fetchJson<WorkflowFailureClusterReportPayload>(
      `${WORKFLOW_FAILURE_CLUSTERS_REPORT_ENDPOINT}?${params.toString()}`,
    );
    setFailureClustersReport(payload);
    setSelectedFailureClusterId((current) =>
      current && payload.clusters.some((cluster) => cluster.cluster_id === current)
        ? current
        : payload.clusters[0]?.cluster_id ?? "",
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
    if (nextView === "workflow-graph-activity") {
      return;
    }

    if (nextView === "conformance") {
      try {
        setConformanceLoading(true);
        await loadConformanceReport();
        setConformanceError(null);
      } catch (reason: unknown) {
        setConformanceError(reason instanceof Error ? reason.message : String(reason));
      } finally {
        setConformanceLoading(false);
      }
      return;
    }

    try {
      setFailureClustersLoading(true);
      await loadFailureClustersReport();
      setFailureClustersError(null);
    } catch (reason: unknown) {
      setFailureClustersError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setFailureClustersLoading(false);
    }
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    try {
      if (selectedReportView === "workflow-failure-clusters") {
        setFailureClustersLoading(true);
        await loadFailureClustersReport();
        setFailureClustersError(null);
      } else {
        setLoading(true);
        await loadWorkflowReport();
        setError(null);
      }
    } catch (reason: unknown) {
      if (selectedReportView === "workflow-failure-clusters") {
        setFailureClustersError(reason instanceof Error ? reason.message : String(reason));
      } else {
        setError(reason instanceof Error ? reason.message : String(reason));
      }
    } finally {
      if (selectedReportView === "workflow-failure-clusters") {
        setFailureClustersLoading(false);
      } else {
        setLoading(false);
      }
    }
  }

  async function applyPreset(hours: number) {
    const next = periodPreset(hours);
    setStartValue(next.start);
    setEndValue(next.end);
    try {
      if (selectedReportView === "workflow-failure-clusters") {
        setFailureClustersLoading(true);
        await loadFailureClustersReport(next.start, next.end);
        setFailureClustersError(null);
      } else {
        setLoading(true);
        await loadWorkflowReport(next.start, next.end);
        setError(null);
      }
    } catch (reason: unknown) {
      if (selectedReportView === "workflow-failure-clusters") {
        setFailureClustersError(reason instanceof Error ? reason.message : String(reason));
      } else {
        setError(reason instanceof Error ? reason.message : String(reason));
      }
    } finally {
      if (selectedReportView === "workflow-failure-clusters") {
        setFailureClustersLoading(false);
      } else {
        setLoading(false);
      }
    }
  }

  async function runSelectedConformanceReport() {
    if (!selectedConformanceReport) {
      return;
    }
    const kind = selectedConformanceReport.kind;
    try {
      setConformanceRunKind(kind);
      const payload = await fetchJson<ConformanceReportRunPayload>(
        conformanceReportRunEndpoint(kind),
        { method: "POST" },
      );
      setConformanceReport(payload.reports);
      setConformanceSelectedKind(kind);
      setConformanceError(
        payload.exit_code === 0
          ? null
          : `${CONFORMANCE_REPORT_TITLES[kind]} conformance suite finished with exit code ${payload.exit_code}.`,
      );
    } catch (reason: unknown) {
      setConformanceError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setConformanceRunKind(null);
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
            className={`feature-list-item ${
              selectedReportView === "workflow-failure-clusters" ? "selected" : ""
            }`}
            onClick={() => void handleReportViewChange("workflow-failure-clusters")}
          >
            <div className="feature-list-title-row">
              <strong>Failure clusters</strong>
              <span className="status-pill">{failureClustersReport?.clusters.length ?? 0}</span>
            </div>
            <div className="feature-list-meta">Repeated workflow failures grouped by signature.</div>
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
                <button className="admin-action-button compact" type="button" onClick={() => void applyPreset(24)} disabled={loading}>
                  24h
                </button>
                <button className="admin-action-button compact" type="button" onClick={() => void applyPreset(24 * 7)} disabled={loading}>
                  7d
                </button>
                <button className="admin-action-button compact" type="button" onClick={() => void applyPreset(24 * 30)} disabled={loading}>
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
                <button type="submit" className="admin-action-button primary compact" disabled={loading}>
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
                <div className="table-wrap mobile-paired-table">
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
                <div className="mobile-record-list report-activity-mobile-record-list" aria-label="Workflow graph activity">
                  {report?.graphs.map((graph) => (
                    <button
                      key={graph.graph_id}
                      type="button"
                      className={`mobile-record-card${selectedGraph?.graph_id === graph.graph_id ? " selected" : ""}`}
                      aria-pressed={selectedGraph?.graph_id === graph.graph_id}
                      onClick={() => setSelectedGraphId(graph.graph_id)}
                    >
                      <div className="mobile-record-title-row">
                        <strong>{graph.name}</strong>
                      </div>
                      <div className="feature-list-meta">{graph.directory_paths.join(", ") || "root"}</div>
                      <div className="activity-bar" aria-hidden="true">
                        <span
                          style={{ width: `${Math.max(4, (graph.activity_count / maxActivity) * 100)}%` }}
                        />
                      </div>
                      <div className="mobile-record-row">
                        <span>Activity</span>
                        <span>{graph.activity_count}</span>
                      </div>
                      <div className="mobile-record-row">
                        <span>Runs</span>
                        <span>{graph.run_count}</span>
                      </div>
                      <div className="mobile-record-row">
                        <span>Trigger</span>
                        <span>{graph.trigger_event_count}</span>
                      </div>
                      <div className="mobile-record-row">
                        <span>Gate</span>
                        <span>{graph.gate_execution_count}</span>
                      </div>
                      <div className="mobile-record-row">
                        <span>Effect</span>
                        <span>{graph.effect_execution_count}</span>
                      </div>
                      <div className="mobile-record-row">
                        <span>Latest</span>
                        <span>{formatTimestamp(graph.latest_activity_at)}</span>
                      </div>
                    </button>
                  ))}
                  {loading && !report ? (
                    <div className="empty-state mobile-record-empty">Loading workflow graph activity...</div>
                  ) : null}
                  {!loading && (report?.graphs.length ?? 0) === 0 ? (
                    <div className="empty-state mobile-record-empty">No workflow graphs found.</div>
                  ) : null}
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
        ) : selectedReportView === "workflow-failure-clusters" ? (
          <>
            <div className="feature-toolbar">
              <div>
                <h2>Workflow failure clusters</h2>
                <p className="muted-text">
                  {failureClustersReport
                    ? `${formatTimestamp(failureClustersReport.period_start)} to ${formatTimestamp(failureClustersReport.period_end)}`
                    : "Repeated stored workflow failures grouped by normalized signature. These are triage signals, not proof of root cause."}
                </p>
              </div>
              <div className="inline-actions compact-actions">
                <button
                  className="admin-action-button compact"
                  type="button"
                  onClick={() => void applyPreset(24)}
                  disabled={failureClustersLoading}
                >
                  24h
                </button>
                <button
                  className="admin-action-button compact"
                  type="button"
                  onClick={() => void applyPreset(24 * 7)}
                  disabled={failureClustersLoading}
                >
                  7d
                </button>
                <button
                  className="admin-action-button compact"
                  type="button"
                  onClick={() => void applyPreset(24 * 30)}
                  disabled={failureClustersLoading}
                >
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
                <button
                  type="submit"
                  className="admin-action-button primary compact"
                  disabled={failureClustersLoading}
                >
                  {failureClustersLoading ? "Loading..." : "Run report"}
                </button>
              </div>
            </form>

            {failureClustersError ? <div className="callout warning-callout">{failureClustersError}</div> : null}

            <div className="admin-grid two-column report-grid">
              <article className="admin-panel">
                <div className="panel-heading">
                  <div className="eyebrow">Ranked by state and recency</div>
                  <h2>Clusters</h2>
                </div>
                <div className="table-wrap mobile-paired-table">
                  <table className="admin-table report-failure-clusters-table">
                    <thead>
                      <tr>
                        <th>State</th>
                        <th>Graph</th>
                        <th>Node</th>
                        <th>Count</th>
                        <th>First seen</th>
                        <th>Last seen</th>
                        <th>Summary</th>
                        <th>Sessions</th>
                        <th>PRs</th>
                        <th>ADO</th>
                        <th>Representative run</th>
                      </tr>
                    </thead>
                    <tbody>
                      {failureClustersReport?.clusters.map((cluster) => {
                        const representativeExample = cluster.examples[0] ?? null;
                        const representativeHref = representativeExample
                          ? workflowFailureClusterRepresentativeRunHref(representativeExample)
                          : null;
                        return (
                          <tr
                            key={cluster.cluster_id}
                            className={selectedFailureCluster?.cluster_id === cluster.cluster_id ? "selected" : ""}
                            onClick={() => setSelectedFailureClusterId(cluster.cluster_id)}
                          >
                            <td>
                              <span className={`status-pill ${workflowFailureClusterStateClassName(cluster.state)}`}>
                                {workflowFailureClusterStateLabel(cluster.state)}
                              </span>
                            </td>
                            <td>
                              <strong>{cluster.graph_name ?? "Unassigned"}</strong>
                              <div className="feature-list-meta">{cluster.directory_path ?? "root"}</div>
                              {cluster.graph_description ? (
                                <div className="feature-list-meta">{truncateText(cluster.graph_description, 120)}</div>
                              ) : null}
                            </td>
                            <td>
                              <strong>{cluster.node_display_name ?? cluster.node_id ?? "Unknown node"}</strong>
                              <div className="feature-list-meta">
                                {cluster.source_kind} · {workflowFailureClusterNodeType(cluster) ?? "n/a"}
                              </div>
                            </td>
                            <td>{cluster.count}</td>
                            <td>{formatTimestamp(cluster.first_seen_at)}</td>
                            <td>{formatTimestamp(cluster.last_seen_at)}</td>
                            <td>{truncateText(cluster.latest_error_summary ?? cluster.error_signature, 140)}</td>
                            <td>{cluster.session_ids.length}</td>
                            <td>{cluster.pr_ids.length}</td>
                            <td>{cluster.ado_item_ids.length}</td>
                            <td>
                              {representativeHref && representativeExample ? (
                                <a href={representativeHref}>{workflowFailureClusterRepresentativeRunLabel(representativeExample)}</a>
                              ) : (
                                "None"
                              )}
                              {representativeExample ? (
                                <div className="feature-list-meta">
                                  {formatTimestamp(representativeExample.occurred_at)}
                                </div>
                              ) : null}
                            </td>
                          </tr>
                        );
                      })}
                      {failureClustersLoading && (failureClustersReport?.clusters.length ?? 0) === 0 ? (
                        <tr>
                          <td className="empty-state-cell" colSpan={11}>
                            Loading failure clusters...
                          </td>
                        </tr>
                      ) : null}
                      {!failureClustersLoading && (failureClustersReport?.clusters.length ?? 0) === 0 ? (
                        <tr>
                          <td className="empty-state-cell" colSpan={11}>
                            No workflow failure clusters found.
                          </td>
                        </tr>
                      ) : null}
                    </tbody>
                  </table>
                </div>
                <div
                  className="mobile-record-list report-failure-clusters-mobile-record-list"
                  aria-label="Workflow failure clusters"
                >
                  {failureClustersReport?.clusters.map((cluster) => {
                    const representativeExample = cluster.examples[0] ?? null;
                    return (
                      <button
                        key={cluster.cluster_id}
                        type="button"
                        className={`mobile-record-card${
                          selectedFailureCluster?.cluster_id === cluster.cluster_id ? " selected" : ""
                        }`}
                        aria-pressed={selectedFailureCluster?.cluster_id === cluster.cluster_id}
                        onClick={() => setSelectedFailureClusterId(cluster.cluster_id)}
                      >
                        <div className="mobile-record-title-row">
                          <strong>{cluster.graph_name ?? "Unassigned"}</strong>
                          <span className={`status-pill ${workflowFailureClusterStateClassName(cluster.state)}`}>
                            {workflowFailureClusterStateLabel(cluster.state)}
                          </span>
                        </div>
                        <div className="feature-list-meta">{cluster.directory_path ?? "root"}</div>
                        <div className="feature-list-meta">
                          {cluster.node_display_name ?? cluster.node_id ?? "Unknown node"}
                          {" · "}
                          {workflowFailureClusterNodeType(cluster) ?? "n/a"}
                        </div>
                        <div className="mobile-record-row">
                          <span>Count</span>
                          <span>{cluster.count}</span>
                        </div>
                        <div className="mobile-record-row">
                          <span>First seen</span>
                          <span>{formatTimestamp(cluster.first_seen_at)}</span>
                        </div>
                        <div className="mobile-record-row">
                          <span>Last seen</span>
                          <span>{formatTimestamp(cluster.last_seen_at)}</span>
                        </div>
                        <div className="mobile-record-row">
                          <span>Summary</span>
                          <span>{truncateText(cluster.latest_error_summary ?? cluster.error_signature, 120)}</span>
                        </div>
                        <div className="mobile-record-row">
                          <span>Sessions</span>
                          <span>{cluster.session_ids.length}</span>
                        </div>
                        <div className="mobile-record-row">
                          <span>PRs</span>
                          <span>{cluster.pr_ids.length}</span>
                        </div>
                        <div className="mobile-record-row">
                          <span>ADO</span>
                          <span>{cluster.ado_item_ids.length}</span>
                        </div>
                        <div className="mobile-record-row">
                          <span>Run</span>
                          <span>{representativeExample ? workflowFailureClusterRepresentativeRunLabel(representativeExample) : "None"}</span>
                        </div>
                      </button>
                    );
                  })}
                  {failureClustersLoading && (failureClustersReport?.clusters.length ?? 0) === 0 ? (
                    <div className="empty-state mobile-record-empty">Loading failure clusters...</div>
                  ) : null}
                  {!failureClustersLoading && (failureClustersReport?.clusters.length ?? 0) === 0 ? (
                    <div className="empty-state mobile-record-empty">No workflow failure clusters found.</div>
                  ) : null}
                </div>
              </article>

              <article className="admin-panel detail-panel">
                <div className="panel-heading">
                  <div>
                    <div className="eyebrow">Selected cluster</div>
                    <h2>{selectedFailureCluster?.graph_name ?? "No cluster selected"}</h2>
                  </div>
                </div>
                {selectedFailureCluster ? (
                  <div className="panel-body report-detail">
                    <div className="callout warning-callout">
                      Clusters are triage signals from stored workflow activity. Review representative run
                      detail before remediation.
                    </div>

                    <div className="detail-list">
                      <div>
                        <span>State</span>
                        <strong>{workflowFailureClusterStateLabel(selectedFailureCluster.state)}</strong>
                      </div>
                      <div>
                        <span>Count</span>
                        <strong>{selectedFailureCluster.count}</strong>
                      </div>
                      <div>
                        <span>First seen</span>
                        <strong>{formatTimestamp(selectedFailureCluster.first_seen_at)}</strong>
                      </div>
                      <div>
                        <span>Last seen</span>
                        <strong>{formatTimestamp(selectedFailureCluster.last_seen_at)}</strong>
                      </div>
                    </div>

                    <div className="report-meta-block">
                      <span className="section-label">Scope</span>
                      <p>
                        {selectedFailureCluster.graph_name ?? "Unassigned"}{" "}
                        {selectedFailureCluster.directory_path ? `(${selectedFailureCluster.directory_path})` : ""}
                      </p>
                      <p>
                        {selectedFailureCluster.node_display_name ?? selectedFailureCluster.node_id ?? "Unknown node"}{" "}
                        {workflowFailureClusterNodeType(selectedFailureCluster)
                          ? `· ${workflowFailureClusterNodeType(selectedFailureCluster)}`
                          : ""}
                      </p>
                      {selectedFailureCluster.graph_description ? (
                        <p>{truncateText(selectedFailureCluster.graph_description, 240)}</p>
                      ) : null}
                    </div>

                    <div className="report-meta-block">
                      <span className="section-label">Signature</span>
                      <pre className="report-guidance">{selectedFailureCluster.error_signature}</pre>
                      <p className="feature-list-meta">Signature key: {selectedFailureCluster.error_signature_key}</p>
                    </div>

                    <div className="report-meta-block">
                      <span className="section-label">Latest summary</span>
                      <p>{truncateText(selectedFailureCluster.latest_error_summary, 300)}</p>
                    </div>

                    <div className="report-meta-block">
                      <span className="section-label">Linked IDs</span>
                      <div className="detail-list">
                        <div>
                          <span>Sessions</span>
                          <strong>{selectedFailureCluster.session_ids.length}</strong>
                        </div>
                        <div>
                          <span>PRs</span>
                          <strong>{selectedFailureCluster.pr_ids.length}</strong>
                        </div>
                        <div>
                          <span>ADO items</span>
                          <strong>{selectedFailureCluster.ado_item_ids.length}</strong>
                        </div>
                        <div>
                          <span>Issues</span>
                          <strong>{selectedFailureCluster.issue_ids.length}</strong>
                        </div>
                        <div>
                          <span>Artifacts</span>
                          <strong>{selectedFailureCluster.artifact_ids.length}</strong>
                        </div>
                      </div>
                      <p>Sessions: {summarizeItems(selectedFailureCluster.session_ids)}</p>
                      <p>PRs: {summarizeItems(selectedFailureCluster.pr_ids)}</p>
                      <p>ADO items: {summarizeItems(selectedFailureCluster.ado_item_ids)}</p>
                      <p>Issues: {summarizeItems(selectedFailureCluster.issue_ids)}</p>
                      <p>Artifacts: {summarizeItems(selectedFailureCluster.artifact_ids)}</p>
                    </div>

                    <div className="report-meta-block">
                      <span className="section-label">Representative IDs</span>
                      <p>Events: {summarizeItems(selectedFailureCluster.event_ids)}</p>
                      <p>Traces: {summarizeItems(selectedFailureCluster.trace_ids)}</p>
                    </div>

                    <div className="report-meta-block">
                      <span className="section-label">Examples</span>
                      {selectedFailureCluster.examples.length > 0 ? (
                        <div className="report-task-list">
                          {selectedFailureCluster.examples.map((example, index) => {
                            const exampleHref = workflowFailureClusterRepresentativeRunHref(example);
                            const exampleBody = (
                              <>
                                <div className="report-task-heading">
                                  <strong>{workflowFailureClusterRepresentativeRunLabel(example)}</strong>
                                  <span>{example.run_state ?? "n/a"}</span>
                                </div>
                                <p>{truncateText(example.error_summary, 220)}</p>
                                <div className="feature-list-meta">
                                  {[
                                    formatTimestamp(example.occurred_at),
                                    example.api_run_detail_href ?? "No API detail",
                                  ].join(" · ")}
                                </div>
                                <p>Artifacts: {summarizeItems(example.artifact_ids)}</p>
                              </>
                            );
                            return exampleHref ? (
                              <a
                                key={`${example.event_id ?? example.trace_id ?? index}-${index}`}
                                className="report-task-item"
                                href={exampleHref}
                              >
                                {exampleBody}
                              </a>
                            ) : (
                              <div key={`${example.event_id ?? example.trace_id ?? index}-${index}`} className="report-task-item">
                                {exampleBody}
                              </div>
                            );
                          })}
                        </div>
                      ) : (
                        <p>No examples in this period.</p>
                      )}
                    </div>
                  </div>
                ) : (
                  <div className="empty-state">
                    {failureClustersLoading
                      ? "Loading failure clusters..."
                      : "Run the report to inspect workflow failure clusters."}
                  </div>
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
                <div className="table-wrap mobile-paired-table">
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
                <div className="mobile-record-list report-conformance-mobile-record-list" aria-label="Conformance reports">
                  {conformanceReports.map((item) => (
                    <button
                      key={item.kind}
                      type="button"
                      className={`mobile-record-card${selectedConformanceReport?.kind === item.kind ? " selected" : ""}`}
                      aria-pressed={selectedConformanceReport?.kind === item.kind}
                      onClick={() => setConformanceSelectedKind(item.kind)}
                    >
                      <div className="mobile-record-title-row">
                        <strong>{CONFORMANCE_REPORT_TITLES[item.kind]}</strong>
                        <span className={`status-pill ${stateClassName(item.state)}`}>
                          {stateLabel(item.state)}
                        </span>
                      </div>
                      <div className="feature-list-meta">{item.artifact_path}</div>
                      <div className="mobile-record-row">
                        <span>Generated</span>
                        <span>{formatTimestamp(item.generated_at)}</span>
                      </div>
                      <div className="mobile-record-row">
                        <span>Updated</span>
                        <span>{formatTimestamp(conformanceReport?.generated_at)}</span>
                      </div>
                      <div className="mobile-record-row">
                        <span>Requirements</span>
                        <span>{formatRequirementCount(item)}</span>
                      </div>
                      <div className="mobile-record-row">
                        <span>State</span>
                        <span>{stateLabel(item.state)}</span>
                      </div>
                      <div className="mobile-record-row">
                        <span>Summary</span>
                        <span>{summarizeRequirementCounts(item.requirement_counts)}</span>
                      </div>
                    </button>
                  ))}
                  {conformanceLoading && conformanceReports.length === 0 ? (
                    <div className="empty-state mobile-record-empty">Loading conformance reports...</div>
                  ) : null}
                  {!conformanceLoading && conformanceReports.length === 0 ? (
                    <div className="empty-state mobile-record-empty">No conformance reports found.</div>
                  ) : null}
                </div>
              </article>

              <article className="admin-panel detail-panel">
                <div className="panel-heading">
                  <div>
                    <div className="eyebrow">Selected report</div>
                    <h2>{selectedConformanceReport?.label ?? "No report selected"}</h2>
                  </div>
                  {selectedConformanceReport ? (
                    <div className="feature-toolbar-actions report-panel-actions">
                      <button
                        type="button"
                        className="admin-action-button primary compact"
                        onClick={() => void runSelectedConformanceReport()}
                        disabled={conformanceLoading || conformanceRunKind !== null}
                      >
                        <RefreshIcon />
                        {conformanceRunKind === selectedConformanceReport.kind ? "Running..." : "Run report"}
                      </button>
                    </div>
                  ) : null}
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
