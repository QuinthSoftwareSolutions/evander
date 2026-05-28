import { useEffect, useMemo, useState } from "react";

import { fetchJson, formatRelativeSeconds, formatTimestamp } from "../admin/api";
import { useAdminUi } from "../admin/AdminContext";
import { Tooltip } from "../admin/Tooltip";
import { hashQueryParam } from "../../adminRouting";

type ApprovalStatus = "pending" | "approved" | "denied" | "expired";

type ApprovalItem = {
  request_id: string;
  session_id: string;
  action_type: string;
  tool_name: string;
  payload_digest: string;
  human_summary: string;
  intended_effect?: string | null;
  directory_path: string;
  vm_id?: string | null;
  status: ApprovalStatus;
  created_at: string;
  expires_at: string;
  approver?: string | null;
  decision_at?: string | null;
  decision_reason?: string | null;
  payload: Record<string, unknown>;
  decision_history: Array<{
    status: ApprovalStatus;
    approver?: string | null;
    reason?: string | null;
    decided_at: string;
  }>;
};

type ApprovalHealth = {
  total: number;
  pending: number;
  approved: number;
  denied: number;
  expired: number;
  oldest_pending_age_seconds?: number | null;
};

type ApprovalIndexResponse = {
  items: ApprovalItem[];
  health: ApprovalHealth;
};

type ApprovalDetailResponse = {
  request: ApprovalItem;
  redacted_payload: Record<string, unknown>;
  audit_context: Array<Record<string, unknown>>;
  age_seconds: number;
  expires_in_seconds: number;
};

type Filters = {
  status: string;
  directOnly: boolean;
  sessionId: string;
  vmId: string;
  toolName: string;
  minAgeMinutes: string;
};

const POLL_INTERVAL_MS = 10000;

function toQuery(filters: Filters, selectedDirectoryPath: string): string {
  const params = new URLSearchParams();
  if (filters.status) params.set("statuses", filters.status);
  if (selectedDirectoryPath.trim()) params.set("directory_path", selectedDirectoryPath.trim());
  params.set("direct_only", String(filters.directOnly));
  if (filters.sessionId.trim()) params.set("session_id", filters.sessionId.trim());
  if (filters.vmId.trim()) params.set("vm_id", filters.vmId.trim());
  if (filters.toolName.trim()) params.set("tool_name", filters.toolName.trim());
  if (filters.minAgeMinutes.trim()) {
    const minAgeMinutes = Number(filters.minAgeMinutes);
    if (Number.isFinite(minAgeMinutes) && minAgeMinutes >= 0) {
      params.set("min_age_seconds", String(minAgeMinutes * 60));
    }
  }
  const query = params.toString();
  return query ? `?${query}` : "";
}

function displayToolName(item: Pick<ApprovalItem, "tool_name" | "action_type">): string {
  return item.tool_name || item.action_type;
}

function approvalRequestIdFromHash(): string {
  if (typeof window === "undefined") {
    return "";
  }
  return hashQueryParam(window.location.hash, "request_id") ?? hashQueryParam(window.location.hash, "approval_id") ?? "";
}

