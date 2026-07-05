import { useEffect, useMemo, useState } from "react";

import { fetchJson, formatTimestamp } from "../admin/api";
import { useAdminUi } from "../admin/AdminContext";
import { Tooltip } from "../admin/Tooltip";
import { readAdminHashQuery } from "../admin/hash";
import { adminHash } from "../../adminRouting";

type SessionRecord = {
  session_id: string;
  directory_path: string;
  vm_id: string | null;
  vm_provider: string | null;
  vm_provider_id?: string | null;
  expires_at: string;
  is_expired: boolean;
  capability_profile?: string | null;
  policy_metadata?: Record<string, unknown> | null;
};

type SessionListResponse = {
  items: SessionRecord[];
  counts: {
    total: number;
    active: number;
    expired: number;
  };
  status_ids: {
    active: string[];
    expired: string[];
  };
};

type ToolVisibilityNotice = {
  kind: string;
  message: string;
  tool_name?: string | null;
};

type ToolVisibilitySessionContext = {
  session_id: string;
  directory_path: string;
  vm_id: string | null;
  vm_provider: string | null;
  expires_at: string;
  is_expired: boolean;
  capability_profile: string | null;
  policy_metadata: Record<string, unknown>;
};

type ToolVisibilityQueryContext = {
  workflow_run_id: string | null;
  mcp_tool_approval_lease_id: string | null;
};

type ToolVisibilityBindingContext = {
  state: "bound" | "mismatch" | "unknown";
  manifest_state: "current" | "stale_manifest_possible" | "unknown";
  transport_session_id: string | null;
  bound_session_id: string | null;
  last_tools_list_at: string | null;
  last_visible_tool_fingerprint: string | null;
  message: string;
};

type ToolVisibilityApprovalSettings = {
  ttl_seconds: number;
  max_uses: number | null;
  payload_mode: "exact" | "any";
};

type ToolVisibilityItem = {
  tool_name: string;
  visibility_state: "visible" | "hidden_by_policy" | "unavailable_to_session" | "unknown";
  available: boolean;
  visible: boolean;
  execution_state?: "denied_at_execution" | null;
  source?: string | null;
  reason?: string | null;
  requires_approval: boolean;
  approval_locked: boolean;
  approval_settings?: ToolVisibilityApprovalSettings | null;
  provider?: string | null;
  category?: string | null;
  label?: string | null;
  last_denied_at?: string | null;
  decision?: string | null;
  error_summary?: string | null;
  request_id?: string | null;
  approval_request_id?: string | null;
  workflow_run_id?: string | null;
  mcp_tool_approval_lease_id?: string | null;
  related_links?: Array<Record<string, unknown>>;
};

type ToolVisibilityCounts = {
  total: number;
  visible: number;
  hidden_by_policy: number;
  unavailable_to_session: number;
  denied_at_execution: number;
  unknown: number;
  requires_approval: number;
};

type ToolVisibilityResponse = {
  session_context: ToolVisibilitySessionContext;
  query_context: ToolVisibilityQueryContext;
  binding_context: ToolVisibilityBindingContext;
  tools: ToolVisibilityItem[];
  counts: ToolVisibilityCounts;
  notices: ToolVisibilityNotice[];
};

type VisibilityFilter = "all" | ToolVisibilityItem["visibility_state"] | "denied_at_execution";

type ToolVisibilityHashState = {
  sessionId: string;
  workflowRunId: string;
  mcpToolApprovalLeaseId: string;
  mcpTransportSessionId: string;
};

const REFRESH_INTERVAL_MS = 10_000;
const EMPTY_SESSIONS: SessionRecord[] = [];
const EMPTY_TOOLS: ToolVisibilityItem[] = [];
const APPROVAL_LOCKED_TOOLTIP =
  "This tool always requires approval by host policy. Operators cannot disable this approval requirement.";

function normalizeText(value: string | null | undefined): string {
  return value?.trim() ?? "";
}

