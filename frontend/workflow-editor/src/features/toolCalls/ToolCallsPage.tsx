import { useEffect, useMemo, useState } from "react";

import { fetchJson, formatTimestamp } from "../admin/api";
import { useAdminUi } from "../admin/AdminContext";
import { RefreshIcon } from "../admin/PageActionIcons";
import { Tooltip } from "../admin/Tooltip";
import { readAdminHashQuery } from "../admin/hash";

type AdminSectionTab =
  | "agents"
  | "approvals"
  | "configuration"
  | "issues"
  | "reports"
  | "sessions"
  | "tool-calls"
  | "users"
  | "vms"
  | "workers"
  | "workflow";

type ToolCallStatus = "success" | "denied" | "error" | "pending_approval";

type ToolCallItem = {
  tool_name: string;
  created_at: string;
  session_id?: string | null;
  directory_path?: string | null;
  duration_ms?: number | null;
  status?: string | null;
  decision?: string | null;
  role?: string | null;
  agent_role?: string | null;
  actor_user_id?: string | null;
  request_id?: string | null;
  correlation_id?: string | null;
  approval_request_id?: string | null;
  workflow_run_id?: string | null;
  trace_id?: string | null;
  vm_id?: string | null;
  vm_name?: string | null;
  agent_id?: string | null;
  error?: string | null;
  error_summary?: string | null;
  args?: unknown;
  result?: unknown;
  redacted_args?: unknown;
  redacted_result?: unknown;
  metadata?: Record<string, unknown> | null;
  related_links?: Array<Record<string, unknown>>;
  [key: string]: unknown;
};

type ToolCallsResponse = {
  items: ToolCallItem[];
  counts?: {
    total?: number | null;
    matched?: number | null;
    returned?: number | null;
    success?: number | null;
    denied?: number | null;
    error?: number | null;
    status_counts?: {
      success?: number | null;
      denied?: number | null;
      error?: number | null;
      [key: string]: number | null | undefined;
    };
  };
  limit?: number | null;
  total?: number | null;
  has_more?: boolean | null;
};

type ToolCallFilters = {
  query: string;
  sessionId: string;
  toolName: string;
  status: ToolCallStatus | "all";
  directOnly: boolean;
  createdAfter: string;
  createdBefore: string;
  limit: string;
};

type ToolCallRelation = {
  label: string;
  tab: AdminSectionTab;
  value: string;
  tooltip: string;
};

type ApprovalPayloadDifferenceEntry = {
  path: string;
  approved_value?: unknown;
  attempted_value?: unknown;
};

type ApprovalPayloadDifferenceBucket = {
  entries: ApprovalPayloadDifferenceEntry[];
  truncated: boolean;
};

type ApprovalPayloadDifferences = {
  changed: ApprovalPayloadDifferenceBucket;
  missing: ApprovalPayloadDifferenceBucket;
  extra: ApprovalPayloadDifferenceBucket;
  truncated: boolean;
};

type ApprovalPayloadDiagnostic = {
  approval_request_id?: string | null;
  status?: string | null;
  rejection_kind?: string | null;
  reason?: string | null;
  payload_match?: boolean | null;
  approved_payload_digest?: string | null;
  attempted_payload_digest?: string | null;
  payload_summary?: string | null;
  approved_payload_summary?: unknown;
  attempted_payload_summary?: unknown;
  approved_payload?: unknown;
  attempted_payload?: unknown;
  requested_payload?: unknown;
  differences: ApprovalPayloadDifferences;
};

const POLL_INTERVAL_MS = 10_000;
const DEFAULT_LIMIT = 100;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringField(item: ToolCallItem | null | undefined, ...keys: string[]): string | null {
  if (!item) {
    return null;
  }
  for (const key of keys) {
    const value = item[key];
    if (typeof value === "string" && value.trim()) {
      return value.trim();
    }
  }
  return null;
}

function toolCallRowKey(item: ToolCallItem | null | undefined, index = 0): string {
  const explicitId = stringField(item, "event_id", "call_id");
  if (explicitId) {
    return explicitId;
  }
  if (!item) {
    return "";
  }
  return [
    "tool-call",
    stringField(item, "tool_name") ?? "unknown-tool",
    stringField(item, "created_at") ?? "unknown-time",
    stringField(item, "session_id") ?? "",
    stringField(item, "request_id") ?? "",
    stringField(item, "correlation_id") ?? "",
    stringField(item, "approval_request_id") ?? "",
    stringField(item, "mcp_tool_approval_lease_id") ?? "",
    stringField(item, "workflow_run_id", "trace_id") ?? "",
    stringField(item, "directory_path") ?? "",
    stringField(item, "status", "decision") ?? "",
    item?.duration_ms == null ? "" : String(item.duration_ms),
    String(index),
  ].join("|");
}

function jsonValue(item: ToolCallItem | null | undefined, ...keys: string[]): unknown {
  if (!item) {
    return undefined;
  }
  for (const key of keys) {
    if (item[key] !== undefined) {
      return item[key];
    }
  }
  return undefined;
}

