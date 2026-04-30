import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { fetchJson, formatTimestamp } from "../admin/api";
import { useAdminUi } from "../admin/AdminContext";
import { Tooltip } from "../admin/Tooltip";
import type {
  AgentActionResponse,
  AgentBackend,
  AgentListResponse,
  AgentLogResponse,
  AgentRecord,
} from "../admin/types";

type ActionLogEntry = {
  at: string;
  message: string;
  tone: "info" | "error";
};

type BackendFilter = "all" | AgentBackend;
type StatusFilter = "active" | "inactive" | "all";

const REFRESH_MS = 5000;
const AGENT_SELECTION_STORAGE_KEY = "admin-web:agents:selected-agent";

async function loadAgentList(
  directoryPath: string,
  directOnly: boolean,
): Promise<AgentListResponse> {
  const query = new URLSearchParams({
    directory_path: directoryPath,
    direct_only: String(directOnly),
    include_inactive: "true",
  });
  return fetchJson<AgentListResponse>(`/api/admin/agents?${query.toString()}`);
}

async function loadAgentLog(agent: AgentRecord | null): Promise<string> {
  if (!agent) return "No agent selected.";
  if (!agent.capabilities.logs) return "Logs are not available for this agent.";
  const query = new URLSearchParams({
    backend: agent.backend,
    lines: "200",
  });
  const guestAgent = hostRecord(agent.host, "agent");
  const repoName = hostString(guestAgent, "preferred_repo_name");
  if (repoName) {
    query.set("repo_name", repoName);
  }
  const payload = await fetchJson<AgentLogResponse>(
    `/api/admin/agents/${encodeURIComponent(agent.agent_id)}/log?${query.toString()}`,
  );
  return payload.text || "No log output yet.";
}

function agentKey(agent: AgentRecord): string {
  return `${agent.backend}:${agent.agent_id}`;
}

function backendLabel(backend: AgentBackend): string {
  return backend === "container_worker_vm" ? "Container workers" : "VM agents";
}

function hostString(host: Record<string, unknown> | null | undefined, key: string): string | null {
  const value = host?.[key];
  return typeof value === "string" && value.trim() ? value : null;
}

