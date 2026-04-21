import { useEffect, useMemo, useState } from "react";

import { fetchJson, formatTimestamp } from "../admin/api";
import { useAdminUi } from "../admin/AdminContext";

type SessionRecord = {
  session_id: string;
  vm_id: string | null;
  model: string | null;
  directory_path: string;
  allowed_tools: string[];
  allowed_script_names: string[];
  expires_at: string;
  created_at: string;
  metadata: Record<string, unknown>;
  issue_number?: string | null;
  is_expired: boolean;
};

type SessionListResponse = {
  items: SessionRecord[];
  active_items: SessionRecord[];
  expired_items: SessionRecord[];
  counts: {
    total: number;
    active: number;
    expired: number;
  };
};

type WakeSchedule = {
  request_id: string;
  session_id: string;
  prompt: string;
  cron?: string | null;
  resume_at?: string | null;
  resume_mode: string;
  enabled: boolean;
  updated_at?: string | null;
  schedule_kind: "cron" | "resume_at";
};

type SessionDetailResponse = {
  session: SessionRecord;
  wake_schedules: WakeSchedule[];
  summary_json: Record<string, unknown>;
};

type SessionCatalogResponse = {
  tool_options: Array<{ value: string; label: string }>;
  resume_mode_options: Array<{ value: string; label: string }>;
  renew_extension_presets: string[];
};

type SessionMutationResponse = {
  action: string;
  message: string;
  session?: SessionRecord;
};

type TabKey = "active" | "expired";

const POLL_INTERVAL_MS = 10000;

function toDatetimeLocalValue(value: string | null | undefined): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const year = date.getFullYear();
  const month = `${date.getMonth() + 1}`.padStart(2, "0");
  const day = `${date.getDate()}`.padStart(2, "0");
  const hours = `${date.getHours()}`.padStart(2, "0");
  const minutes = `${date.getMinutes()}`.padStart(2, "0");
  return `${year}-${month}-${day}T${hours}:${minutes}`;
}

function sortTools(values: string[]): string[] {
  return [...values].sort((left, right) => left.localeCompare(right));
}

function matchesFilter(item: SessionRecord, query: string): boolean {
  const normalized = query.trim().toLowerCase();
  if (!normalized) return true;
  return [item.session_id, item.vm_id ?? "", item.issue_number ?? "", item.directory_path, item.model ?? ""]
    .join(" ")
    .toLowerCase()
    .includes(normalized);
}

function pickSessionIdForTab(response: SessionListResponse, tab: TabKey, preferredSessionId?: string): string {
  const tabItems = tab === "active" ? response.active_items : response.expired_items;
  if (preferredSessionId && tabItems.some((item) => item.session_id === preferredSessionId)) {
    return preferredSessionId;
  }
  return tabItems[0]?.session_id ?? "";
}