function jsonText(value: unknown, fallback = "{}"): string {
  if (value === undefined) {
    return fallback;
  }
  try {
    const text = JSON.stringify(value, null, 2);
    return text ?? fallback;
  } catch {
    return typeof value === "string" ? value : String(value);
  }
}

function textField(value: unknown): string | null {
  if (typeof value !== "string") {
    return null;
  }
  const text = value.trim();
  return text ? text : null;
}

function truncateText(value: string | null | undefined, maxLength = 120): string {
  const text = value?.trim();
  if (!text) {
    return "None";
  }
  if (text.length <= maxLength) {
    return text;
  }
  return `${text.slice(0, maxLength - 1).trimEnd()}…`;
}

function formatDurationMs(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) {
    return "n/a";
  }
  if (value < 1000) {
    return `${Math.max(0, Math.round(value))}ms`;
  }
  if (value < 60_000) {
    const seconds = value / 1000;
    const precision = seconds < 10 ? 1 : 0;
    return `${seconds.toFixed(precision)}s`;
  }
  if (value < 3_600_000) {
    return `${Math.round(value / 60_000)}m`;
  }
  return `${Math.round(value / 3_600_000)}h`;
}

function toIsoTimestamp(value: string): string | null {
  const trimmed = value.trim();
  if (!trimmed) {
    return null;
  }
  const date = new Date(trimmed);
  if (Number.isNaN(date.getTime())) {
    return null;
  }
  return date.toISOString();
}

function normalizeStatus(item: ToolCallItem): ToolCallStatus {
  const explicitStatus = (stringField(item, "status") ?? "").toLowerCase();
  if (
    explicitStatus === "success" ||
    explicitStatus === "denied" ||
    explicitStatus === "error" ||
    explicitStatus === "pending_approval"
  ) {
    return explicitStatus;
  }

  const decision = (stringField(item, "decision") ?? "").toLowerCase();
  if (decision === "pending_approval") {
    return "pending_approval";
  }
  if (decision === "denied" || decision === "rejected" || decision === "forbidden") {
    return "denied";
  }
  if (decision === "error" || decision === "failed" || decision === "failure") {
    return "error";
  }
  if (decision === "approved" || decision === "success" || decision === "allowed" || decision === "accepted") {
    return "success";
  }

  if (stringField(item, "error_summary", "error")) {
    return "error";
  }

  return "success";
}

function statusLabel(status: ToolCallStatus): string {
  switch (status) {
    case "success":
      return "Success";
    case "denied":
      return "Denied";
    case "error":
      return "Error";
    case "pending_approval":
      return "Pending approval";
  }
}

function statusClass(status: ToolCallStatus): string {
  switch (status) {
    case "success":
      return "state-pass";
    case "denied":
      return "state-fail";
    case "error":
      return "state-fail";
    case "pending_approval":
      return "state-idle";
  }
}

function statusSummary(item: ToolCallItem): string {
  const status = normalizeStatus(item);
  const decision = stringField(item, "decision")?.trim();
  if (!decision || decision.toLowerCase() === status) {
    return statusLabel(status);
  }
  return `${statusLabel(status)} (${decision})`;
}

function normalizeActor(item: ToolCallItem): string {
  const role = stringField(item, "agent_role", "role", "automation_role");
  const actor = stringField(item, "actor_user_id", "user_id", "actor_id");
  if (role && actor) {
    return `${role} • ${actor}`;
  }
  return role ?? actor ?? "Unknown";
}

function normalizeRequestId(value: string | null | undefined): string {
  if (!value) {
    return "None";
  }
  return value.length > 18 ? `${value.slice(0, 8)}…${value.slice(-8)}` : value;
}

function tabLabel(tab: AdminSectionTab): string {
  switch (tab) {
    case "agents":
      return "Agents";
    case "approvals":
      return "Approvals";
    case "configuration":
      return "Configuration";
    case "issues":
      return "Issues";
    case "reports":
      return "Reports";
    case "sessions":
      return "Sessions";
    case "tool-calls":
      return "Tool Calls";
    case "users":
      return "Users";
    case "vms":
      return "VMs";
    case "workers":
      return "Workers";
    case "workflow":
      return "Workflow";
  }
}

function usesRecentOnly(filters: ToolCallFilters): boolean {
  return !(
    filters.query.trim() ||
    filters.sessionId.trim() ||
    filters.toolName.trim() ||
    filters.status !== "all" ||
    filters.createdAfter.trim() ||
    filters.createdBefore.trim()
  );
}

function buildQuery(filters: ToolCallFilters, selectedDirectoryPath: string): string {
  const params = new URLSearchParams();
  const query = filters.query.trim();
  const sessionId = filters.sessionId.trim();
  const toolName = filters.toolName.trim();
  const limit = Number(filters.limit);

  if (query) {
    params.set("q", query);
  }
  if (selectedDirectoryPath.trim()) {
    params.set("directory_path", selectedDirectoryPath.trim());
  }
  params.set("direct_only", String(filters.directOnly));
  if (sessionId) {
    params.set("session_id", sessionId);
  }
  if (toolName) {
    params.set("tool_name", toolName);
  }
  if (filters.status !== "all") {
    params.set("status", filters.status);
  }
  const createdAfter = toIsoTimestamp(filters.createdAfter);
  if (createdAfter) {
    params.set("created_after", createdAfter);
  }
  const createdBefore = toIsoTimestamp(filters.createdBefore);
  if (createdBefore) {
    params.set("created_before", createdBefore);
  }
  if (usesRecentOnly(filters)) {
    params.set("recent_only", "true");
  }
  params.set("limit", String(Number.isInteger(limit) && limit > 0 ? limit : DEFAULT_LIMIT));

  const queryString = params.toString();
  return queryString ? `?${queryString}` : "";
}