function hostNumber(host: Record<string, unknown> | null | undefined, key: string): number | null {
  const value = host?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function hostStringList(host: Record<string, unknown> | null | undefined, key: string): string[] {
  const value = host?.[key];
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string" && item.trim().length > 0);
}

function hostRecord(
  host: Record<string, unknown> | null | undefined,
  key: string,
): Record<string, unknown> | null {
  const value = host?.[key];
  if (typeof value === "object" && value !== null && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  return null;
}

function recordEntries(value: Record<string, unknown> | null | undefined): Array<[string, string]> {
  if (!value) return [];
  return Object.entries(value)
    .filter(([, item]) => item != null && typeof item !== "object")
    .map(([key, item]) => [key, String(item)]);
}

function metadataForAgent(agent: AgentRecord | null): Record<string, unknown> | null {
  if (!agent) return null;
  return hostRecord(agent.host, "metadata");
}

function labelsForAgent(agent: AgentRecord | null): Array<[string, string]> {
  if (!agent) return [];
  const directLabels = hostRecord(agent.host, "labels");
  const metadataLabels = hostRecord(metadataForAgent(agent), "labels");
  return recordEntries(directLabels ?? metadataLabels).sort(([left], [right]) =>
    left.localeCompare(right, undefined, { sensitivity: "base" }),
  );
}

function isActiveStatus(status: string): boolean {
  return ["preparing", "starting", "running"].includes(status.trim().toLowerCase());
}

function statusClass(status: string): string {
  const normalized = status.trim().toLowerCase();
  if (normalized === "running" || normalized === "starting" || normalized === "preparing") {
    return "running";
  }
  if (normalized === "failed") return "failed";
  return "idle";
}

function timestampRank(value: string | null): number {
  if (!value) return 0;
  const time = new Date(value).getTime();
  return Number.isFinite(time) ? time : 0;
}

function AgentIcon({ children, viewBox = "0 0 24 24" }: { children: ReactNode; viewBox?: string }) {
  return (
    <svg aria-hidden="true" viewBox={viewBox} fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
      {children}
    </svg>
  );
}

function StartIcon() {
  return (
    <AgentIcon>
      <path d="M8 6.5v11l8-5.5z" fill="currentColor" stroke="none" />
    </AgentIcon>
  );
}

function StopIcon() {
  return (
    <AgentIcon>
      <rect x="7.5" y="7.5" width="9" height="9" rx="1.5" fill="currentColor" stroke="none" />
    </AgentIcon>
  );
}

function PowerIcon() {
  return (
    <AgentIcon>
      <path d="M12 3.5v7" />
      <path d="M7.2 6.8a8 8 0 1 0 9.6 0" />
    </AgentIcon>
  );
}

function ResumeIcon() {
  return (
    <AgentIcon>
      <path d="M7.5 7.5H4.5v-3" />
      <path d="M4.7 11a7.5 7.5 0 1 1 2.2 6.1" />
    </AgentIcon>
  );
}

function EnableIcon() {
  return (
    <AgentIcon>
      <circle cx="12" cy="12" r="8" />
      <path d="m8.8 12.2 2.1 2.1 4.5-4.8" />
    </AgentIcon>
  );
}

function DisableIcon() {
  return (
    <AgentIcon>
      <circle cx="12" cy="12" r="8" />
      <path d="M8.2 15.8 15.8 8.2" />
    </AgentIcon>
  );
}

function KillIcon() {
  return (
    <AgentIcon>
      <path d="M9 4.8h6l4.2 4.2v6L15 19.2H9L4.8 15v-6z" />
      <path d="m9.4 9.4 5.2 5.2" />
      <path d="m14.6 9.4-5.2 5.2" />
    </AgentIcon>
  );
}

function DestroyIcon() {
  return (
    <AgentIcon>
      <path d="M5.5 7.5h13" />
      <path d="M9 7.5v-2h6v2" />
      <path d="M8.5 10.5v6" />
      <path d="M12 10.5v6" />
      <path d="M15.5 10.5v6" />
      <path d="M7.5 7.5v10.2a1.3 1.3 0 0 0 1.3 1.3h6.4a1.3 1.3 0 0 0 1.3-1.3V7.5" />
    </AgentIcon>
  );
}

export function AgentsPage() {
  const { privilegedUnlocked, selectedDirectoryPath } = useAdminUi();
  const [items, setItems] = useState<AgentRecord[]>([]);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [backendFilter, setBackendFilter] = useState<BackendFilter>("all");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [agentLog, setAgentLog] = useState("Loading...");
  const [resumePrompt, setResumePrompt] = useState("");
  const [resumeModel, setResumeModel] = useState("");
  const [showResumeForm, setShowResumeForm] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [actionLog, setActionLog] = useState<ActionLogEntry[]>([]);
  const [selectionHydrated, setSelectionHydrated] = useState(false);

  useEffect(() => {
    const saved = window.localStorage.getItem(AGENT_SELECTION_STORAGE_KEY);
    if (saved) {
      setSelectedKey(saved);
    }
    setSelectionHydrated(true);
  }, []);

  useEffect(() => {
    if (!selectionHydrated) return;
    if (selectedKey) {
      window.localStorage.setItem(AGENT_SELECTION_STORAGE_KEY, selectedKey);
      return;
    }
    window.localStorage.removeItem(AGENT_SELECTION_STORAGE_KEY);
  }, [selectedKey, selectionHydrated]);

  const filteredItems = useMemo(() => {
    return [...items]
      .filter((item) => backendFilter === "all" || item.backend === backendFilter)
      .filter((item) => {
        if (statusFilter === "all") return true;
        const active = isActiveStatus(item.status);
        return statusFilter === "active" ? active : !active;
      })
      .sort((left, right) => {
        const byTime = timestampRank(right.updated_at) - timestampRank(left.updated_at);
        if (byTime !== 0) return byTime;
        return agentKey(left).localeCompare(agentKey(right), undefined, {
          numeric: true,
          sensitivity: "base",
        });
      });
  }, [backendFilter, items, statusFilter]);

  const groupedItems = useMemo(() => {
    return {
      libvirt_vm: filteredItems.filter((item) => item.backend === "libvirt_vm"),
      container_worker_vm: filteredItems.filter((item) => item.backend === "container_worker_vm"),
    } satisfies Record<AgentBackend, AgentRecord[]>;
  }, [filteredItems]);

  const selectedAgent = useMemo(
    () => filteredItems.find((item) => agentKey(item) === selectedKey) ?? filteredItems[0] ?? null,
    [filteredItems, selectedKey],
  );

  const refreshAgents = useCallback(async () => {
    const payload = await loadAgentList(selectedDirectoryPath, true);
    setItems(payload.items);
    setSelectedKey((current) => {
      if (current && payload.items.some((item) => agentKey(item) === current)) return current;
      return payload.items[0] ? agentKey(payload.items[0]) : null;
    });
  }, [selectedDirectoryPath]);

  useEffect(() => {
    let cancelled = false;
    const refresh = async () => {
      try {
        await refreshAgents();
        if (!cancelled) setError(null);
      } catch (reason: unknown) {
        if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason));
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void refresh();
    const timer = window.setInterval(() => {
      void refresh();
    }, REFRESH_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [refreshAgents]);

  useEffect(() => {
    if (!selectedAgent) {
      setAgentLog("No agent selected.");
      return;
    }
    let cancelled = false;
    const refreshLog = async (showLoadingState: boolean) => {
      if (showLoadingState) setAgentLog("Loading...");
      try {
        const text = await loadAgentLog(selectedAgent);
        if (!cancelled) setAgentLog((current) => (current === text ? current : text));
      } catch (reason: unknown) {
        const message = reason instanceof Error ? reason.message : String(reason);
        if (!cancelled) setAgentLog((current) => (current === message ? current : message));
      }
    };
    void refreshLog(true);
    const timer = window.setInterval(() => {
      void refreshLog(false);
    }, REFRESH_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [selectedAgent?.backend, selectedAgent?.agent_id]);

  async function handleManualRefresh() {
    setLoading(true);
    try {
      await refreshAgents();
      setError(null);
      setStatus("Refreshed agent list.");
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  }

  async function runAgentAction(action: string, body: Record<string, unknown> = {}) {
    if (!selectedAgent) return;
    setBusyAction(action);
    try {
      const query = new URLSearchParams({ backend: selectedAgent.backend });
      const payload = await fetchJson<AgentActionResponse>(
        `/api/admin/agents/${encodeURIComponent(selectedAgent.agent_id)}/actions/${encodeURIComponent(action)}?${query.toString()}`,
        {
          method: "POST",
          body: JSON.stringify(body),
        },
      );
      const message = `${selectedAgent.target_name}: ${payload.message}`;
      setActionLog((current) => [{ at: new Date().toISOString(), message, tone: "info" as const }, ...current].slice(0, 30));
      setStatus(payload.message);
      setError(null);
      if (action === "resume-agent") {
        setResumePrompt("");
        setResumeModel("");
        setShowResumeForm(false);
      }
      await refreshAgents();
      setSelectedKey(agentKey(selectedAgent));
    } catch (reason: unknown) {
      const message = reason instanceof Error ? reason.message : String(reason);
      setError(message);
      setStatus(null);
      setActionLog((current) => [{ at: new Date().toISOString(), message: `${selectedAgent.target_name}: ${message}`, tone: "error" as const }, ...current].slice(0, 30));
    } finally {
      setBusyAction(null);
    }
  }

  const guestAgent = hostRecord(selectedAgent?.host, "agent");
  const agentState = hostString(guestAgent, "state") ?? "unknown";
  const agentPid = hostNumber(guestAgent, "pid");
  const powerState = hostString(selectedAgent?.host, "power_state") ?? selectedAgent?.status ?? "unknown";
  const selectedMetadata = metadataForAgent(selectedAgent);
  const selectedLabels = labelsForAgent(selectedAgent);
  const canMutate = busyAction === null && privilegedUnlocked;
  const showStart = selectedAgent?.backend === "libvirt_vm" && selectedAgent.capabilities.start && powerState !== "running";
  const showShutdown = selectedAgent?.backend === "libvirt_vm" && selectedAgent.capabilities.shutdown && powerState === "running";
  const showEnable = selectedAgent?.backend === "libvirt_vm" && selectedAgent.capabilities.enable_disable && agentState === "disabled";
  const showDisable = selectedAgent?.backend === "libvirt_vm" && selectedAgent.capabilities.enable_disable && agentState !== "disabled";
  const showKill = selectedAgent?.backend === "libvirt_vm" && selectedAgent.capabilities.kill && agentState === "running" && agentPid != null;
  const showResume = selectedAgent?.backend === "libvirt_vm" && selectedAgent.capabilities.resume && agentState !== "disabled";
  const showContainerStop = selectedAgent?.backend === "container_worker_vm" && selectedAgent.capabilities.stop && isActiveStatus(selectedAgent.status);
  const showDestroy = Boolean(selectedAgent?.capabilities.destroy);
  const logTitle = selectedAgent?.backend === "container_worker_vm"
    ? hostString(selectedAgent.host, "worker_job_id") ?? "Worker job"
    : hostString(guestAgent, "preferred_repo_name") ?? "Agent output";

  return (
    <div className="feature-page agents-page">
      <section className="feature-sidebar">
        <div className="feature-sidebar-header">
          <div>
            <div className="eyebrow">Execution targets</div>
            <h1>Agents</h1>
          </div>
          <div className="inline-actions compact-actions">
            <button type="button" onClick={() => void handleManualRefresh()} disabled={loading}>
              Refresh
            </button>
          </div>
        </div>
        {!privilegedUnlocked ? (
          <div className="callout warning-callout">Unlock privileged operations to stop, destroy, or resume targets.</div>
        ) : null}
        <div className="config-field-grid agents-filter-grid">
          <label className="field-stack">
            <span>Backend</span>
            <select value={backendFilter} onChange={(event) => setBackendFilter(event.target.value as BackendFilter)}>
              <option value="all">All backends</option>
              <option value="libvirt_vm">VM agents</option>
              <option value="container_worker_vm">Container workers</option>
            </select>
          </label>
          <label className="field-stack">
            <span>Status</span>
            <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value as StatusFilter)}>
              <option value="all">All</option>
              <option value="active">Active</option>
              <option value="inactive">Inactive</option>
            </select>
          </label>
        </div>
        <div className="feature-list vm-list agent-list">
          {(["libvirt_vm", "container_worker_vm"] as AgentBackend[]).map((backend) => {
            const group = groupedItems[backend];
            if (group.length === 0) return null;
            return (
              <div className="agent-list-section" key={backend}>
                <div className="section-label agent-list-heading">{backendLabel(backend)}</div>
                {group.map((item) => (
                  <button
                    key={agentKey(item)}
                    type="button"
                    className={`feature-list-item vm-list-item agent-list-item${selectedAgent && agentKey(selectedAgent) === agentKey(item) ? " selected" : ""}`}
                    onClick={() => {
                      setSelectedKey(agentKey(item));
                      setShowResumeForm(false);
                    }}
                  >
                    <div className="feature-list-title-row vm-list-item-row">
                      <strong>{item.target_name || item.agent_id}</strong>
                      <span className={`status-pill vm-status-pill state-${statusClass(item.status)}`}>
                        {item.status}
                      </span>
                    </div>
                    <div className="feature-list-meta agent-list-item-meta">
                      {item.session_id ?? "No session"} / {item.directory_path}
                    </div>
                  </button>
                ))}
              </div>
            );
          })}
          {!loading && filteredItems.length === 0 ? <div className="empty-state">No agents found for the selected filters.</div> : null}
        </div>
      </section>

      <section className="feature-content">
        <div className="feature-toolbar">
          <div>
            <h2>{selectedAgent?.target_name ?? "Select an agent"}</h2>
            <p className="muted-text">
              {selectedAgent
                ? `${backendLabel(selectedAgent.backend)} / ${selectedAgent.directory_path}`
                : "Choose an execution target from the left to inspect details and logs."}
            </p>
          </div>
          {selectedAgent ? (
            <div className="feature-toolbar-actions icon-actions">
              <Tooltip content={`Current status: ${selectedAgent.status}.`}>
                <span className={`status-pill state-${statusClass(selectedAgent.status)}`}>
                  {selectedAgent.status}
                </span>
              </Tooltip>
              {showStart ? (
                <Tooltip content="Start the selected VM.">
                  <button type="button" onClick={() => void runAgentAction("start")} disabled={!canMutate} aria-label="Start VM">
                    {busyAction === "start" ? "..." : <StartIcon />}
                  </button>
                </Tooltip>
              ) : null}
              {showShutdown ? (
                <Tooltip content="Request a graceful shutdown for the selected VM.">
                  <button
                    type="button"
                    onClick={() => {
                      if (window.confirm(`Shut down ${selectedAgent.target_name}?`)) {
                        void runAgentAction("shutdown");
                      }
                    }}
                    disabled={!canMutate}
                    aria-label="Shut down VM"
                  >
                    {busyAction === "shutdown" ? "..." : <PowerIcon />}
                  </button>
                </Tooltip>
              ) : null}
              {showContainerStop ? (
                <Tooltip content="Stop the selected worker job.">
                  <button
                    type="button"
                    onClick={() => {
                      if (window.confirm(`Stop ${selectedAgent.target_name}?`)) {
                        void runAgentAction("stop");
                      }
                    }}
                    disabled={!canMutate}
                    aria-label="Stop worker job"
                  >
                    {busyAction === "stop" ? "..." : <StopIcon />}
                  </button>
                </Tooltip>
              ) : null}
              {showEnable ? (
                <Tooltip content="Enable the guest agent.">
                  <button type="button" onClick={() => void runAgentAction("enable-agent")} disabled={!canMutate} aria-label="Enable agent">
                    <EnableIcon />
                  </button>
                </Tooltip>
              ) : null}
              {showDisable ? (
                <Tooltip content="Disable the guest agent.">
                  <button
                    type="button"
                    onClick={() => {
                      if (window.confirm(`Disable the agent for ${selectedAgent.target_name}?`)) {
                        void runAgentAction("disable-agent");
                      }
                    }}
                    disabled={!canMutate}
                    aria-label="Disable agent"
                  >
                    <DisableIcon />
                  </button>
                </Tooltip>
              ) : null}
              {showKill ? (
                <Tooltip content="Force-kill the currently running guest agent process.">
                  <button
                    type="button"
                    onClick={() => {
                      if (window.confirm(`Kill the running agent on ${selectedAgent.target_name}?`)) {
                        void runAgentAction("kill-agent", { pid: agentPid });
                      }
                    }}
                    disabled={!canMutate}
                    aria-label="Kill agent"
                  >
                    <KillIcon />
                  </button>
                </Tooltip>
              ) : null}
              {showResume ? (
                <Tooltip content={showResumeForm ? "Close the resume form." : "Open the resume form."}>
                  <button type="button" className={showResumeForm ? "primary" : ""} onClick={() => setShowResumeForm((current) => !current)} disabled={!canMutate} aria-label="Resume agent">
                    <ResumeIcon />
                  </button>
                </Tooltip>
              ) : null}
              {showDestroy ? (
                <Tooltip content={selectedAgent.backend === "container_worker_vm" ? "Delete the worker job record from the worker." : "Destroy the VM and remove local VM state."}>
                  <button
                    type="button"
                    className="danger"
                    onClick={() => {
                      if (window.confirm(`Destroy ${selectedAgent.target_name}?`)) {
                        void runAgentAction("destroy");
                      }
                    }}
                    disabled={!canMutate}
                    aria-label="Destroy target"
                  >
                    <DestroyIcon />
                  </button>
                </Tooltip>
              ) : null}
            </div>
          ) : null}
        </div>

        {status ? <div className="callout success-callout">{status}</div> : null}
        {error ? <div className="callout error-callout">{error}</div> : null}

        {selectedAgent ? (
          <>
            {showResumeForm ? (
              <article className="panel compact-panel">
                <div className="panel-header">
                  <h3>Resume agent</h3>
                  <span className="panel-meta">{selectedAgent.session_id ?? selectedAgent.agent_id}</span>
                </div>
                <div className="config-field-grid">
                  <label className="field-stack">
                    <span>Prompt</span>
                    <textarea rows={5} value={resumePrompt} onChange={(event) => setResumePrompt(event.target.value)} />
                  </label>
                  <label className="field-stack">
                    <span>Model override</span>
                    <input value={resumeModel} onChange={(event) => setResumeModel(event.target.value)} placeholder="Optional" />
                  </label>
                </div>
                <div className="inline-actions compact-actions">
                  <button
                    className="primary"
                    type="button"
                    disabled={!canMutate || !resumePrompt.trim()}
                    onClick={() =>
                      void runAgentAction("resume-agent", {
                        prompt: resumePrompt.trim(),
                        model: resumeModel.trim() || null,
                        resume_mode: "same_context",
                      })
                    }
                  >
                    {busyAction === "resume-agent" ? "Resuming..." : "Resume agent"}
                  </button>
                </div>
              </article>
            ) : null}

            <div className="feature-grid">
              <article className="panel">
                <div className="panel-header">
                  <h3>Target</h3>
                  <span className="panel-meta">{selectedAgent.backend}</span>
                </div>
                <dl className="property-grid">
                  <div>
                    <dt>Agent id</dt>
                    <dd>{selectedAgent.agent_id}</dd>
                  </div>
                  <div>
                    <dt>Session</dt>
                    <dd>{selectedAgent.session_id ?? "None"}</dd>
                  </div>
                  <div>
                    <dt>Directory</dt>
                    <dd>{selectedAgent.directory_path}</dd>
                  </div>
                  <div>
                    <dt>Updated</dt>
                    <dd>{formatTimestamp(selectedAgent.updated_at)}</dd>
                  </div>
                  <div>
                    <dt>Started</dt>
                    <dd>{formatTimestamp(selectedAgent.started_at)}</dd>
                  </div>
                  <div>
                    <dt>Finished</dt>
                    <dd>{formatTimestamp(selectedAgent.finished_at)}</dd>
                  </div>
                </dl>
              </article>

              <article className="panel">
                <div className="panel-header">
                  <h3>{selectedAgent.backend === "container_worker_vm" ? "Worker job" : "VM host"}</h3>
                  <span className="panel-meta">{selectedAgent.status}</span>
                </div>
                <dl className="property-grid">
                  {selectedAgent.backend === "container_worker_vm" ? (
                    <>
                      <div>
                        <dt>Job id</dt>
                        <dd>{hostString(selectedAgent.host, "worker_job_id") ?? "Unknown"}</dd>
                      </div>
                      <div>
                        <dt>Worker URL</dt>
                        <dd>{hostString(selectedAgent.host, "worker_url") ?? "Unknown"}</dd>
                      </div>
                      <div>
                        <dt>Image</dt>
                        <dd>{hostString(selectedAgent.host, "image") ?? "Unknown"}</dd>
                      </div>
                      <div>
                        <dt>Worker status</dt>
                        <dd>{hostString(selectedAgent.host, "last_status") ?? selectedAgent.status}</dd>
                      </div>
                    </>
                  ) : (
                    <>
                      <div>
                        <dt>Power</dt>
                        <dd>{powerState}</dd>
                      </div>
                      <div>
                        <dt>IP address</dt>
                        <dd>{hostString(selectedAgent.host, "ip_address") ?? "Unknown"}</dd>
                      </div>
                      <div>
                        <dt>Agent state</dt>
                        <dd>{agentState}</dd>
                      </div>
                      <div>
                        <dt>Agent pid</dt>
                        <dd>{agentPid ?? "Not running"}</dd>
                      </div>
                      <div>
                        <dt>Repositories</dt>
                        <dd>{hostStringList(guestAgent, "repo_names").join(", ") || "None"}</dd>
                      </div>
                      <div>
                        <dt>Issue</dt>
                        <dd>{hostString(selectedAgent.host, "issue_number") ?? "None"}</dd>
                      </div>
                    </>
                  )}
                </dl>
              </article>
            </div>

            {selectedLabels.length > 0 ? (
              <article className="panel compact-panel">
                <div className="panel-header">
                  <h3>Labels</h3>
                  <span className="panel-meta">{selectedLabels.length} entries</span>
                </div>
                <dl className="property-grid vm-label-grid">
                  {selectedLabels.map(([key, value]) => (
                    <div key={key}>
                      <dt>{key}</dt>
                      <dd>{value}</dd>
                    </div>
                  ))}
                </dl>
              </article>
            ) : null}

            {selectedMetadata ? (
              <details className="panel compact-disclosure">
                <summary className="panel-header compact-disclosure-summary">
                  <h3>Metadata</h3>
                  <span className="panel-meta">Worker-supplied context</span>
                </summary>
                <pre className="log-view compact-log-view">{JSON.stringify(selectedMetadata, null, 2)}</pre>
              </details>
            ) : null}

            {actionLog.length > 0 ? (
              <details className="panel compact-disclosure">
                <summary className="panel-header compact-disclosure-summary">
                  <h3>Recent actions</h3>
                  <span className="panel-meta">{actionLog.length} recent</span>
                </summary>
                <pre className="log-view compact-log-view">
                  {actionLog.map((entry) => `[${formatTimestamp(entry.at)}] ${entry.message}`).join("\n")}
                </pre>
              </details>
            ) : null}

            <article className="panel">
              <div className="panel-header">
                <h3>Logs</h3>
                <span className="panel-meta">{logTitle}</span>
              </div>
              <pre className="log-view vm-agent-log">{agentLog}</pre>
            </article>
          </>
        ) : (
          <div className="empty-state">Select an agent to inspect details and logs.</div>
        )}
      </section>
    </div>
  );
}