export function SessionsScreen() {
  const { privilegedUnlocked, selectedDirectoryPath } = useAdminUi();
  const [response, setResponse] = useState<SessionListResponse | null>(null);
  const [selectedSessionId, setSelectedSessionId] = useState("");
  const [detail, setDetail] = useState<SessionDetailResponse | null>(null);
  const [catalog, setCatalog] = useState<SessionCatalogResponse | null>(null);
  const [activeTab, setActiveTab] = useState<TabKey>("active");
  const [directOnly, setDirectOnly] = useState(true);
  const [sessionFilter, setSessionFilter] = useState("");
  const [renewExtension, setRenewExtension] = useState("1d");
  const [modelDraft, setModelDraft] = useState("");
  const [modelDirty, setModelDirty] = useState(false);
  const [modelBoundSessionId, setModelBoundSessionId] = useState("");
  const [toolsDraft, setToolsDraft] = useState<string[]>([]);
  const [toolsDirty, setToolsDirty] = useState(false);
  const [toolsBoundSessionId, setToolsBoundSessionId] = useState("");
  const [wakePrompt, setWakePrompt] = useState("");
  const [wakeCron, setWakeCron] = useState("");
  const [wakeResumeAt, setWakeResumeAt] = useState("");
  const [wakeMode, setWakeMode] = useState("same_context");
  const [wakeEnabled, setWakeEnabled] = useState(true);
  const [editingWakeRequestId, setEditingWakeRequestId] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [modelStatus, setModelStatus] = useState<string | null>(null);
  const [toolsStatus, setToolsStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  async function refreshSessions(): Promise<SessionListResponse> {
    const query = new URLSearchParams({
      include_expired: "true",
      directory_path: selectedDirectoryPath,
      direct_only: String(directOnly),
    });
    const payload = await fetchJson<SessionListResponse>(`/api/admin/sessions?${query.toString()}`);
    setResponse(payload);
    setSelectedSessionId((current) => {
      const nextSelected = pickSessionIdForTab(payload, activeTab, current);
      if (nextSelected) {
        return nextSelected;
      }
      if (current && payload.items.some((item) => item.session_id === current)) {
        return current;
      }
      return payload.active_items[0]?.session_id ?? payload.expired_items[0]?.session_id ?? "";
    });
    return payload;
  }

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const [catalogPayload] = await Promise.all([
          fetchJson<SessionCatalogResponse>("/api/admin/sessions/catalog"),
          refreshSessions(),
        ]);
        if (cancelled) return;
        setCatalog(catalogPayload);
        setError(null);
      } catch (reason) {
        if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason));
      }
    }

    void load();
    const timer = window.setInterval(() => void refreshSessions().catch(() => {}), POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [activeTab, selectedDirectoryPath, directOnly]);

  useEffect(() => {
    const nextItems = activeTab === "active" ? response?.active_items ?? [] : response?.expired_items ?? [];
    if (selectedSessionId && nextItems.some((item) => item.session_id === selectedSessionId)) {
      return;
    }
    setSelectedSessionId(nextItems[0]?.session_id ?? "");
  }, [activeTab, response?.active_items, response?.expired_items, selectedSessionId]);

  useEffect(() => {
    if (!selectedSessionId) {
      setDetail(null);
      setDetailError(null);
      setEditingWakeRequestId("");
      return;
    }
    let cancelled = false;
    fetchJson<SessionDetailResponse>(`/api/admin/sessions/${encodeURIComponent(selectedSessionId)}`)
      .then((payload) => {
        if (cancelled) return;
        setDetail(payload);
        setDetailError(null);
        const sessionId = payload.session.session_id;
        if (modelBoundSessionId !== sessionId) {
          setModelDraft(payload.session.model ?? "");
          setModelDirty(false);
          setModelStatus(null);
          setModelBoundSessionId(sessionId);
        } else if (!modelDirty) {
          setModelDraft(payload.session.model ?? "");
        }
        if (toolsBoundSessionId !== sessionId) {
          setToolsDraft(sortTools(payload.session.allowed_tools));
          setToolsDirty(false);
          setToolsStatus(null);
          setToolsBoundSessionId(sessionId);
        } else if (!toolsDirty) {
          setToolsDraft(sortTools(payload.session.allowed_tools));
        }
        if (!editingWakeRequestId || !payload.wake_schedules.some((item) => item.request_id === editingWakeRequestId)) {
          const firstSchedule = payload.wake_schedules[0];
          if (firstSchedule) {
            setEditingWakeRequestId(firstSchedule.request_id);
            setWakePrompt(firstSchedule.prompt);
            setWakeCron(firstSchedule.cron ?? "");
            setWakeResumeAt(toDatetimeLocalValue(firstSchedule.resume_at));
            setWakeMode(firstSchedule.resume_mode);
            setWakeEnabled(firstSchedule.enabled);
          } else {
            setEditingWakeRequestId("");
            setWakePrompt("");
            setWakeCron("");
            setWakeResumeAt("");
            setWakeMode("same_context");
            setWakeEnabled(true);
          }
        }
      })
      .catch((reason) => {
        if (cancelled) return;
        setDetailError(reason instanceof Error ? reason.message : String(reason));
      });
    return () => {
      cancelled = true;
    };
  }, [selectedSessionId]);

  const visibleItems = useMemo(() => {
    const source = activeTab === "active" ? response?.active_items ?? [] : response?.expired_items ?? [];
    return source.filter((item) => matchesFilter(item, sessionFilter));
  }, [activeTab, response?.active_items, response?.expired_items, sessionFilter]);

  useEffect(() => {
    if (selectedSessionId && visibleItems.some((item) => item.session_id === selectedSessionId)) {
      return;
    }
    setSelectedSessionId(visibleItems[0]?.session_id ?? "");
  }, [selectedSessionId, visibleItems]);

  const selectedSession = useMemo(
    () => response?.items.find((item) => item.session_id === selectedSessionId) ?? detail?.session ?? null,
    [detail?.session, response?.items, selectedSessionId],
  );

  const selectedWakeSchedule = useMemo(
    () => detail?.wake_schedules.find((item) => item.request_id === editingWakeRequestId) ?? null,
    [detail?.wake_schedules, editingWakeRequestId],
  );
  const mutationLocked = !privilegedUnlocked;

  function resetWakeEditor() {
    setEditingWakeRequestId("");
    setWakePrompt("");
    setWakeCron("");
    setWakeResumeAt("");
    setWakeMode("same_context");
    setWakeEnabled(true);
  }

  function bindWakeSchedule(requestId: string) {
    const item = detail?.wake_schedules.find((schedule) => schedule.request_id === requestId);
    if (!item) return;
    setEditingWakeRequestId(item.request_id);
    setWakePrompt(item.prompt);
    setWakeCron(item.cron ?? "");
    setWakeResumeAt(toDatetimeLocalValue(item.resume_at));
    setWakeMode(item.resume_mode);
    setWakeEnabled(item.enabled);
  }

  async function refreshSelectedSessionDetail(sessionId: string) {
    const payload = await fetchJson<SessionDetailResponse>(`/api/admin/sessions/${encodeURIComponent(sessionId)}`);
    setDetail(payload);
    return payload;
  }

  async function runSessionAction(action: "renew" | "revoke" | "delete") {
    if (!selectedSession) return;
    if (action === "renew" && !renewExtension.trim()) {
      setError("Renew extension is required.");
      return;
    }
    if (action === "delete" && !window.confirm(`Delete session ${selectedSession.session_id}? This cannot be undone.`)) {
      return;
    }
    if (action === "revoke" && !window.confirm(`Revoke session ${selectedSession.session_id}?`)) {
      return;
    }
    setBusy(action);
    try {
      const payload = await fetchJson<{ message: string }>(
        `/api/admin/sessions/${encodeURIComponent(selectedSession.session_id)}${action === "delete" ? "" : `/${action}`}`,
        {
          method: action === "delete" ? "DELETE" : "POST",
          body: action === "renew" ? JSON.stringify({ extension: renewExtension.trim() }) : undefined,
        },
      );
      setStatus(payload.message);
      setError(null);
      const refreshedSessions = await refreshSessions();
      const nextSelectedSessionId = action === "delete"
        ? pickSessionIdForTab(refreshedSessions, activeTab)
        : pickSessionIdForTab(refreshedSessions, activeTab, selectedSession.session_id);
      if (action === "delete") {
        if (!nextSelectedSessionId) {
          setDetail(null);
          setDetailError(null);
        }
      } else if (nextSelectedSessionId) {
        await refreshSelectedSessionDetail(nextSelectedSessionId);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(null);
    }
  }

  async function saveModel() {
    if (!selectedSession) return;
    if (!modelDraft.trim()) {
      setModelStatus("Model is required.");
      return;
    }
    setBusy("model");
    try {
      const payload = await fetchJson<SessionMutationResponse>(
        `/api/admin/sessions/${encodeURIComponent(selectedSession.session_id)}/model`,
        {
          method: "PUT",
          body: JSON.stringify({ model: modelDraft.trim() }),
        },
      );
      setStatus(payload.message);
      setModelStatus("Saved.");
      setModelDirty(false);
      setModelBoundSessionId(selectedSession.session_id);
      setError(null);
      await refreshSessions();
      await refreshSelectedSessionDetail(selectedSession.session_id);
    } catch (reason) {
      setModelStatus(null);
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(null);
    }
  }

  function toggleTool(toolValue: string) {
    setToolsDirty(true);
    setToolsStatus(null);
    setToolsDraft((current) => {
      const next = current.includes(toolValue) ? current.filter((value) => value !== toolValue) : [...current, toolValue];
      return sortTools(next);
    });
  }

  async function saveTools() {
    if (!selectedSession) return;
    setBusy("tools");
    try {
      const payload = await fetchJson<SessionMutationResponse>(
        `/api/admin/sessions/${encodeURIComponent(selectedSession.session_id)}/allowed-tools`,
        {
          method: "PUT",
          body: JSON.stringify({ allowed_tools: toolsDraft }),
        },
      );
      setStatus(payload.message);
      setToolsStatus("Saved.");
      setToolsDirty(false);
      setToolsBoundSessionId(selectedSession.session_id);
      setError(null);
      await refreshSessions();
      await refreshSelectedSessionDetail(selectedSession.session_id);
    } catch (reason) {
      setToolsStatus(null);
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(null);
    }
  }

  async function submitWakeSchedule(mode: "create" | "update") {
    if (!selectedSession) return;
    if (!wakePrompt.trim()) {
      setError("Wake prompt is required.");
      return;
    }
    if (!wakeCron.trim() && !wakeResumeAt.trim()) {
      setError("Provide either a cron expression or a resume timestamp.");
      return;
    }
    if (wakeCron.trim() && wakeResumeAt.trim()) {
      setError("Choose either a cron expression or a resume timestamp, not both.");
      return;
    }
    setBusy(mode === "create" ? "wake-create" : "wake-update");
    try {
      const path = mode === "create"
        ? `/api/admin/sessions/${encodeURIComponent(selectedSession.session_id)}/wake-schedules`
        : `/api/admin/sessions/wake-schedules/${encodeURIComponent(editingWakeRequestId)}`;
      const payload = await fetchJson<{ item: WakeSchedule }>(path, {
        method: mode === "create" ? "POST" : "PATCH",
        body: JSON.stringify({
          prompt: wakePrompt.trim(),
          cron: wakeCron.trim() || null,
          resume_at: wakeResumeAt ? new Date(wakeResumeAt).toISOString() : null,
          resume_mode: wakeMode,
          enabled: wakeEnabled,
        }),
      });
      setStatus(
        mode === "create"
          ? `Created wake schedule for ${selectedSession.session_id}.`
          : `Updated wake schedule ${payload.item.request_id}.`,
      );
      setError(null);
      const refreshed = await refreshSelectedSessionDetail(selectedSession.session_id);
      bindWakeSchedule(payload.item.request_id);
      setDetail(refreshed);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(null);
    }
  }

  async function toggleWakeScheduleEnabled(item: WakeSchedule) {
    setBusy(`wake-toggle-${item.request_id}`);
    try {
      await fetchJson<{ item: WakeSchedule }>(`/api/admin/sessions/wake-schedules/${encodeURIComponent(item.request_id)}`, {
        method: "PATCH",
        body: JSON.stringify({ enabled: !item.enabled }),
      });
      setStatus(`${!item.enabled ? "Enabled" : "Disabled"} wake schedule ${item.request_id}.`);
      setError(null);
      const refreshed = await refreshSelectedSessionDetail(item.session_id);
      setDetail(refreshed);
      bindWakeSchedule(item.request_id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(null);
    }
  }

  async function deleteWakeSchedule(item: WakeSchedule) {
    if (!window.confirm(`Delete wake schedule ${item.request_id}?`)) {
      return;
    }
    setBusy(`wake-delete-${item.request_id}`);
    try {
      await fetchJson<{ ok: boolean }>(`/api/admin/sessions/wake-schedules/${encodeURIComponent(item.request_id)}`, {
        method: "DELETE",
      });
      setStatus(`Deleted wake schedule ${item.request_id}.`);
      setError(null);
      const refreshed = await refreshSelectedSessionDetail(item.session_id);
      setDetail(refreshed);
      resetWakeEditor();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="feature-page">
      <section className="feature-sidebar">
        <div className="feature-sidebar-header">
          <div>
            <div className="eyebrow">Session control</div>
            <h1>Sessions</h1>
          </div>
          <button type="button" onClick={() => void refreshSessions()}>
            Refresh
          </button>
        </div>
        {mutationLocked ? <div className="callout warning-callout">Unlock privileged operations to renew, revoke, edit, or schedule sessions.</div> : null}
        <div className="panel section-panel">
          <div className="feature-list-title-row">
            <strong>Directory scope</strong>
            <span className="panel-meta">{selectedDirectoryPath}</span>
          </div>
          <div className="feature-list-meta">{directOnly ? "Direct matches only" : "Including descendants"}</div>
        </div>
        <label className="checkbox-row">
          <input type="checkbox" checked={directOnly} onChange={(event) => setDirectOnly(event.target.checked)} />
          <span>Direct directory only</span>
        </label>
        <label className="field-stack">
          <span>Find session</span>
          <input value={sessionFilter} onChange={(event) => setSessionFilter(event.target.value)} placeholder="session, VM, issue, model" />
        </label>
        <div className="inline-actions">
          <button type="button" className={activeTab === "active" ? "primary" : ""} onClick={() => setActiveTab("active")}>
            Active ({response?.counts.active ?? 0})
          </button>
          <button type="button" className={activeTab === "expired" ? "primary" : ""} onClick={() => setActiveTab("expired")}>
            Expired ({response?.counts.expired ?? 0})
          </button>
        </div>
        <div className="feature-list">
          {visibleItems.map((item) => (
            <button
              key={item.session_id}
              type="button"
              className={`feature-list-item${item.session_id === selectedSessionId ? " selected" : ""}`}
              onClick={() => setSelectedSessionId(item.session_id)}
            >
              <div className="feature-list-title-row">
                <strong>{item.session_id}</strong>
                <span className={`status-pill state-${item.is_expired ? "failed" : "running"}`}>{item.is_expired ? "Expired" : "Active"}</span>
              </div>
              <div className="feature-list-meta">{item.issue_number ? `Issue ${item.issue_number}` : "No linked issue"}</div>
              <div className="feature-list-meta">{item.vm_id ?? "No VM attached"}</div>
              <div className="feature-list-meta">{item.model ?? "No model configured"}</div>
            </button>
          ))}
          {visibleItems.length === 0 ? <div className="empty-state">No sessions matched the current view.</div> : null}
        </div>
      </section>
      <section className="feature-content">
        <div className="feature-toolbar">
          <div>
            <h2>{selectedSession?.session_id ?? "Select a session"}</h2>
            <p className="muted-text">
              {selectedSession
                ? "Manage expiry, model and tool access, then inspect wake schedules and the live session summary."
                : "Choose a session from the left to inspect and manage it."}
            </p>
          </div>
          {selectedSession ? (
            <div className="feature-toolbar-actions">
              <span className={`status-pill state-${selectedSession.is_expired ? "failed" : "running"}`}>
                {selectedSession.is_expired ? "Expired" : "Active"}
              </span>
              <span className="panel-meta">{selectedSession.vm_id ?? "No VM"}</span>
            </div>
          ) : null}
        </div>

        {error ? <div className="callout error-callout">{error}</div> : null}
        {status ? <div className="callout success-callout">{status}</div> : null}
        {selectedSession ? (
          <div className="stack-layout">
            <article className="panel">
              <div className="panel-header">
                <h3>Session overview</h3>
                <span className="panel-meta">{selectedSession.directory_path}</span>
              </div>
              <div className="panel-body">
                <dl className="property-grid">
                  <div><dt>VM</dt><dd>{selectedSession.vm_id ?? "None"}</dd></div>
                  <div><dt>Issue</dt><dd>{selectedSession.issue_number ?? "None"}</dd></div>
                  <div><dt>Directory</dt><dd>{selectedSession.directory_path}</dd></div>
                  <div><dt>Model</dt><dd>{selectedSession.model ?? "Unset"}</dd></div>
                  <div><dt>Created</dt><dd>{formatTimestamp(selectedSession.created_at)}</dd></div>
                  <div><dt>Expires</dt><dd>{formatTimestamp(selectedSession.expires_at)}</dd></div>
                </dl>
                <div className="inline-actions">
                  <label className="field-stack">
                    <span>Extend by</span>
                    <input
                      list="renew-extension-presets"
                      value={renewExtension}
                      onChange={(event) => setRenewExtension(event.target.value)}
                    />
                    <datalist id="renew-extension-presets">
                      {(catalog?.renew_extension_presets ?? []).map((preset) => (
                        <option key={preset} value={preset} />
                      ))}
                    </datalist>
                  </label>
                  <button type="button" onClick={() => void runSessionAction("renew")} disabled={busy === "renew" || mutationLocked}>
                    {busy === "renew" ? "Extending..." : "Extend"}
                  </button>
                  <button type="button" onClick={() => void runSessionAction("revoke")} disabled={busy === "revoke" || mutationLocked}>
                    {busy === "revoke" ? "Revoking..." : "Revoke"}
                  </button>
                  <button className="danger" type="button" onClick={() => void runSessionAction("delete")} disabled={busy === "delete" || mutationLocked}>
                    {busy === "delete" ? "Deleting..." : "Delete"}
                  </button>
                </div>
              </div>
            </article>
            <div className="feature-grid">
              <article className="panel">
                <div className="panel-header">
                  <h3>Model and tools</h3>
                  <span className="panel-meta">{modelDirty || toolsDirty ? "Unsaved changes" : "In sync"}</span>
                </div>
                <div className="panel-body">
                  <label className="field-stack">
                    <span>Model</span>
                    <input
                      value={modelDraft}
                      onChange={(event) => {
                        setModelDraft(event.target.value);
                        setModelDirty(true);
                        setModelStatus(null);
                      }}
                      placeholder="Required to start or resume the agent"
                      disabled={mutationLocked}
                    />
                  </label>
                  {modelStatus ? <div className="success-box">{modelStatus}</div> : null}
                  <button type="button" onClick={() => void saveModel()} disabled={busy === "model" || mutationLocked}>
                    {busy === "model" ? "Saving..." : "Save model"}
                  </button>
                  <div className="field-stack">
                    <span>Allowed tools</span>
                    <div className="feature-list compact-list">
                      {(catalog?.tool_options ?? []).map((tool) => (
                        <label key={tool.value} className="checkbox-row">
                          <input
                            type="checkbox"
                            checked={toolsDraft.includes(tool.value)}
                            onChange={() => toggleTool(tool.value)}
                            disabled={mutationLocked}
                          />
                          <span>{tool.label}</span>
                        </label>
                      ))}
                    </div>
                  </div>
                  {toolsStatus ? <div className="success-box">{toolsStatus}</div> : null}
                  <button type="button" onClick={() => void saveTools()} disabled={busy === "tools" || mutationLocked}>
                    {busy === "tools" ? "Saving..." : "Save tools"}
                  </button>
                </div>
              </article>
              <article className="panel">
                <div className="panel-header">
                  <h3>Wake schedules</h3>
                  <span className="panel-meta">{detail?.wake_schedules.length ?? 0} configured</span>
                </div>
                <div className="panel-body">
                  <div className="inline-actions">
                    <button type="button" onClick={resetWakeEditor} disabled={mutationLocked}>
                      New schedule
                    </button>
                    {selectedWakeSchedule ? (
                      <button type="button" onClick={() => void toggleWakeScheduleEnabled(selectedWakeSchedule)} disabled={busy === `wake-toggle-${selectedWakeSchedule.request_id}` || mutationLocked}>
                        {selectedWakeSchedule.enabled ? "Disable selected" : "Enable selected"}
                      </button>
                    ) : null}
                  </div>
                  <label className="field-stack">
                    <span>Prompt</span>
                    <textarea rows={4} value={wakePrompt} onChange={(event) => setWakePrompt(event.target.value)} disabled={mutationLocked} />
                  </label>
                  <label className="field-stack">
                    <span>Cron</span>
                    <input value={wakeCron} onChange={(event) => setWakeCron(event.target.value)} placeholder="*/30 * * * *" disabled={mutationLocked} />
                  </label>
                  <label className="field-stack">
                    <span>Resume at</span>
                    <input type="datetime-local" value={wakeResumeAt} onChange={(event) => setWakeResumeAt(event.target.value)} disabled={mutationLocked} />
                  </label>
                  <label className="field-stack">
                    <span>Resume mode</span>
                    <select value={wakeMode} onChange={(event) => setWakeMode(event.target.value)} disabled={mutationLocked}>
                      {(catalog?.resume_mode_options ?? []).map((option) => (
                        <option key={option.value} value={option.value}>{option.label}</option>
                      ))}
                    </select>
                  </label>
                  <label className="checkbox-row">
                    <input type="checkbox" checked={wakeEnabled} onChange={(event) => setWakeEnabled(event.target.checked)} disabled={mutationLocked} />
                    <span>Enabled</span>
                  </label>
                  <div className="inline-actions">
                    <button
                      type="button"
                      onClick={() => void submitWakeSchedule(editingWakeRequestId ? "update" : "create")}
                      disabled={busy === "wake-create" || busy === "wake-update" || mutationLocked}
                    >
                      {editingWakeRequestId ? (busy === "wake-update" ? "Saving..." : "Save schedule") : (busy === "wake-create" ? "Creating..." : "Create wake schedule")}
                    </button>
                    {selectedWakeSchedule ? (
                      <button
                        className="danger"
                        type="button"
                        onClick={() => void deleteWakeSchedule(selectedWakeSchedule)}
                        disabled={busy === `wake-delete-${selectedWakeSchedule.request_id}` || mutationLocked}
                      >
                        {busy === `wake-delete-${selectedWakeSchedule.request_id}` ? "Deleting..." : "Delete selected"}
                      </button>
                    ) : null}
                  </div>
                  <div className="feature-list compact-list">
                    {(detail?.wake_schedules ?? []).map((item) => (
                      <button
                        key={item.request_id}
                        type="button"
                        className={`feature-list-item${editingWakeRequestId === item.request_id ? " selected" : ""}`}
                        onClick={() => bindWakeSchedule(item.request_id)}
                      >
                        <div className="feature-list-title-row">
                          <strong>{item.schedule_kind === "cron" ? item.cron : formatTimestamp(item.resume_at)}</strong>
                          <span className={`status-pill state-${item.enabled ? "running" : "idle"}`}>{item.enabled ? "Enabled" : "Disabled"}</span>
                        </div>
                        <div className="feature-list-meta">{item.prompt}</div>
                        <div className="feature-list-meta">{item.resume_mode}</div>
                      </button>
                    ))}
                    {detail?.wake_schedules.length === 0 ? <div className="empty-state">No wake schedules configured.</div> : null}
                  </div>
                </div>
              </article>
            </div>
            <article className="panel">
              <div className="panel-header">
                <h3>Session detail</h3>
                <span className="panel-meta">{detailError ? "Unavailable" : "Live summary"}</span>
              </div>
              <pre className="log-view">
                {detailError
                  ? detailError
                  : JSON.stringify(detail?.summary_json ?? selectedSession, null, 2)}
              </pre>
            </article>
          </div>
        ) : (
          <div className="empty-state">Select a session to inspect its details.</div>
        )}
      </section>
    </div>
  );
}