function toolCallFiltersFromHash(): ToolCallFilters {
  const params = readAdminHashQuery();
  const directOnlyParam = params.get("direct_only");
  const statusParam = (params.get("status") ?? "").trim();
  return {
    query: params.get("q")?.trim() ?? "",
    sessionId: params.get("session_id")?.trim() ?? "",
    toolName: params.get("tool_name")?.trim() ?? "",
    status:
      statusParam === "success" ||
      statusParam === "pending_approval" ||
      statusParam === "denied" ||
      statusParam === "error"
        ? (statusParam as ToolCallFilters["status"])
        : "all",
    directOnly: directOnlyParam == null ? true : directOnlyParam.trim().toLowerCase() !== "false",
    createdAfter: "",
    createdBefore: "",
    limit: String(DEFAULT_LIMIT),
  };
}

function relationLinks(item: ToolCallItem): ToolCallRelation[] {
  const links: ToolCallRelation[] = [];
  const pushLink = (tab: AdminSectionTab, label: string, value: string | null | undefined) => {
    if (!value) {
      return;
    }
    if (links.some((link) => link.tab === tab && link.value === value)) {
      return;
    }
    links.push({
      tab,
      label,
      value,
      tooltip: `${label}: ${value}`,
    });
  };

  pushLink("sessions", "Session", stringField(item, "session_id"));
  pushLink("approvals", "Approval", stringField(item, "approval_request_id"));
  pushLink("workflow", "Workflow run", stringField(item, "workflow_run_id", "trace_id"));
  pushLink("agents", "Agent", stringField(item, "agent_id", "agent_session_id"));
  pushLink("vms", "VM", stringField(item, "vm_id", "vm_name"));

  const relatedLinks = item.related_links;
  if (Array.isArray(relatedLinks)) {
    for (const rawLink of relatedLinks) {
      if (!isRecord(rawLink)) {
        continue;
      }
      const tabValue = typeof rawLink.tab === "string" ? rawLink.tab : typeof rawLink.target === "string" ? rawLink.target : "";
      const kindValue = typeof rawLink.kind === "string" ? rawLink.kind : "";
      const tab = tabValue === "tool-calls" || tabValue === "toolCalls"
        ? "tool-calls"
        : tabValue
          ? (tabValue as AdminSectionTab)
          : kindValue === "approval_request"
            ? "approvals"
            : kindValue === "workflow_run" || kindValue === "workflow_trace"
              ? "workflow"
              : kindValue === "session"
                ? "sessions"
                : kindValue === "vm"
                  ? "vms"
                  : (kindValue as AdminSectionTab);
      if (!["agents", "approvals", "configuration", "issues", "reports", "sessions", "tool-calls", "users", "vms", "workers", "workflow"].includes(tab)) {
        continue;
      }
      const value = typeof rawLink.value === "string" ? rawLink.value : typeof rawLink.id === "string" ? rawLink.id : "";
      const label = typeof rawLink.label === "string" ? rawLink.label : tabLabel(tab);
      if (value) {
        pushLink(tab, label, value);
      }
    }
  }

  return links;
}

function relationButtonLabel(link: ToolCallRelation): string {
  return `${link.label}: ${link.value.length > 24 ? `${link.value.slice(0, 10)}…${link.value.slice(-8)}` : link.value}`;
}

function normalizeDiagnosticEntries(value: unknown): ApprovalPayloadDifferenceEntry[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.flatMap((entry) => {
    if (!isRecord(entry)) {
      return [];
    }
    const path = textField(entry.path) ?? "<root>";
    const normalized: ApprovalPayloadDifferenceEntry = { path };
    if ("approved_value" in entry) {
      normalized.approved_value = entry.approved_value;
    }
    if ("attempted_value" in entry) {
      normalized.attempted_value = entry.attempted_value;
    }
    return [normalized];
  });
}

function normalizeDiagnosticBucket(value: unknown): ApprovalPayloadDifferenceBucket {
  if (Array.isArray(value)) {
    return {
      entries: normalizeDiagnosticEntries(value),
      truncated: false,
    };
  }
  if (!isRecord(value)) {
    return {
      entries: [],
      truncated: false,
    };
  }
  return {
    entries: normalizeDiagnosticEntries(value.entries),
    truncated: Boolean(value.truncated),
  };
}