function stringField(item: Record<string, unknown> | null | undefined, ...keys: string[]): string | null {
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

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hashStateFromLocation(hash = typeof window === "undefined" ? "" : window.location.hash): ToolVisibilityHashState {
  const params = readAdminHashQuery(hash);
  return {
    sessionId: normalizeText(params.get("session_id")),
    workflowRunId: normalizeText(params.get("workflow_run_id")),
    mcpToolApprovalLeaseId: normalizeText(params.get("mcp_tool_approval_lease_id")),
    mcpTransportSessionId: normalizeText(params.get("mcp_transport_session_id")),
  };
}

function hashStateToQuery(state: ToolVisibilityHashState): string {
  return adminHash("tool-visibility", {
    session_id: state.sessionId,
    workflow_run_id: state.workflowRunId,
    mcp_tool_approval_lease_id: state.mcpToolApprovalLeaseId,
    mcp_transport_session_id: state.mcpTransportSessionId,
  });
}

function stateClassForVisibility(state: ToolVisibilityItem["visibility_state"]): string {
  switch (state) {
    case "visible":
      return "state-running";
    case "hidden_by_policy":
      return "state-idle";
    case "unavailable_to_session":
      return "state-missing";
    case "unknown":
      return "state-stale";
  }
}

function stateClassForBinding(state: ToolVisibilityBindingContext["state"] | ToolVisibilityBindingContext["manifest_state"]): string {
  switch (state) {
    case "bound":
    case "current":
      return "state-running";
    case "mismatch":
      return "state-failed";
    case "stale_manifest_possible":
      return "state-stale";
    case "unknown":
      return "state-idle";
  }
}

function visibilityLabel(state: ToolVisibilityItem["visibility_state"]): string {
  switch (state) {
    case "visible":
      return "Visible";
    case "hidden_by_policy":
      return "Hidden by policy";
    case "unavailable_to_session":
      return "Unavailable to session";
    case "unknown":
      return "Unknown";
  }
}

function bindingStateLabel(state: ToolVisibilityBindingContext["state"]): string {
  switch (state) {
    case "bound":
      return "Bound";
    case "mismatch":
      return "Mismatch";
    case "unknown":
      return "Unknown";
  }
}

function manifestStateLabel(state: ToolVisibilityBindingContext["manifest_state"]): string {
  switch (state) {
    case "current":
      return "Current";
    case "stale_manifest_possible":
      return "Stale manifest possible";
    case "unknown":
      return "Unknown";
  }
}

function lastDeniedLabel(item: ToolVisibilityItem): string {
  if (!item.last_denied_at) {
    return "None";
  }
  return formatTimestamp(item.last_denied_at);
}

function denialSummary(item: ToolVisibilityItem): string {
  if (item.execution_state !== "denied_at_execution") {
    return "No denied-at-execution record.";
  }
  const pieces = [lastDeniedLabel(item)];
  if (item.decision) {
    pieces.push(item.decision);
  }
  pieces.push(item.error_summary?.trim() || "No safe error summary recorded.");
  return pieces.join(" • ");
}

function toolSearchText(item: ToolVisibilityItem): string {
  return [
    item.tool_name,
    item.label ?? "",
    item.category ?? "",
    item.provider ?? "",
    item.source ?? "",
    item.reason ?? "",
    item.decision ?? "",
    item.error_summary ?? "",
  ]
    .join(" ")
    .toLowerCase();
}

function sessionSearchText(item: SessionRecord): string {
  return [
    item.session_id,
    item.directory_path,
    item.vm_id ?? "",
    item.vm_provider ?? "",
    item.vm_provider_id ?? "",
    item.capability_profile ?? "",
    item.is_expired ? "expired" : "active",
  ]
    .join(" ")
    .toLowerCase();
}

function formatPolicyMetadata(metadata: Record<string, unknown> | null | undefined): string {
  const keys = Object.keys(metadata ?? {}).sort((left, right) => left.localeCompare(right));
  return keys.length > 0 ? keys.join(", ") : "None";
}

function summarizeApprovalSettings(settings: ToolVisibilityApprovalSettings | null | undefined): string {
  if (!settings) {
    return "None";
  }
  const maxUses = settings.max_uses == null ? "unlimited" : String(settings.max_uses);
  return `TTL ${settings.ttl_seconds}s • ${maxUses} use${settings.max_uses === 1 ? "" : "s"} • ${settings.payload_mode}`;
}

function approvalLabel(item: ToolVisibilityItem, detail = false): string {
  if (item.approval_locked) {
    return detail ? "Always requires approval" : "Locked approval";
  }
  if (item.requires_approval) {
    return "Required";
  }
  return detail ? "Not required" : "No";
}

function approvalBadge(item: ToolVisibilityItem, detail = false, options: { focusable?: boolean } = {}) {
  const label = approvalLabel(item, detail);
  const badge = (
    <span className={`status-pill${item.approval_locked ? " approval-locked-pill" : item.requires_approval ? " state-idle" : " state-running"}`}>
      {label}
    </span>
  );
  if (!item.approval_locked) {
    return badge;
  }
  return (
    <Tooltip content={APPROVAL_LOCKED_TOOLTIP} focusable={options.focusable ?? true} ariaLabel={label}>
      {badge}
    </Tooltip>
  );
}

function visibilityFilterLabel(value: VisibilityFilter): string {
  switch (value) {
    case "all":
      return "All";
    case "visible":
      return "Visible";
    case "hidden_by_policy":
      return "Hidden by policy";
    case "unavailable_to_session":
      return "Unavailable";
    case "denied_at_execution":
      return "Denied at execution";
    case "unknown":
      return "Unknown";
  }
}

function computeCounts(response: ToolVisibilityResponse | null): ToolVisibilityCounts {
  if (response?.counts) {
    return response.counts;
  }
  const counts: ToolVisibilityCounts = {
    total: 0,
    visible: 0,
    hidden_by_policy: 0,
    unavailable_to_session: 0,
    denied_at_execution: 0,
    unknown: 0,
    requires_approval: 0,
  };
  for (const tool of response?.tools ?? []) {
    counts.total += 1;
    if (tool.visibility_state === "visible") {
      counts.visible += 1;
    } else if (tool.visibility_state === "hidden_by_policy") {
      counts.hidden_by_policy += 1;
    } else if (tool.visibility_state === "unavailable_to_session") {
      counts.unavailable_to_session += 1;
    } else {
      counts.unknown += 1;
    }
    if (tool.execution_state === "denied_at_execution") {
      counts.denied_at_execution += 1;
    }
    if (tool.requires_approval) {
      counts.requires_approval += 1;
    }
  }
  return counts;
}

function SessionSummaryCard({ session }: { session: ToolVisibilitySessionContext }) {
  return (
    <article className="panel section-panel">
      <div className="panel-header">
        <h3>Session context</h3>
        <span className={`status-pill ${session.is_expired ? "state-failed" : "state-running"}`}>{session.is_expired ? "Expired" : "Active"}</span>
      </div>
      <dl className="property-grid">
        <div>
          <dt>Session</dt>
          <dd>{session.session_id}</dd>
        </div>
        <div>
          <dt>Directory</dt>
          <dd>{session.directory_path}</dd>
        </div>
        <div>
          <dt>VM</dt>
          <dd>{session.vm_id ?? "None"}</dd>
        </div>
        <div>
          <dt>Provider</dt>
          <dd>{session.vm_provider ?? "None"}</dd>
        </div>
        <div>
          <dt>Expires</dt>
          <dd>{formatTimestamp(session.expires_at)}</dd>
        </div>
        <div>
          <dt>Capability</dt>
          <dd>{session.capability_profile ?? "Missing"}</dd>
        </div>
        <div>
          <dt>Policy metadata</dt>
          <dd>{formatPolicyMetadata(session.policy_metadata)}</dd>
        </div>
      </dl>
    </article>
  );
}

function BindingSummaryCard({ queryContext, binding }: { queryContext: ToolVisibilityQueryContext; binding: ToolVisibilityBindingContext }) {
  return (
    <article className="panel section-panel">
      <div className="panel-header">
        <h3>Transport state</h3>
        <span className={`status-pill ${stateClassForBinding(binding.state)}`}>{bindingStateLabel(binding.state)}</span>
      </div>
      <dl className="property-grid">
        <div>
          <dt>Manifest</dt>
          <dd>
            <span className={`status-pill ${stateClassForBinding(binding.manifest_state)}`}>{manifestStateLabel(binding.manifest_state)}</span>
          </dd>
        </div>
        <div>
          <dt>Transport</dt>
          <dd>{binding.transport_session_id ?? "None"}</dd>
        </div>
        <div>
          <dt>Bound session</dt>
          <dd>{binding.bound_session_id ?? "None"}</dd>
        </div>
        <div>
          <dt>Last tools list</dt>
          <dd>{formatTimestamp(binding.last_tools_list_at)}</dd>
        </div>
        <div>
          <dt>Fingerprint</dt>
          <dd>{binding.last_visible_tool_fingerprint ?? "None"}</dd>
        </div>
        <div>
          <dt>Workflow run</dt>
          <dd>{queryContext.workflow_run_id ?? "None"}</dd>
        </div>
        <div>
          <dt>Approval lease</dt>
          <dd>{queryContext.mcp_tool_approval_lease_id ?? "None"}</dd>
        </div>
      </dl>
      <p className="feature-list-meta">{binding.message}</p>
    </article>
  );
}

function CountsSummaryCard({ counts, filteredCount }: { counts: ToolVisibilityCounts; filteredCount: number }) {
  return (
    <article className="panel section-panel">
      <div className="panel-header">
        <h3>Counts</h3>
        <span className="panel-meta">{filteredCount} shown</span>
      </div>
      <dl className="property-grid">
        <div>
          <dt>Total</dt>
          <dd>{counts.total}</dd>
        </div>
        <div>
          <dt>Visible</dt>
          <dd>{counts.visible}</dd>
        </div>
        <div>
          <dt>Hidden by policy</dt>
          <dd>{counts.hidden_by_policy}</dd>
        </div>
        <div>
          <dt>Unavailable</dt>
          <dd>{counts.unavailable_to_session}</dd>
        </div>
        <div>
          <dt>Denied at execution</dt>
          <dd>{counts.denied_at_execution}</dd>
        </div>
        <div>
          <dt>Requires approval</dt>
          <dd>{counts.requires_approval}</dd>
        </div>
        <div>
          <dt>Unknown</dt>
          <dd>{counts.unknown}</dd>
        </div>
      </dl>
    </article>
  );
}

export function ToolVisibilityPage() {
  const { selectedDirectoryPath, openDirectoryPicker } = useAdminUi();
  const [hashState, setHashState] = useState<ToolVisibilityHashState>(() => hashStateFromLocation());
  const [sessionsResponse, setSessionsResponse] = useState<SessionListResponse | null>(null);
  const [visibilityResponse, setVisibilityResponse] = useState<ToolVisibilityResponse | null>(null);
  const [sessionSearch, setSessionSearch] = useState("");
  const [toolSearch, setToolSearch] = useState("");
  const [visibilityFilter, setVisibilityFilter] = useState<VisibilityFilter>("all");
  const [selectedToolName, setSelectedToolName] = useState("");
  const [loadingSessions, setLoadingSessions] = useState(true);
  const [loadingVisibility, setLoadingVisibility] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const selectedSessionId = hashState.sessionId;
  const query = useMemo(() => {
    const params = new URLSearchParams();
    params.set("directory_path", selectedDirectoryPath);
    params.set("direct_only", "false");
    params.set("include_expired", "true");
    return params.toString();
  }, [selectedDirectoryPath]);

  const visibilityQuery = useMemo(() => {
    if (!selectedSessionId) {
      return "";
    }
    const params = new URLSearchParams({ session_id: selectedSessionId });
    if (hashState.workflowRunId) {
      params.set("workflow_run_id", hashState.workflowRunId);
    }
    if (hashState.mcpToolApprovalLeaseId) {
      params.set("mcp_tool_approval_lease_id", hashState.mcpToolApprovalLeaseId);
    }
    if (hashState.mcpTransportSessionId) {
      params.set("mcp_transport_session_id", hashState.mcpTransportSessionId);
    }
    return params.toString();
  }, [hashState.mcpToolApprovalLeaseId, hashState.mcpTransportSessionId, hashState.workflowRunId, selectedSessionId]);

  const sessionItems = sessionsResponse?.items ?? EMPTY_SESSIONS;
  const filteredSessions = useMemo(() => {
    const normalized = sessionSearch.trim().toLowerCase();
    if (!normalized) {
      return sessionItems;
    }
    return sessionItems.filter((item) => sessionSearchText(item).includes(normalized));
  }, [sessionItems, sessionSearch]);

  const visibilityItems = visibilityResponse?.tools ?? EMPTY_TOOLS;
  const filteredTools = useMemo(() => {
    const normalized = toolSearch.trim().toLowerCase();
    return visibilityItems.filter((item) => {
      if (visibilityFilter === "denied_at_execution") {
        if (item.execution_state !== "denied_at_execution") {
          return false;
        }
      } else if (visibilityFilter !== "all" && item.visibility_state !== visibilityFilter) {
        return false;
      }
      if (!normalized) {
        return true;
      }
      return toolSearchText(item).includes(normalized);
    });
  }, [toolSearch, visibilityFilter, visibilityItems]);

  const selectedTool = useMemo(() => {
    if (!filteredTools.length) {
      return null;
    }
    if (selectedToolName) {
      const match = filteredTools.find((item) => item.tool_name === selectedToolName);
      if (match) {
        return match;
      }
    }
    return filteredTools[0] ?? null;
  }, [filteredTools, selectedToolName]);

  const counts = computeCounts(visibilityResponse);

  useEffect(() => {
    function syncHash() {
      setHashState(hashStateFromLocation());
    }

    syncHash();
    window.addEventListener("hashchange", syncHash);
    return () => window.removeEventListener("hashchange", syncHash);
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function loadSessions() {
      try {
        setLoadingSessions(true);
        const payload = await fetchJson<SessionListResponse>(`/api/admin/sessions?${query}`);
        if (cancelled) {
          return;
        }
        setSessionsResponse(payload);
        setError(null);
      } catch (reason) {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : String(reason));
        }
      } finally {
        if (!cancelled) {
          setLoadingSessions(false);
        }
      }
    }

    void loadSessions();
    return () => {
      cancelled = true;
    };
  }, [query]);

  useEffect(() => {
    if (selectedSessionId || sessionItems.length === 0) {
      return;
    }
    window.location.hash = hashStateToQuery({
      ...hashState,
      sessionId: sessionItems[0]?.session_id ?? "",
    });
  }, [hashState, selectedSessionId, sessionItems]);

  useEffect(() => {
    if (!selectedSessionId) {
      setVisibilityResponse(null);
      setLoadingVisibility(false);
      return;
    }

    let cancelled = false;

    async function loadVisibility() {
      try {
        setLoadingVisibility(true);
        const payload = await fetchJson<ToolVisibilityResponse>(`/api/admin/tool-visibility?${visibilityQuery}`);
        if (cancelled) {
          return;
        }
        setVisibilityResponse(payload);
        setError(null);
      } catch (reason) {
        if (!cancelled) {
          setVisibilityResponse(null);
          setError(reason instanceof Error ? reason.message : String(reason));
        }
      } finally {
        if (!cancelled) {
          setLoadingVisibility(false);
        }
      }
    }

    void loadVisibility();
    const timer = window.setInterval(() => {
      void loadVisibility().catch(() => {});
    }, REFRESH_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [selectedSessionId, visibilityQuery]);

  useEffect(() => {
    if (filteredTools.length === 0) {
      setSelectedToolName("");
      return;
    }
    setSelectedToolName((current) => {
      if (current && filteredTools.some((item) => item.tool_name === current)) {
        return current;
      }
      return filteredTools[0]?.tool_name ?? "";
    });
  }, [filteredTools]);

  function selectSession(sessionId: string) {
    window.location.hash = hashStateToQuery({
      ...hashState,
      sessionId,
    });
  }

  function refresh() {
    setLoadingSessions(true);
    setLoadingVisibility(true);
    setError(null);
    void fetchJson<SessionListResponse>(`/api/admin/sessions?${query}`)
      .then((payload) => setSessionsResponse(payload))
      .catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)))
      .finally(() => setLoadingSessions(false));
    if (selectedSessionId) {
      void fetchJson<ToolVisibilityResponse>(`/api/admin/tool-visibility?${visibilityQuery}`)
        .then((payload) => setVisibilityResponse(payload))
        .catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)))
        .finally(() => setLoadingVisibility(false));
    } else {
      setLoadingVisibility(false);
    }
  }

  const selectedSession = visibilityResponse?.session_context ?? null;
  const notices = visibilityResponse?.notices ?? [];
  const showInitialLoader = loadingSessions && sessionItems.length === 0;
  const showVisibilityLoader = loadingVisibility && !visibilityResponse;
  const sessionScope = sessionsResponse?.counts ?? { total: 0, active: 0, expired: 0 };

  return (
    <div className="feature-page tool-visibility-page">
      <section className="feature-sidebar">
        <div className="feature-sidebar-header">
          <div>
            <div className="eyebrow">Inspector</div>
            <h1>Tool Visibility</h1>
          </div>
          <button className="admin-action-button compact" type="button" onClick={() => void refresh()}>
            Refresh
          </button>
        </div>

        <div className="panel section-panel">
          <div className="feature-list-title-row">
            <strong>Directory scope</strong>
            <span className="panel-meta">{selectedDirectoryPath}</span>
          </div>
          <div className="feature-list-meta">
            {sessionScope.total} sessions indexed • {sessionScope.active} active • {sessionScope.expired} expired
          </div>
          <div className="feature-list-meta">Sessions are loaded with expired records included so stale clients remain inspectable.</div>
          <div className="feature-toolbar-actions">
            <button type="button" className="admin-action-button compact" onClick={openDirectoryPicker}>
              Change directory
            </button>
          </div>
        </div>

        <label className="field-stack">
          <span>Find session</span>
          <input value={sessionSearch} onChange={(event) => setSessionSearch(event.target.value)} placeholder="session id, VM, provider, capability" />
        </label>

        <div className="feature-list session-list">
          {filteredSessions.map((item) => (
            <button
              key={item.session_id}
              type="button"
              className={`feature-list-item session-list-item${item.session_id === selectedSessionId ? " selected" : ""}`}
              onClick={() => selectSession(item.session_id)}
            >
              <div className="feature-list-title-row session-list-item-row">
                <strong>{item.session_id}</strong>
                <span className={`status-pill ${item.is_expired ? "state-failed" : "state-running"}`}>{item.is_expired ? "Expired" : "Active"}</span>
              </div>
              <div className="feature-list-meta">{item.directory_path}</div>
              <div className="feature-list-meta">
                {item.vm_id ?? item.vm_provider_id ?? "No VM"}{item.vm_provider ? ` • ${item.vm_provider}` : ""}
              </div>
              <div className="feature-list-meta">{item.capability_profile ?? "Capability profile missing"}</div>
            </button>
          ))}
          {showInitialLoader ? <div className="empty-state">Loading sessions...</div> : null}
          {!showInitialLoader && filteredSessions.length === 0 ? <div className="empty-state">No sessions matched the current search.</div> : null}
        </div>
      </section>

      <section className="feature-content">
        <div className="feature-toolbar">
          <div>
            <h2>{selectedSession?.session_id ?? "Select a session"}</h2>
            <p className="muted-text">
              Compare policy visibility, binding telemetry, and denied execution history without exposing raw tool arguments or results.
            </p>
          </div>
          <div className="feature-toolbar-actions">
            <span className="panel-meta">{counts.total} tools</span>
            <button className="admin-action-button compact" type="button" onClick={() => void refresh()}>
              Refresh
            </button>
          </div>
        </div>

        <div className="callout warning-callout">
          MCP clients can cache a tool manifest on the client side. If policy changes, reconnect or refresh the client to pick up the latest manifest.
        </div>
        {notices.map((notice) => (
          <div key={`${notice.kind}:${notice.tool_name ?? "session"}`} className="callout warning-callout">
            {notice.message}
          </div>
        ))}
        {error ? <div className="callout error-callout">{error}</div> : null}

        {showVisibilityLoader ? <div className="empty-state">Loading tool visibility...</div> : null}

        {visibilityResponse ? (
          <>
            <div className="detail-grid tool-visibility-summary-grid">
              <SessionSummaryCard session={visibilityResponse.session_context} />
              <BindingSummaryCard queryContext={visibilityResponse.query_context} binding={visibilityResponse.binding_context} />
              <CountsSummaryCard counts={counts} filteredCount={filteredTools.length} />
            </div>

            <div className="admin-grid two-column tool-visibility-grid">
              <article className="panel section-panel">
                <div className="panel-header">
                  <div>
                    <h3>Tools</h3>
                    <p className="muted-text">Filter the effective visibility table or inspect a denied-at-execution record.</p>
                  </div>
                  <div className="feature-toolbar-actions tool-visibility-filter-actions">
                    <label className="field-stack">
                      <span>Search</span>
                      <input value={toolSearch} onChange={(event) => setToolSearch(event.target.value)} placeholder="tool, category, provider, reason" />
                    </label>
                    <label className="field-stack">
                      <span>State</span>
                      <select value={visibilityFilter} onChange={(event) => setVisibilityFilter(event.target.value as VisibilityFilter)}>
                        {(["all", "visible", "hidden_by_policy", "unavailable_to_session", "denied_at_execution", "unknown"] as const).map((value) => (
                          <option key={value} value={value}>
                            {visibilityFilterLabel(value)}
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>
                </div>

                <div className="table-wrap mobile-paired-table">
                  <table className="admin-table tool-visibility-table">
                    <thead>
                      <tr>
                        <th>Tool</th>
                        <th>Category / provider</th>
                        <th>Visibility</th>
                        <th>Source</th>
                        <th>Approval</th>
                        <th>Last denied</th>
                      </tr>
                    </thead>
                    <tbody>
                      {filteredTools.map((item) => (
                        <tr
                          key={item.tool_name}
                          className={item.tool_name === selectedTool?.tool_name ? "selected" : ""}
                          onClick={() => setSelectedToolName(item.tool_name)}
                        >
                          <td>
                            <strong>{item.label ?? item.tool_name}</strong>
                            <div className="feature-list-meta">{item.tool_name}</div>
                          </td>
                          <td>
                            <div>{item.category ?? "Other"}</div>
                            <div className="feature-list-meta">{item.provider ?? "None"}</div>
                          </td>
                          <td>
                            <div className="status-pills">
                              <span className={`status-pill ${stateClassForVisibility(item.visibility_state)}`}>{visibilityLabel(item.visibility_state)}</span>
                              {item.execution_state === "denied_at_execution" ? (
                                <span className="status-pill state-failed">Denied at execution</span>
                              ) : null}
                            </div>
                          </td>
                          <td>{item.source ?? "None"}</td>
                          <td>
                            <div className="status-pills">
                              {approvalBadge(item)}
                            </div>
                          </td>
                          <td>{lastDeniedLabel(item)}</td>
                        </tr>
                      ))}
                      {!showVisibilityLoader && filteredTools.length === 0 ? (
                        <tr>
                          <td className="empty-state-cell" colSpan={6}>
                            No tools matched the current filter.
                          </td>
                        </tr>
                      ) : null}
                    </tbody>
                  </table>
                </div>

                <div className="mobile-record-list tool-visibility-mobile-record-list" aria-label="Tool visibility records">
                  {filteredTools.map((item) => (
                    <button
                      key={item.tool_name}
                      type="button"
                      className={`mobile-record-card${item.tool_name === selectedTool?.tool_name ? " selected" : ""}`}
                      aria-pressed={item.tool_name === selectedTool?.tool_name}
                      onClick={() => setSelectedToolName(item.tool_name)}
                    >
                      <div className="mobile-record-title-row">
                        <strong>{item.label ?? item.tool_name}</strong>
                        <span className={`status-pill ${stateClassForVisibility(item.visibility_state)}`}>{visibilityLabel(item.visibility_state)}</span>
                      </div>
                      <div className="mobile-record-row">
                        <span>Tool</span>
                        <span>{item.tool_name}</span>
                      </div>
                      <div className="mobile-record-row">
                        <span>Category</span>
                        <span>{item.category ?? "Other"}</span>
                      </div>
                      <div className="mobile-record-row">
                        <span>Provider</span>
                        <span>{item.provider ?? "None"}</span>
                      </div>
                      <div className="mobile-record-row">
                        <span>Source</span>
                        <span>{item.source ?? "None"}</span>
                      </div>
                      <div className="mobile-record-row">
                        <span>Approval</span>
                        <span>{approvalBadge(item, false, { focusable: false })}</span>
                      </div>
                      <div className="mobile-record-row">
                        <span>Last denied</span>
                        <span>{lastDeniedLabel(item)}</span>
                      </div>
                    </button>
                  ))}
                  {!showVisibilityLoader && filteredTools.length === 0 ? (
                    <div className="empty-state mobile-record-empty">No tools matched the current filter.</div>
                  ) : null}
                </div>
              </article>

              <article className="panel detail-panel tool-visibility-detail-panel">
                <div className="panel-header">
                  <div>
                    <h3>{selectedTool?.label ?? selectedTool?.tool_name ?? "Select a tool"}</h3>
                    <p className="muted-text">
                      {selectedTool
                        ? `${selectedTool.category ?? "Other"}${selectedTool.provider ? ` • ${selectedTool.provider}` : ""}`
                        : "Choose a row to inspect the policy reason and safe denial summary."}
                    </p>
                  </div>
                  {selectedTool ? (
                    <span className={`status-pill ${stateClassForVisibility(selectedTool.visibility_state)}`}>{visibilityLabel(selectedTool.visibility_state)}</span>
                  ) : (
                    <span className="panel-meta">{filteredTools.length} tools shown</span>
                  )}
                </div>

                {selectedTool ? (
                  <div className="stack-layout tool-visibility-detail-stack">
                    <dl className="property-grid">
                      <div>
                        <dt>Tool</dt>
                        <dd>{selectedTool.tool_name}</dd>
                      </div>
                      <div>
                        <dt>Label</dt>
                        <dd>{selectedTool.label ?? "None"}</dd>
                      </div>
                      <div>
                        <dt>Category</dt>
                        <dd>{selectedTool.category ?? "Other"}</dd>
                      </div>
                      <div>
                        <dt>Provider</dt>
                        <dd>{selectedTool.provider ?? "None"}</dd>
                      </div>
                      <div>
                        <dt>Visibility source</dt>
                        <dd>{selectedTool.source ?? "None"}</dd>
                      </div>
                      <div>
                        <dt>Approval</dt>
                        <dd>
                          <div className="status-pills">{approvalBadge(selectedTool, true)}</div>
                        </dd>
                      </div>
                    </dl>

                    <div className="detail-block">
                      <div className="feature-list-title-row">
                        <strong>Policy reason</strong>
                      </div>
                      <div className="feature-list-meta">{selectedTool.reason ?? "None"}</div>
                    </div>

                    <div className="detail-block">
                      <div className="feature-list-title-row">
                        <strong>Safe denial summary</strong>
                      </div>
                      <div className="feature-list-meta">{denialSummary(selectedTool)}</div>
                    </div>

                    {selectedTool.approval_settings ? (
                      <div className="detail-block">
                        <div className="feature-list-title-row">
                          <strong>Approval settings</strong>
                        </div>
                        <div className="feature-list-meta">{summarizeApprovalSettings(selectedTool.approval_settings)}</div>
                      </div>
                    ) : null}

                    <dl className="property-grid">
                      <div>
                        <dt>Denied at</dt>
                        <dd>{lastDeniedLabel(selectedTool)}</dd>
                      </div>
                      <div>
                        <dt>Decision</dt>
                        <dd>{selectedTool.decision ?? "None"}</dd>
                      </div>
                      <div>
                        <dt>Request id</dt>
                        <dd>{selectedTool.request_id ?? "None"}</dd>
                      </div>
                      <div>
                        <dt>Approval request</dt>
                        <dd>{selectedTool.approval_request_id ?? "None"}</dd>
                      </div>
                      <div>
                        <dt>Workflow run</dt>
                        <dd>{selectedTool.workflow_run_id ?? "None"}</dd>
                      </div>
                      <div>
                        <dt>Approval lease</dt>
                        <dd>{selectedTool.mcp_tool_approval_lease_id ?? "None"}</dd>
                      </div>
                    </dl>

                    <div className="detail-block">
                      <div className="feature-list-title-row">
                        <strong>Related links</strong>
                      </div>
                      <div className="feature-list-meta">
                        {Array.isArray(selectedTool.related_links) && selectedTool.related_links.length > 0 ? (
                          <div className="status-pills">
                            {selectedTool.related_links.map((link, index) => {
                              const label = stringField(link, "label") ?? stringField(link, "tab") ?? stringField(link, "kind") ?? `Link ${index + 1}`;
                              const value = stringField(link, "value") ?? stringField(link, "id") ?? stringField(link, "target") ?? "Unknown";
                              return (
                                <span key={`${label}:${value}:${index}`} className="permission-chip">
                                  {label}: {value}
                                </span>
                              );
                            })}
                          </div>
                        ) : (
                          "None"
                        )}
                      </div>
                    </div>
                  </div>
                ) : (
                  <div className="empty-state">Select a tool to inspect the policy reason and safe denial summary.</div>
                )}
              </article>
            </div>
          </>
        ) : null}
      </section>
    </div>
  );
}