export function ApprovalsScreen() {
  const { privilegedUnlocked, authRequired, currentUserId, currentUserLabel, selectedDirectoryPath } = useAdminUi();
  const initialRequestId = approvalRequestIdFromHash();
  const [filters, setFilters] = useState<Filters>({
    status: initialRequestId ? "" : "pending",
    directOnly: true,
    sessionId: "",
    vmId: "",
    toolName: "",
    minAgeMinutes: "",
  });
  const [response, setResponse] = useState<ApprovalIndexResponse | null>(null);
  const [selectedId, setSelectedId] = useState<string>(initialRequestId);
  const [deepLinkedRequestId, setDeepLinkedRequestId] = useState<string>(initialRequestId);
  const [detail, setDetail] = useState<ApprovalDetailResponse | null>(null);
  const [approver, setApprover] = useState("admin");
  const [reason, setReason] = useState("");
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [acting, setActing] = useState<"approve" | "deny" | "revoke" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const query = useMemo(() => toQuery(filters, selectedDirectoryPath), [filters, selectedDirectoryPath]);
  const usesAnonymousApprover = !authRequired;
  const signedInApprover = currentUserLabel.trim() || currentUserId.trim() || "unknown";

  async function loadApprovals(options: { preserveSelectedId?: string } = {}) {
    const next = await fetchJson<ApprovalIndexResponse>(`/api/admin/approvals${query}`);
    setResponse(next);
    setSelectedId((current) => {
      if (options.preserveSelectedId && current === options.preserveSelectedId) return current;
      if (current && next.items.some((item) => item.request_id === current)) return current;
      if (current && current === deepLinkedRequestId) return current;
      return next.items[0]?.request_id ?? "";
    });
  }

  useEffect(() => {
    const applyDeepLink = () => {
      const requestId = approvalRequestIdFromHash();
      if (!requestId) {
        return;
      }
      setDeepLinkedRequestId(requestId);
      setSelectedId(requestId);
      setFilters((current) => (current.status ? { ...current, status: "" } : current));
    };

    applyDeepLink();
    window.addEventListener("hashchange", applyDeepLink);
    return () => window.removeEventListener("hashchange", applyDeepLink);
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        await loadApprovals();
        if (cancelled) return;
        setError(null);
      } catch (reasonValue: unknown) {
        if (cancelled) return;
        setError(reasonValue instanceof Error ? reasonValue.message : String(reasonValue));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void load();
    const interval = window.setInterval(() => void load().catch(() => {}), POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [query]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    setDetailLoading(true);
    fetchJson<ApprovalDetailResponse>(`/api/admin/approvals/${encodeURIComponent(selectedId)}`)
      .then((payload) => {
        if (cancelled) return;
        setDetail(payload);
        setReason(payload.request.status === "pending" || (payload.request.status === "approved" && payload.expires_in_seconds > 0) ? "" : payload.request.decision_reason ?? "");
        setError(null);
      })
      .catch((reasonValue: unknown) => {
        if (cancelled) return;
        setError(reasonValue instanceof Error ? reasonValue.message : String(reasonValue));
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  async function submitDecision(approve: boolean) {
    if (!detail) return;
    const resolvedApprover = usesAnonymousApprover ? approver.trim() : signedInApprover;
    if (!resolvedApprover) {
      setError("Approver is required.");
      return;
    }
    if (!reason.trim()) {
      setError("Reason is required.");
      return;
    }
    setActing(approve ? "approve" : "deny");
    try {
      const actionLabel = approve ? "Approved" : "Denied";
      await fetchJson<{ request: ApprovalItem }>(`/api/admin/approvals/${encodeURIComponent(detail.request.request_id)}/decision`, {
        method: "POST",
        body: JSON.stringify({
          ...(usesAnonymousApprover ? { approver: resolvedApprover } : {}),
          approve,
          reason: reason.trim(),
        }),
      });
      await loadApprovals({ preserveSelectedId: detail.request.request_id });
      const refreshed = await fetchJson<ApprovalDetailResponse>(`/api/admin/approvals/${encodeURIComponent(detail.request.request_id)}`);
      setDetail(refreshed);
      setReason(refreshed.request.decision_reason ?? reason.trim());
      setStatusMessage(`${actionLabel} ${detail.request.request_id}.`);
      setError(null);
    } catch (reasonValue: unknown) {
      setError(reasonValue instanceof Error ? reasonValue.message : String(reasonValue));
    } finally {
      setActing(null);
    }
  }

  async function submitRevoke() {
    if (!detail) return;
    const resolvedApprover = usesAnonymousApprover ? approver.trim() : signedInApprover;
    if (!resolvedApprover) {
      setError("Approver is required.");
      return;
    }
    if (!reason.trim()) {
      setError("Reason is required.");
      return;
    }
    setActing("revoke");
    try {
      await fetchJson<{ request: ApprovalItem }>(`/api/admin/approvals/${encodeURIComponent(detail.request.request_id)}/revoke`, {
        method: "POST",
        body: JSON.stringify({
          ...(usesAnonymousApprover ? { approver: resolvedApprover } : {}),
          reason: reason.trim(),
        }),
      });
      await loadApprovals({ preserveSelectedId: detail.request.request_id });
      const refreshed = await fetchJson<ApprovalDetailResponse>(`/api/admin/approvals/${encodeURIComponent(detail.request.request_id)}`);
      setDetail(refreshed);
      setReason(refreshed.request.decision_reason ?? reason.trim());
      setStatusMessage(`Revoked ${detail.request.request_id}.`);
      setError(null);
    } catch (reasonValue: unknown) {
      setError(reasonValue instanceof Error ? reasonValue.message : String(reasonValue));
    } finally {
      setActing(null);
    }
  }

  const items = response?.items ?? [];
  const mutationLocked = !privilegedUnlocked;
  const selectedApprovalIsValid = detail ? detail.request.status === "approved" && detail.expires_in_seconds > 0 : false;

  return (
    <div className="feature-page approvals-page">
      <section className="feature-sidebar">
        <div className="feature-sidebar-header">
          <div>
            <div className="eyebrow">Approval queue</div>
            <h1>Approvals</h1>
          </div>
          <button type="button" onClick={() => void loadApprovals()}>
            Refresh
          </button>
        </div>
        {mutationLocked ? <div className="callout warning-callout">Unlock privileged operations to approve, deny, or revoke queued requests.</div> : null}
        <label className="field-stack">
          <span>Status</span>
          <select value={filters.status} onChange={(event) => setFilters((current) => ({ ...current, status: event.target.value }))}>
            <option value="">All</option>
            <option value="pending">Pending</option>
            <option value="approved">Approved</option>
            <option value="denied">Denied</option>
            <option value="expired">Expired</option>
          </select>
        </label>
        <div className="panel section-panel">
          <div className="feature-list-title-row">
            <strong>Directory scope</strong>
            <span className="panel-meta">{selectedDirectoryPath}</span>
          </div>
          <div className="feature-list-meta">{filters.directOnly ? "Direct matches only" : "Including descendants"}</div>
        </div>
        <label className="checkbox-row">
          <input
            type="checkbox"
            checked={filters.directOnly}
            onChange={(event) => setFilters((current) => ({ ...current, directOnly: event.target.checked }))}
          />
          <span>Direct directory only</span>
        </label>
        <label className="field-stack">
          <span>Session</span>
          <input value={filters.sessionId} onChange={(event) => setFilters((current) => ({ ...current, sessionId: event.target.value }))} placeholder="issue-14084-session" />
        </label>
        <label className="field-stack">
          <span>VM</span>
          <input value={filters.vmId} onChange={(event) => setFilters((current) => ({ ...current, vmId: event.target.value }))} placeholder="vm-14084" />
        </label>
        <label className="field-stack">
          <span>Tool</span>
          <input value={filters.toolName} onChange={(event) => setFilters((current) => ({ ...current, toolName: event.target.value }))} placeholder="email_send_approved" />
        </label>
        <label className="field-stack">
          <span>Min age minutes</span>
          <input value={filters.minAgeMinutes} onChange={(event) => setFilters((current) => ({ ...current, minAgeMinutes: event.target.value }))} placeholder="5" />
        </label>
        <div className="feature-list">
            {items.map((item) => (
              <button
                key={item.request_id}
                type="button"
                className={`feature-list-item approval-list-item${selectedId === item.request_id ? " selected" : ""}`}
                onClick={() => {
                  setDeepLinkedRequestId("");
                  setSelectedId(item.request_id);
                }}
              >
                <div className="feature-list-title-row approval-list-item-title-row">
                  <strong>{item.human_summary}</strong>
                  <span className={`status-pill state-${item.status === "approved" ? "running" : item.status === "pending" ? "idle" : "failed"}`}>
                    {item.status}
                  </span>
                </div>
                <Tooltip content={`Requested tool/action: ${displayToolName(item)}`} inline={false}>
                  <div className="feature-list-meta">{displayToolName(item)}</div>
                </Tooltip>
                <div className="feature-list-meta">{item.session_id}{item.vm_id ? ` • ${item.vm_id}` : ""}</div>
                <div className="feature-list-meta">{formatTimestamp(item.created_at)}</div>
              </button>
            ))}
          {!loading && items.length === 0 ? <div className="empty-state">No approval requests matched the current filters.</div> : null}
        </div>
      </section>

      <section className="feature-content">
        <div className="feature-toolbar">
          <div>
            <h2>{detail ? detail.request.request_id : "Select a request"}</h2>
            <p className="muted-text">
              Review payload context, record decisions, and keep the reason trail intact.
            </p>
          </div>
          <div className="feature-toolbar-actions">
            <span className="panel-meta">{loading ? "Loading..." : `${items.length} shown`}</span>
          </div>
        </div>

        {error ? <div className="callout error-callout">{error}</div> : null}
        {statusMessage ? <div className="callout success-callout">{statusMessage}</div> : null}

        <article className="panel">
          <div className="panel-header">
            <h3>Review</h3>
            <span className="panel-meta">{detailLoading ? "Loading..." : detail ? detail.request.request_id : "Nothing selected"}</span>
          </div>
          {detail ? (
            <div className="stack-layout">
              <dl className="property-grid">
                <div><dt>Status</dt><dd>{detail.request.status}</dd></div>
                <div><dt>Tool</dt><dd><Tooltip content={`Requested tool/action: ${displayToolName(detail.request)}`}>{displayToolName(detail.request)}</Tooltip></dd></div>
                <div><dt>Session</dt><dd>{detail.request.session_id}</dd></div>
                <div><dt>VM</dt><dd>{detail.request.vm_id ?? "n/a"}</dd></div>
                <div><dt>Created</dt><dd>{formatTimestamp(detail.request.created_at)}</dd></div>
                <div><dt>Age</dt><dd>{formatRelativeSeconds(detail.age_seconds)}</dd></div>
                <div><dt>Expires</dt><dd>{formatTimestamp(detail.request.expires_at)}</dd></div>
                <div><dt>Expires in</dt><dd>{formatRelativeSeconds(detail.expires_in_seconds)}</dd></div>
              </dl>

              <section className="approval-review-summary">
                <div className="panel-header approval-review-summary-header">
                  <h3>Summary</h3>
                  <span className="panel-meta">{detail.request.directory_path}</span>
                </div>
                <div className="approval-review-summary-body">
                  <p>{detail.request.human_summary}</p>
                  {detail.request.intended_effect ? (
                    <p className="muted-copy">
                      Intended effect:{" "}
                      <Tooltip content={detail.request.intended_effect}>
                        <span>{detail.request.intended_effect}</span>
                      </Tooltip>
                    </p>
                  ) : null}
                  <p className="muted-copy">Directory: <Tooltip content={`Directory scope for this approval request: ${detail.request.directory_path}`}><span>{detail.request.directory_path}</span></Tooltip></p>
                  <p className="muted-copy">Payload digest: <Tooltip content={`Payload digest: ${detail.request.payload_digest}`}><span>{detail.request.payload_digest}</span></Tooltip></p>
                </div>
              </section>

              <div className="feature-grid">
                <article className="panel">
                  <div className="panel-header">
                    <h3>Decision</h3>
                    <span className="panel-meta">{usesAnonymousApprover ? "Required approver and reason" : "Signed-in user and reason"}</span>
                  </div>
                  <div className="panel-body">
                {usesAnonymousApprover ? (
                  <label className="field-stack">
                    <span>Approver</span>
                    <input value={approver} onChange={(event) => setApprover(event.target.value)} disabled={mutationLocked} />
                  </label>
                ) : (
                  <p className="muted-copy">Decision will be recorded as {signedInApprover}.</p>
                )}
                <label className="field-stack">
                  <span>Reason</span>
                  <textarea value={reason} rows={4} onChange={(event) => setReason(event.target.value)} disabled={mutationLocked} />
                </label>
                <div className="action-row">
                  {detail.request.status === "pending" ? (
                    <>
                      <button
                        type="button"
                        className="primary"
                        disabled={acting !== null || mutationLocked}
                        onClick={() => void submitDecision(true)}
                      >
                        {acting === "approve" ? "Approving..." : "Approve"}
                      </button>
                      <button
                        type="button"
                        className="danger"
                        disabled={acting !== null || mutationLocked}
                        onClick={() => void submitDecision(false)}
                      >
                        {acting === "deny" ? "Denying..." : "Deny"}
                      </button>
                    </>
                  ) : selectedApprovalIsValid ? (
                    <button
                      type="button"
                      className="danger"
                      disabled={acting !== null || mutationLocked}
                      onClick={() => void submitRevoke()}
                    >
                      {acting === "revoke" ? "Revoking..." : "Revoke"}
                    </button>
                  ) : null}
                </div>
                {detail.request.status !== "pending" ? (
                  <p className="muted-copy">
                    Decision recorded by {detail.request.approver ?? "unknown"} at {formatTimestamp(detail.request.decision_at)}.
                  </p>
                ) : null}
                  </div>
                </article>

                <article className="panel">
                  <div className="panel-header">
                    <h3>Redacted payload</h3>
                  </div>
                  <pre className="log-view">{JSON.stringify(detail.redacted_payload, null, 2)}</pre>
                </article>
              </div>

              <article className="panel">
                <div className="panel-header">
                  <h3>Decision history</h3>
                </div>
                {detail.request.decision_history.length > 0 ? (
                  <div className="history-list">
                    {detail.request.decision_history.map((entry, index) => (
                      <div key={`${entry.decided_at}-${index}`} className="history-card">
                        <strong>{entry.status}</strong>
                        <span>{entry.approver ?? "unknown approver"}</span>
                        <span>{formatTimestamp(entry.decided_at)}</span>
                        <p>{entry.reason ?? "No reason recorded."}</p>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="empty-state">No decision has been recorded yet.</div>
                )}
              </article>

              <article className="panel">
                <div className="panel-header">
                  <h3>Audit context</h3>
                </div>
                {detail.audit_context.length > 0 ? (
                  <pre className="log-view">{JSON.stringify(detail.audit_context, null, 2)}</pre>
                ) : (
                  <div className="empty-state">No matching audit entries were found.</div>
                )}
              </article>
            </div>
          ) : (
            <div className="empty-state">Select an approval request to inspect it.</div>
          )}
        </article>
      </section>
    </div>
  );
}