function normalizeDiagnosticDifferences(value: unknown): ApprovalPayloadDifferences {
  if (!isRecord(value)) {
    const emptyBucket = { entries: [], truncated: false };
    return {
      changed: emptyBucket,
      missing: emptyBucket,
      extra: emptyBucket,
      truncated: false,
    };
  }
  const changed = normalizeDiagnosticBucket(value.changed);
  const missing = normalizeDiagnosticBucket(value.missing);
  const extra = normalizeDiagnosticBucket(value.extra);
  return {
    changed,
    missing,
    extra,
    truncated: Boolean(value.truncated || changed.truncated || missing.truncated || extra.truncated),
  };
}

function normalizeApprovalPayloadDiagnostic(value: unknown): ApprovalPayloadDiagnostic | null {
  if (!isRecord(value)) {
    return null;
  }
  return {
    approval_request_id: textField(value.approval_request_id),
    status: textField(value.status),
    rejection_kind: textField(value.rejection_kind),
    reason: textField(value.reason),
    payload_match: typeof value.payload_match === "boolean" ? value.payload_match : null,
    approved_payload_digest: textField(value.approved_payload_digest),
    attempted_payload_digest: textField(value.attempted_payload_digest),
    payload_summary: textField(value.payload_summary),
    approved_payload_summary: value.approved_payload_summary ?? value.approved_payload ?? value.requested_payload ?? null,
    attempted_payload_summary: value.attempted_payload_summary ?? value.attempted_payload ?? null,
    approved_payload: value.approved_payload ?? null,
    attempted_payload: value.attempted_payload ?? null,
    requested_payload: value.requested_payload ?? null,
    differences: normalizeDiagnosticDifferences(value.differences),
  };
}

function approvalDiagnosticFromItem(item: ToolCallItem | null | undefined): ApprovalPayloadDiagnostic | null {
  if (!item) {
    return null;
  }
  const rawResult = isRecord(item.result) ? item.result : null;
  const rawDiagnostic = rawResult?.approval_diagnostic ?? jsonValue(item, "approval_diagnostic");
  return normalizeApprovalPayloadDiagnostic(rawDiagnostic);
}

function diagnosticMatchLabel(value: boolean | null | undefined): string {
  if (value === true) {
    return "Match";
  }
  if (value === false) {
    return "Mismatch";
  }
  return "Unknown";
}

function diagnosticMatchClass(value: boolean | null | undefined): string {
  if (value === true) {
    return "state-running";
  }
  if (value === false) {
    return "state-fail";
  }
  return "state-idle";
}

function renderDiagnosticBucket(label: string, bucket: ApprovalPayloadDifferenceBucket, emptyText: string) {
  return (
    <section className="approval-payload-diagnostic-bucket">
      <div className="approval-payload-diagnostic-bucket-header">
        <h4>{label}</h4>
        <span className="panel-meta">
          {bucket.entries.length} {bucket.entries.length === 1 ? "entry" : "entries"}
          {bucket.truncated ? " truncated" : ""}
        </span>
      </div>
      {bucket.entries.length > 0 ? (
        <div className="approval-payload-diagnostic-entry-list">
          {bucket.entries.map((entry) => (
            <div key={`${label}-${entry.path}`} className="approval-payload-diagnostic-entry">
              <code className="approval-payload-diagnostic-path">{entry.path}</code>
              <div className="approval-payload-diagnostic-value-grid">
                {"approved_value" in entry ? (
                  <div className="approval-payload-diagnostic-value-cell">
                    <span>Approved</span>
                    <pre>{jsonText(entry.approved_value)}</pre>
                  </div>
                ) : null}
                {"attempted_value" in entry ? (
                  <div className="approval-payload-diagnostic-value-cell">
                    <span>Attempted</span>
                    <pre>{jsonText(entry.attempted_value)}</pre>
                  </div>
                ) : null}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="empty-state approval-payload-diagnostic-empty">{emptyText}</div>
      )}
    </section>
  );
}

function ToolCallApprovalDiagnosticSection({
  diagnostic,
}: {
  diagnostic: ApprovalPayloadDiagnostic;
}) {
  return (
    <section className="panel approval-payload-diagnostic tool-call-approval-diagnostic">
      <div className="panel-header">
        <div>
          <h3>Approval payload diagnostic</h3>
          <p className="field-description">
            Diagnostic only: the diff explains why the payload was rejected, but it does not loosen the approval boundary.
          </p>
        </div>
        <span className={`status-pill ${diagnosticMatchClass(diagnostic.payload_match)}`}>{diagnosticMatchLabel(diagnostic.payload_match)}</span>
      </div>

      <dl className="property-grid approval-payload-diagnostic-grid">
        <div>
          <dt>Approval request</dt>
          <dd>{diagnostic.approval_request_id ?? "n/a"}</dd>
        </div>
        <div>
          <dt>Status</dt>
          <dd>{diagnostic.status ?? "n/a"}</dd>
        </div>
        <div>
          <dt>Rejection kind</dt>
          <dd>{diagnostic.rejection_kind ?? "n/a"}</dd>
        </div>
        <div>
          <dt>Reason</dt>
          <dd>{diagnostic.reason ?? "n/a"}</dd>
        </div>
        <div>
          <dt>Approved digest</dt>
          <dd>{diagnostic.approved_payload_digest ?? "n/a"}</dd>
        </div>
        <div>
          <dt>Attempted digest</dt>
          <dd>{diagnostic.attempted_payload_digest ?? "n/a"}</dd>
        </div>
      </dl>

      {diagnostic.payload_summary ? (
        <p className="muted-copy approval-payload-diagnostic-summary-note">
          Payload summary: <span>{diagnostic.payload_summary}</span>
        </p>
      ) : null}

      <div className="approval-payload-diagnostic-summaries">
        <div className="approval-payload-diagnostic-summary">
          <h4>Approved payload summary</h4>
          <pre>{jsonText(diagnostic.approved_payload_summary ?? diagnostic.approved_payload, "None")}</pre>
        </div>
        <div className="approval-payload-diagnostic-summary">
          <h4>Attempted payload summary</h4>
          <pre>{jsonText(diagnostic.attempted_payload_summary ?? diagnostic.attempted_payload ?? diagnostic.requested_payload, "None")}</pre>
        </div>
      </div>

      <div className="approval-payload-diagnostic-differences">
        {renderDiagnosticBucket("Changed fields", diagnostic.differences.changed, "No changed fields were reported.")}
        {renderDiagnosticBucket("Missing fields", diagnostic.differences.missing, "No missing fields were reported.")}
        {renderDiagnosticBucket("Extra fields", diagnostic.differences.extra, "No extra fields were reported.")}
      </div>
    </section>
  );
}

function toolCallSelectionMatchesQuery(item: ToolCallItem, query: string): boolean {
  const normalized = query.trim().toLowerCase();
  if (!normalized) {
    return false;
  }
  return [
    stringField(item, "request_id"),
    stringField(item, "correlation_id"),
    stringField(item, "approval_request_id"),
    stringField(item, "workflow_run_id"),
    stringField(item, "trace_id"),
    stringField(item, "session_id"),
    stringField(item, "tool_name"),
    stringField(item, "directory_path"),
    stringField(item, "agent_id", "agent_session_id"),
    stringField(item, "vm_id", "vm_name"),
    stringField(item, "event_id", "call_id"),
  ]
    .filter((value): value is string => Boolean(value))
    .some((value) => value.toLowerCase().includes(normalized));
}

function ToolCallJsonField({ label, value }: { label: string; value: unknown }) {
  return (
    <label className="field-stack tool-call-json-field">
      <span>{label}</span>
      <textarea rows={10} readOnly value={jsonText(value)} />
    </label>
  );
}

export function ToolCallsPage() {
  const { selectedDirectoryPath } = useAdminUi();
  const [filters, setFilters] = useState<ToolCallFilters>(() => toolCallFiltersFromHash());
  const [response, setResponse] = useState<ToolCallsResponse | null>(null);
  const [selectedCallKey, setSelectedCallKey] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const query = useMemo(() => buildQuery(filters, selectedDirectoryPath), [filters, selectedDirectoryPath]);
  const items = response?.items ?? [];
  const selectedCall = useMemo(() => {
    if (!selectedCallKey) {
      return items[0] ?? null;
    }
    return items.find((item, index) => toolCallRowKey(item, index) === selectedCallKey) ?? null;
  }, [items, selectedCallKey]);
  const relations = useMemo(() => (selectedCall ? relationLinks(selectedCall) : []), [selectedCall]);
  const approvalDiagnostic = useMemo(() => approvalDiagnosticFromItem(selectedCall), [selectedCall]);
  const counts = response?.counts;
  const statusCounts = counts?.status_counts ?? {};
  const successCount = counts?.success ?? statusCounts.success ?? 0;
  const pendingApprovalCount = counts?.status_counts?.pending_approval ?? 0;
  const deniedCount = counts?.denied ?? statusCounts.denied ?? 0;
  const errorCount = counts?.error ?? statusCounts.error ?? 0;
  const visibleTotal = counts?.matched ?? counts?.total ?? response?.total ?? items.length;
  const recentOnly = useMemo(() => usesRecentOnly(filters), [filters]);
  const hasMore = Boolean(response?.has_more);
  const showInitialLoader = loading && items.length === 0;
  const loadingMessage = recentOnly ? "Loading recent audit entries..." : "Loading audit entries...";
  const historyScopeText = recentOnly ? "recent audit entries" : "the current filters";

  useEffect(() => {
    const syncFiltersFromHash = () => {
      setFilters(toolCallFiltersFromHash());
      setSelectedCallKey("");
    };

    syncFiltersFromHash();
    window.addEventListener("hashchange", syncFiltersFromHash);
    return () => {
      window.removeEventListener("hashchange", syncFiltersFromHash);
    };
  }, []);

  async function loadToolCalls() {
    const payload = await fetchJson<ToolCallsResponse>(`/api/admin/tool-calls${query}`);
    setResponse(payload);
  }

  useEffect(() => {
    let cancelled = false;
    let requestInFlight = false;
    const controller = new AbortController();

    async function refresh() {
      if (requestInFlight) {
        return;
      }
      requestInFlight = true;
      try {
        setLoading(true);
        const payload = await fetchJson<ToolCallsResponse>(`/api/admin/tool-calls${query}`, {
          signal: controller.signal,
        });
        if (!cancelled) {
          setResponse(payload);
          setError(null);
        }
      } catch (reason) {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : String(reason));
        }
      } finally {
        requestInFlight = false;
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    void refresh();
    const intervalId = window.setInterval(() => {
      void refresh().catch(() => {});
    }, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      controller.abort();
      window.clearInterval(intervalId);
    };
  }, [query]);

  useEffect(() => {
    if (items.length === 0) {
      setSelectedCallKey("");
      return;
    }
    setSelectedCallKey((current) => {
      if (current && items.some((item, index) => toolCallRowKey(item, index) === current)) {
        return current;
      }
      if (filters.query.trim()) {
        const matchedIndex = items.findIndex((item) => toolCallSelectionMatchesQuery(item, filters.query));
        if (matchedIndex >= 0) {
          return toolCallRowKey(items[matchedIndex] ?? null, matchedIndex);
        }
      }
      return toolCallRowKey(items[0] ?? null, 0);
    });
  }, [filters.query, items, selectedCallKey]);

  function navigateToTab(tab: AdminSectionTab) {
    window.location.hash = tab;
  }

  return (
    <div className="feature-page tool-calls-page">
      <section className="feature-sidebar">
        <div className="feature-sidebar-header">
          <div>
            <div className="eyebrow">Audit trail</div>
            <h1>Tool Calls</h1>
          </div>
          <button className="admin-action-button compact" type="button" onClick={() => void loadToolCalls()}>
            <RefreshIcon />
            Refresh
          </button>
        </div>
        <div className="panel section-panel">
          <div className="feature-list-title-row">
            <strong>Directory scope</strong>
            <span className="panel-meta">{selectedDirectoryPath}</span>
          </div>
          <div className="feature-list-meta">{filters.directOnly ? "Direct matches only" : "Including descendants"}</div>
        </div>
        <label className="field-stack">
          <span>Search</span>
          <input
            value={filters.query}
            onChange={(event) => setFilters((current) => ({ ...current, query: event.target.value }))}
            placeholder="session, VM, tool, request id"
          />
        </label>
        <label className="field-stack">
          <span>Session</span>
          <input
            value={filters.sessionId}
            onChange={(event) => setFilters((current) => ({ ...current, sessionId: event.target.value }))}
            placeholder="session-123"
          />
        </label>
        <label className="field-stack">
          <span>Tool</span>
          <input
            value={filters.toolName}
            onChange={(event) => setFilters((current) => ({ ...current, toolName: event.target.value }))}
            placeholder="prompt_agent"
          />
        </label>
          <label className="field-stack">
            <span>Status</span>
            <select value={filters.status} onChange={(event) => setFilters((current) => ({ ...current, status: event.target.value as ToolCallFilters["status"] }))}>
              <option value="all">All</option>
              <option value="success">Success</option>
              <option value="pending_approval">Pending approval</option>
              <option value="denied">Denied</option>
              <option value="error">Error</option>
            </select>
          </label>
        <label className="checkbox-row">
          <input
            type="checkbox"
            checked={filters.directOnly}
            onChange={(event) => setFilters((current) => ({ ...current, directOnly: event.target.checked }))}
          />
          <span>Direct directory only</span>
        </label>
        <label className="field-stack">
          <span>Created after</span>
          <input
            type="datetime-local"
            value={filters.createdAfter}
            onChange={(event) => setFilters((current) => ({ ...current, createdAfter: event.target.value }))}
          />
        </label>
        <label className="field-stack">
          <span>Created before</span>
          <input
            type="datetime-local"
            value={filters.createdBefore}
            onChange={(event) => setFilters((current) => ({ ...current, createdBefore: event.target.value }))}
          />
        </label>
        <label className="field-stack">
          <span>Limit</span>
          <input
            type="number"
            min="1"
            step="1"
            value={filters.limit}
            onChange={(event) => setFilters((current) => ({ ...current, limit: event.target.value }))}
            placeholder={String(DEFAULT_LIMIT)}
          />
        </label>
      </section>

      <section className="feature-content">
        <div className="feature-toolbar">
          <div>
            <h2>Tool call audit log</h2>
            <p className="muted-text">
              Review redacted MCP tool invocations, inspect details, and jump to related admin views.
            </p>
          </div>
          <div className="feature-toolbar-actions">
            <span className="panel-meta">{visibleTotal} visible</span>
            <button className="admin-action-button compact" type="button" onClick={() => void loadToolCalls()}>
              <RefreshIcon />
              Refresh
            </button>
          </div>
        </div>

        {error ? <div className="callout error-callout">{error}</div> : null}

        <div className="admin-grid two-column tool-calls-grid">
          <article className="panel section-panel">
            <div className="panel-header">
              <div>
                <h3>Audit entries</h3>
                <p className="muted-text">
                  Showing {items.length} {items.length === 1 ? "entry" : "entries"} from {historyScopeText}.
                </p>
                {recentOnly && hasMore ? (
                  <p className="muted-text tool-call-history-note">
                    Older matching entries may exist. Apply a filter for an exact full-history search.
                  </p>
                ) : null}
              </div>
              <div className="tool-call-count-summary">
                {recentOnly ? <span className="status-pill state-idle">Recent entries</span> : null}
                <span className="panel-meta">
                  {successCount} success / {pendingApprovalCount} pending approval / {deniedCount} denied / {errorCount} error
                </span>
              </div>
            </div>
            {showInitialLoader ? (
              <div className="empty-state tool-call-loading-state">{loadingMessage}</div>
            ) : (
              <>
                <div className="table-wrap mobile-paired-table">
                  <table className="admin-table tool-calls-table">
                    <thead>
                      <tr>
                        <th>Timestamp</th>
                        <th>Status</th>
                        <th>Duration</th>
                        <th>Session</th>
                        <th>Role / actor</th>
                        <th>Directory</th>
                        <th>Tool</th>
                        <th>Request / correlation</th>
                        <th>Error</th>
                      </tr>
                    </thead>
                    <tbody>
                      {items.map((item, index) => {
                        const itemKey = toolCallRowKey(item, index);
                        const itemStatus = normalizeStatus(item);
                        return (
                          <tr
                            key={itemKey}
                            className={itemKey === selectedCallKey ? "selected" : ""}
                            onClick={() => setSelectedCallKey(itemKey)}
                          >
                            <td>{formatTimestamp(item.created_at)}</td>
                            <td>
                              <span className={`status-pill ${statusClass(itemStatus)}`}>{statusLabel(itemStatus)}</span>
                            </td>
                            <td>{formatDurationMs(item.duration_ms)}</td>
                            <td>{stringField(item, "session_id") ?? "None"}</td>
                            <td>{normalizeActor(item)}</td>
                            <td>{stringField(item, "directory_path") ?? "root"}</td>
                            <td>{item.tool_name}</td>
                            <td>
                              <div>{normalizeRequestId(stringField(item, "request_id"))}</div>
                              <div className="panel-meta">{normalizeRequestId(stringField(item, "correlation_id"))}</div>
                            </td>
                            <td>{truncateText(stringField(item, "error_summary", "error"), 88)}</td>
                          </tr>
                        );
                      })}
                      {!loading && items.length === 0 ? (
                        <tr>
                          <td className="empty-state-cell" colSpan={9}>
                            No tool calls matched the current filters.
                          </td>
                        </tr>
                      ) : null}
                    </tbody>
                  </table>
                </div>
                <div className="mobile-record-list tool-calls-mobile-record-list" aria-label="Tool call audit entries">
                  {items.map((item, index) => {
                    const itemKey = toolCallRowKey(item, index);
                    const itemStatus = normalizeStatus(item);
                    return (
                      <button
                        key={itemKey}
                        type="button"
                        className={`mobile-record-card${itemKey === selectedCallKey ? " selected" : ""}`}
                        aria-pressed={itemKey === selectedCallKey}
                        onClick={() => setSelectedCallKey(itemKey)}
                      >
                        <div className="mobile-record-title-row">
                          <strong>{item.tool_name}</strong>
                          <span className={`status-pill ${statusClass(itemStatus)}`}>{statusLabel(itemStatus)}</span>
                        </div>
                        <div className="mobile-record-row">
                          <span>Timestamp</span>
                          <span>{formatTimestamp(item.created_at)}</span>
                        </div>
                        <div className="mobile-record-row">
                          <span>Duration</span>
                          <span>{formatDurationMs(item.duration_ms)}</span>
                        </div>
                        <div className="mobile-record-row">
                          <span>Session</span>
                          <span>{stringField(item, "session_id") ?? "None"}</span>
                        </div>
                        <div className="mobile-record-row">
                          <span>Directory</span>
                          <span>{stringField(item, "directory_path") ?? "root"}</span>
                        </div>
                        <div className="mobile-record-row">
                          <span>Request</span>
                          <span>{normalizeRequestId(stringField(item, "request_id"))}</span>
                        </div>
                        <div className="mobile-record-row">
                          <span>Correlation</span>
                          <span>{normalizeRequestId(stringField(item, "correlation_id"))}</span>
                        </div>
                        <div className="mobile-record-row">
                          <span>Error</span>
                          <span>{truncateText(stringField(item, "error_summary", "error"), 88)}</span>
                        </div>
                      </button>
                    );
                  })}
                  {loading && items.length === 0 ? (
                    <div className="empty-state mobile-record-empty">Loading tool call audit entries...</div>
                  ) : null}
                  {!loading && items.length === 0 ? (
                    <div className="empty-state mobile-record-empty">No tool calls matched the current filters.</div>
                  ) : null}
                </div>
              </>
            )}
          </article>

          <article className="panel detail-panel tool-call-detail-panel">
            <div className="panel-header">
              <div>
                <h3>{selectedCall?.tool_name ?? "Select a tool call"}</h3>
                <p className="muted-text">
                  {selectedCall ? formatTimestamp(selectedCall.created_at) : "Choose a row to inspect redacted arguments and metadata."}
                </p>
              </div>
              {selectedCall ? (
                <span className={`status-pill ${statusClass(normalizeStatus(selectedCall))}`}>{statusSummary(selectedCall)}</span>
              ) : (
                <span className="panel-meta">{visibleTotal} total</span>
              )}
            </div>

            {selectedCall ? (
              <div className="tool-call-detail-stack">
                {relations.length > 0 ? (
                  <div className="feature-toolbar-actions compact-actions tool-call-relations">
                    {relations.map((link) => (
                      <Tooltip key={`${link.tab}-${link.value}`} content={link.tooltip}>
                        <button className="admin-action-button compact" type="button" onClick={() => navigateToTab(link.tab)}>
                          {relationButtonLabel(link)}
                        </button>
                      </Tooltip>
                    ))}
                  </div>
                ) : null}

                {stringField(selectedCall, "error_summary", "error") ? (
                  <div className="callout error-callout">{stringField(selectedCall, "error_summary", "error")}</div>
                ) : null}

                {approvalDiagnostic ? <ToolCallApprovalDiagnosticSection diagnostic={approvalDiagnostic} /> : null}

                <dl className="detail-list">
                  <div>
                    <span>Timestamp</span>
                    <strong>{formatTimestamp(selectedCall.created_at)}</strong>
                  </div>
                  <div>
                    <span>Status</span>
                    <strong>{statusSummary(selectedCall)}</strong>
                  </div>
                  <div>
                    <span>Duration</span>
                    <strong>{formatDurationMs(selectedCall.duration_ms)}</strong>
                  </div>
                  <div>
                    <span>Session</span>
                    <strong>{stringField(selectedCall, "session_id") ?? "None"}</strong>
                  </div>
                  <div>
                    <span>Role / actor</span>
                    <strong>{normalizeActor(selectedCall)}</strong>
                  </div>
                  <div>
                    <span>Directory</span>
                    <strong>{stringField(selectedCall, "directory_path") ?? selectedDirectoryPath}</strong>
                  </div>
                  <div>
                    <span>Tool</span>
                    <strong>{selectedCall.tool_name}</strong>
                  </div>
                  <div>
                    <span>Request id</span>
                    <strong>{stringField(selectedCall, "request_id") ?? "None"}</strong>
                  </div>
                  <div>
                    <span>Correlation id</span>
                    <strong>{stringField(selectedCall, "correlation_id") ?? "None"}</strong>
                  </div>
                  <div>
                    <span>Approval request</span>
                    <strong>{stringField(selectedCall, "approval_request_id") ?? "None"}</strong>
                  </div>
                  <div>
                    <span>Workflow run</span>
                    <strong>{stringField(selectedCall, "workflow_run_id") ?? stringField(selectedCall, "trace_id") ?? "None"}</strong>
                  </div>
                  <div>
                    <span>Agent / VM</span>
                    <strong>
                      {stringField(selectedCall, "agent_id", "agent_session_id") ??
                        stringField(selectedCall, "vm_id", "vm_name") ??
                        "None"}
                    </strong>
                  </div>
                </dl>

                <div className="tool-call-json-grid">
                  <ToolCallJsonField
                    label="Arguments"
                    value={jsonValue(selectedCall, "redacted_args", "args", "payload", "request_payload", "input")}
                  />
                  <ToolCallJsonField
                    label="Result"
                    value={jsonValue(selectedCall, "redacted_result", "result", "response", "output")}
                  />
                </div>

                <details className="raw-json-panel" open>
                  <summary>Normalized metadata</summary>
                  <p className="field-description">
                    The fields below are the structured values surfaced by the audit log and the UI.
                  </p>
                  <div className="tool-call-json-grid">
                    <ToolCallJsonField
                      label="Metadata"
                      value={{
                        created_at: selectedCall.created_at,
                        status: normalizeStatus(selectedCall),
                        decision: stringField(selectedCall, "decision"),
                        session_id: stringField(selectedCall, "session_id"),
                        directory_path: stringField(selectedCall, "directory_path") ?? selectedDirectoryPath,
                        duration_ms: selectedCall.duration_ms ?? null,
                        tool_name: selectedCall.tool_name,
                        request_id: stringField(selectedCall, "request_id"),
                        correlation_id: stringField(selectedCall, "correlation_id"),
                        approval_request_id: stringField(selectedCall, "approval_request_id"),
                        workflow_run_id: stringField(selectedCall, "workflow_run_id"),
                        trace_id: stringField(selectedCall, "trace_id"),
                        role: stringField(selectedCall, "role"),
                        agent_role: stringField(selectedCall, "agent_role"),
                        actor_user_id: stringField(selectedCall, "actor_user_id"),
                        vm_id: stringField(selectedCall, "vm_id"),
                        vm_name: stringField(selectedCall, "vm_name"),
                        agent_id: stringField(selectedCall, "agent_id"),
                      }}
                    />
                    <ToolCallJsonField label="Raw event data" value={jsonValue(selectedCall, "metadata", "context", "raw_metadata")} />
                  </div>
                </details>
              </div>
            ) : (
              <div className="empty-state">No tool call is selected.</div>
            )}
          </article>
        </div>
      </section>
    </div>
  );
}
