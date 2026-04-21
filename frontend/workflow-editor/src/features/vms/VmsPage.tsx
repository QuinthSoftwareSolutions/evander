import { useCallback, useEffect, useMemo, useState } from "react";
import type { Dispatch, ReactNode, SetStateAction } from "react";

import { fetchJson, formatTimestamp } from "../admin/api";
import { useAdminUi } from "../admin/AdminContext";
import { Tooltip } from "../admin/Tooltip";
import { type VmActionResponse, type VmListResponse, type VmLogResponse, type VmRecord } from "../admin/types";

type ActionLogEntry = {
  at: string;
  message: string;
  tone: "info" | "error";
};

type VmCreateFormState = {
  vmName: string;
  sessionId: string;
  model: string;
  dotnet: boolean;
  nuget: boolean;
  playwright: boolean;
  mssql: boolean;
  mssqlInitRoot: string;
};

const EMPTY_VM_FORM: VmCreateFormState = {
  vmName: "",
  sessionId: "",
  model: "",
  dotnet: false,
  nuget: false,
  playwright: false,
  mssql: false,
  mssqlInitRoot: "",
};

const REFRESH_MS = 5000;
const VM_SELECTION_STORAGE_KEY = "admin-web:vms:selected-vm";

async function loadVmList(directoryPath: string, directOnly: boolean): Promise<VmListResponse> {
  const query = new URLSearchParams({
    directory_path: directoryPath,
    direct_only: String(directOnly),
    include_inactive: "true",
  });
  return fetchJson<VmListResponse>(`/api/admin/vms?${query.toString()}`);
}

async function loadVmLog(vm: VmRecord | null): Promise<string> {
  if (!vm) return "No VM selected.";
  const query = new URLSearchParams({ lines: "200" });
  if (vm.agent.preferred_repo_name) {
    query.set("repo_name", vm.agent.preferred_repo_name);
  }
  const payload = await fetchJson<VmLogResponse>(`/api/admin/vms/${encodeURIComponent(vm.vm_name)}/agent-log?${query.toString()}`);
  return payload.text || "No agent log output yet.";
}

function appendLogEntry(
  setActionLog: Dispatch<SetStateAction<ActionLogEntry[]>>,
  message: string,
  tone: ActionLogEntry["tone"],
) {
  setActionLog((current) => [{ at: new Date().toISOString(), message, tone }, ...current].slice(0, 30));
}

function VmIcon({ children, viewBox = "0 0 24 24" }: { children: ReactNode; viewBox?: string }) {
  return (
    <svg aria-hidden="true" viewBox={viewBox} fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
      {children}
    </svg>
  );
}

function VmRunningIcon() {
  return (
    <VmIcon>
      <path d="M9 7.5v9l7-4.5z" fill="currentColor" stroke="none" />
    </VmIcon>
  );
}

function VmStoppedIcon() {
  return (
    <VmIcon>
      <rect x="7.5" y="7.5" width="9" height="9" rx="1.5" fill="currentColor" stroke="none" />
    </VmIcon>
  );
}

function StartIcon() {
  return (
    <VmIcon>
      <path d="M8 6.5v11l8-5.5z" fill="currentColor" stroke="none" />
    </VmIcon>
  );
}

function PowerIcon() {
  return (
    <VmIcon>
      <path d="M12 3.5v7" />
      <path d="M7.2 6.8a8 8 0 1 0 9.6 0" />
    </VmIcon>
  );
}

function EnableIcon() {
  return (
    <VmIcon>
      <circle cx="12" cy="12" r="8" />
      <path d="m8.8 12.2 2.1 2.1 4.5-4.8" />
    </VmIcon>
  );
}

function DisableIcon() {
  return (
    <VmIcon>
      <circle cx="12" cy="12" r="8" />
      <path d="M8.2 15.8 15.8 8.2" />
    </VmIcon>
  );
}

function KillAgentIcon() {
  return (
    <VmIcon>
      <path d="M9 4.8h6l4.2 4.2v6L15 19.2H9L4.8 15v-6z" />
      <path d="m9.4 9.4 5.2 5.2" />
      <path d="m14.6 9.4-5.2 5.2" />
    </VmIcon>
  );
}

function ResumeIcon() {
  return (
    <VmIcon>
      <path d="M7.5 7.5H4.5v-3" />
      <path d="M4.7 11a7.5 7.5 0 1 1 2.2 6.1" />
    </VmIcon>
  );
}

function ExpandResumeIcon() {
  return (
    <VmIcon>
      <path d="m7 10 5 5 5-5" />
    </VmIcon>
  );
}

function DestroyIcon() {
  return (
    <VmIcon>
      <path d="M5.5 7.5h13" />
      <path d="M9 7.5v-2h6v2" />
      <path d="M8.5 10.5v6" />
      <path d="M12 10.5v6" />
      <path d="M15.5 10.5v6" />
      <path d="M7.5 7.5v10.2a1.3 1.3 0 0 0 1.3 1.3h6.4a1.3 1.3 0 0 0 1.3-1.3V7.5" />
    </VmIcon>
  );
}

export function VmsPage() {
  const { privilegedUnlocked, selectedDirectoryPath } = useAdminUi();
  const [items, setItems] = useState<VmRecord[]>([]);
  const [selectedVmName, setSelectedVmName] = useState<string | null>(null);
  const [agentLog, setAgentLog] = useState("Loading...");
  const [resumePrompt, setResumePrompt] = useState("");
  const [resumeModel, setResumeModel] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [inlineValidation, setInlineValidation] = useState<string>("");
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [actionLog, setActionLog] = useState<ActionLogEntry[]>([]);
  const [showCreateVm, setShowCreateVm] = useState(false);
  const [showResumeForm, setShowResumeForm] = useState(false);
  const [createVmForm, setCreateVmForm] = useState<VmCreateFormState>(EMPTY_VM_FORM);
  const [selectionHydrated, setSelectionHydrated] = useState(false);

  useEffect(() => {
    const saved = window.localStorage.getItem(VM_SELECTION_STORAGE_KEY);
    if (saved) {
      setSelectedVmName(saved);
    }
    setSelectionHydrated(true);
  }, []);

  useEffect(() => {
    if (!selectionHydrated) return;
    if (selectedVmName) {
      window.localStorage.setItem(VM_SELECTION_STORAGE_KEY, selectedVmName);
      return;
    }
    window.localStorage.removeItem(VM_SELECTION_STORAGE_KEY);
  }, [selectedVmName, selectionHydrated]);

  const sortedItems = useMemo(
    () =>
      [...items].sort((left, right) =>
        left.vm_name.localeCompare(right.vm_name, undefined, { numeric: true, sensitivity: "base" }),
      ),
    [items],
  );

  const selectedVm = useMemo(
    () => sortedItems.find((item) => item.vm_name === selectedVmName) ?? sortedItems[0] ?? null,
    [sortedItems, selectedVmName],
  );

  const refreshVms = useCallback(async () => {
    const payload = await loadVmList(selectedDirectoryPath, true);
    setItems(payload.items);
    setSelectedVmName((current) => {
      if (current && payload.items.some((item) => item.vm_name === current)) return current;
      return payload.items[0]?.vm_name ?? null;
    });
  }, [selectedDirectoryPath]);

  useEffect(() => {
    let cancelled = false;

    const refresh = async () => {
      try {
        await refreshVms();
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
    };

    void refresh();
    const timer = window.setInterval(() => {
      void refresh();
    }, REFRESH_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [refreshVms]);

  useEffect(() => {
    if (!selectedVm) {
      setAgentLog("No VM selected.");
      return;
    }

    let cancelled = false;

    const refreshLog = async (showLoadingState: boolean) => {
      if (showLoadingState) {
        setAgentLog("Loading...");
      }
      try {
        const text = await loadVmLog(selectedVm);
        if (!cancelled) {
          setAgentLog((current) => (current === text ? current : text));
        }
      } catch (reason: unknown) {
        if (!cancelled) {
          const message = reason instanceof Error ? reason.message : String(reason);
          setAgentLog((current) => (current === message ? current : message));
        }
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
  }, [selectedVm?.vm_name, selectedVm?.agent.preferred_repo_name]);

  async function handleManualRefresh() {
    setLoading(true);
    try {
      await refreshVms();
      setError(null);
      setStatus("Refreshed VM list.");
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  }

  async function runVmAction(action: string, body: Record<string, unknown> = {}) {
    if (!selectedVm) return;
    setBusyAction(action);
    setInlineValidation("");
    try {
      const query = new URLSearchParams({
        directory_path: selectedDirectoryPath,
        direct_only: "true",
      });
      const payload = await fetchJson<VmActionResponse>(
        `/api/admin/vms/${encodeURIComponent(selectedVm.vm_name)}/actions/${encodeURIComponent(action)}?${query.toString()}`,
        {
          method: "POST",
          body: JSON.stringify(body),
        },
      );
      appendLogEntry(setActionLog, `${selectedVm.vm_name}: ${payload.message}`, "info");
      setStatus(payload.message);
      setError(null);
      if (action === "resume-agent") {
        setResumePrompt("");
        setResumeModel("");
        setShowResumeForm(false);
      }
      await refreshVms();
      if (action === "destroy") {
        setShowResumeForm(false);
      } else {
        setSelectedVmName(selectedVm.vm_name);
      }
    } catch (reason: unknown) {
      const message = reason instanceof Error ? reason.message : String(reason);
      setError(message);
      setInlineValidation(action === "start" || action === "resume-agent" ? message : "");
      setStatus(null);
      appendLogEntry(setActionLog, `${selectedVm.vm_name}: ${message}`, "error");
    } finally {
      setBusyAction(null);
    }
  }

  async function handleCreateVm() {
    if (!createVmForm.vmName.trim()) {
      setInlineValidation("VM name is required.");
      return;
    }
    setBusyAction("create-vm");
    setInlineValidation("");
    try {
      const payload = await fetchJson<VmActionResponse>("/api/admin/vms", {
        method: "POST",
        body: JSON.stringify({
          vm_name: createVmForm.vmName,
          session_id: createVmForm.sessionId || null,
          model: createVmForm.model || null,
          dotnet: createVmForm.dotnet,
          nuget: createVmForm.nuget,
          playwright: createVmForm.playwright,
          mssql: createVmForm.mssql,
          mssql_init_root: createVmForm.mssql ? createVmForm.mssqlInitRoot || null : null,
          directory_path: selectedDirectoryPath,
        }),
      });
      appendLogEntry(setActionLog, `${payload.vm_name}: ${payload.message}`, "info");
      setStatus(payload.message);
      setError(null);
      setShowCreateVm(false);
      setCreateVmForm(EMPTY_VM_FORM);
      await refreshVms();
      setSelectedVmName(payload.vm_name);
    } catch (reason: unknown) {
      const message = reason instanceof Error ? reason.message : String(reason);
      setError(message);
      setStatus(null);
      setInlineValidation(message);
      appendLogEntry(setActionLog, `Create VM: ${message}`, "error");
    } finally {
      setBusyAction(null);
    }
  }

  const supportsAgentControls = Boolean(selectedVm?.vm_name) && selectedVm?.agent.state !== "unknown";
  const showStart = selectedVm?.libvirt_state !== "running";
  const showShutdown = selectedVm?.libvirt_state === "running";
  const showEnable = supportsAgentControls && selectedVm?.agent.state === "disabled";
  const showDisable = supportsAgentControls && selectedVm?.agent.state !== "disabled";
  const showKill = selectedVm?.agent.state === "running" && selectedVm.agent.pid != null;
  const showResume = supportsAgentControls && selectedVm?.agent.state !== "disabled";
  const selectedVmLogName = selectedVm?.agent.preferred_repo_name ?? "Agent output";
  const actionDisabled = busyAction !== null || !privilegedUnlocked;
  const resumeButtonLabel = showResumeForm ? "Close resume form" : "Resume agent";

  return (
    <div className="feature-page vms-page">
      <section className="feature-sidebar">
        <div className="feature-sidebar-header">
          <div>
            <div className="eyebrow">Lifecycle control</div>
            <h1>VMs</h1>
          </div>
          <div className="inline-actions compact-actions">
            <button
              type="button"
              className={showCreateVm ? "" : "primary"}
              onClick={() => {
                setShowCreateVm((current) => !current);
                setInlineValidation("");
              }}
              disabled={actionDisabled}
            >
              {showCreateVm ? "Close new VM" : "New VM"}
            </button>
          </div>
        </div>
        {!privilegedUnlocked ? (
          <div className="callout warning-callout">Unlock privileged operations to create VMs or change VM state.</div>
        ) : null}
        <div className="feature-list vm-list">
          {sortedItems.map((item) => (
            <button
              key={item.vm_name}
              type="button"
              className={`feature-list-item vm-list-item${selectedVm?.vm_name === item.vm_name ? " selected" : ""}`}
              onClick={() => {
                setSelectedVmName(item.vm_name);
                setInlineValidation("");
              }}
            >
              <div className="feature-list-title-row vm-list-item-row">
                <strong>{item.vm_name}</strong>
                <Tooltip content={item.libvirt_state === "running" ? "VM is currently running." : "VM is currently stopped."}>
                  <span
                    className={`status-pill vm-status-pill state-${item.libvirt_state === "running" ? "running" : "idle"}`}
                    aria-label={item.libvirt_state === "running" ? "VM running" : "VM stopped"}
                  >
                    {item.libvirt_state === "running" ? <VmRunningIcon /> : <VmStoppedIcon />}
                  </span>
                </Tooltip>
              </div>
            </button>
          ))}
          {!loading && items.length === 0 ? <div className="empty-state">No VMs found for the selected directory scope.</div> : null}
        </div>
      </section>

      <section className="feature-content">
        <div className="feature-toolbar">
          <div>
            <h2>{selectedVm?.vm_name ?? "Select a VM"}</h2>
            <p className="muted-text">
              {selectedVm
                ? "Inspect machine state, manage the agent lifecycle, and review recent operator and agent logs."
                : "Choose a VM from the left to inspect details and run actions."}
            </p>
          </div>
          {selectedVm ? (
            <div className="feature-toolbar-actions icon-actions">
              <Tooltip content={`Host libvirt state for ${selectedVm.vm_name}: ${selectedVm.libvirt_state}.`}>
                <span className={`status-pill state-${selectedVm.libvirt_state === "running" ? "running" : "idle"}`}>
                  VM {selectedVm.libvirt_state}
                </span>
              </Tooltip>
              <Tooltip content={`Guest agent state for ${selectedVm.vm_name}: ${selectedVm.agent.state}.`}>
                <span className={`status-pill state-${selectedVm.agent.state === "running" ? "running" : selectedVm.agent.state === "disabled" ? "failed" : "idle"}`}>
                  Agent {selectedVm.agent.state}
                </span>
              </Tooltip>
              {showStart ? (
                <Tooltip content="Start the selected VM through libvirt on the host.">
                  <button
                    type="button"
                    onClick={() => void runVmAction("start")}
                    disabled={actionDisabled}
                    aria-label={busyAction === "start" ? "Starting VM" : "Start VM"}
                  >
                    {busyAction === "start" ? "…" : <StartIcon />}
                  </button>
                </Tooltip>
              ) : null}
              {showShutdown ? (
                <Tooltip content="Request a graceful guest shutdown for the selected VM.">
                  <button
                    type="button"
                    onClick={() => {
                      if (window.confirm(`Shut down ${selectedVm.vm_name}?`)) {
                        void runVmAction("shutdown");
                      }
                    }}
                    disabled={actionDisabled}
                    aria-label={busyAction === "shutdown" ? "Shutting down VM" : "Shut down VM"}
                  >
                    {busyAction === "shutdown" ? "…" : <PowerIcon />}
                  </button>
                </Tooltip>
              ) : null}
              {showEnable ? (
                <Tooltip content="Enable the guest agent so prompts and session automation can run again.">
                  <button
                    type="button"
                    onClick={() => void runVmAction("enable-agent")}
                    disabled={actionDisabled}
                    aria-label="Enable agent"
                  >
                    <EnableIcon />
                  </button>
                </Tooltip>
              ) : null}
              {showDisable ? (
                <Tooltip content="Disable the guest agent without shutting down the VM.">
                  <button
                    type="button"
                    onClick={() => {
                      if (window.confirm(`Disable the agent for ${selectedVm.vm_name}?`)) {
                        void runVmAction("disable-agent");
                      }
                    }}
                    disabled={actionDisabled}
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
                      if (window.confirm(`Kill the running agent on ${selectedVm.vm_name}?`)) {
                        void runVmAction("kill-agent", { pid: selectedVm.agent.pid });
                      }
                    }}
                    disabled={actionDisabled}
                    aria-label="Kill agent"
                  >
                    <KillAgentIcon />
                  </button>
                </Tooltip>
              ) : null}
              {showResume ? (
                <Tooltip content={showResumeForm ? "Collapse the resume form." : "Open the resume form and send a prompt into the current session."}>
                  <button
                    type="button"
                    className={showResumeForm ? "primary" : ""}
                    onClick={() => {
                      setShowResumeForm((current) => !current);
                      setInlineValidation("");
                    }}
                    disabled={actionDisabled}
                    aria-label={resumeButtonLabel}
                  >
                    {showResumeForm ? <ExpandResumeIcon /> : <ResumeIcon />}
                  </button>
                </Tooltip>
              ) : null}
              <Tooltip content="Destroy the VM and remove its associated local host-side state.">
                <button
                  type="button"
                  className="danger"
                  onClick={() => {
                    const message = selectedVm.issue_number
                      ? `Destroy ${selectedVm.vm_name} and remove its associated issue-VM state?`
                      : `Destroy ${selectedVm.vm_name} and remove its local session state?`;
                    if (window.confirm(message)) {
                      void runVmAction("destroy");
                    }
                  }}
                  disabled={actionDisabled}
                  aria-label="Destroy VM"
                >
                  <DestroyIcon />
                </button>
              </Tooltip>
            </div>
          ) : null}
        </div>

        {status ? <div className="callout success-callout">{status}</div> : null}
        {error ? <div className="callout error-callout">{error}</div> : null}
        {inlineValidation ? <div className="callout warning-callout">{inlineValidation}</div> : null}

        {showCreateVm ? (
          <article className="panel">
            <div className="panel-header">
              <h3>Create standalone VM</h3>
              <span className="panel-meta">Uses the current directory context unless overridden.</span>
            </div>
            <div className="panel-body">
              <div className="config-field-grid">
                <label className="field-stack">
                  <span>VM name</span>
                  <input
                    value={createVmForm.vmName}
                    onChange={(event) => setCreateVmForm((current) => ({ ...current, vmName: event.target.value }))}
                    placeholder="lab-vm-1"
                  />
                </label>
                <label className="field-stack">
                  <span>Session ID</span>
                  <input
                    value={createVmForm.sessionId}
                    onChange={(event) => setCreateVmForm((current) => ({ ...current, sessionId: event.target.value }))}
                    placeholder="Optional"
                  />
                </label>
                <label className="field-stack">
                  <span>Model</span>
                  <input
                    value={createVmForm.model}
                    onChange={(event) => setCreateVmForm((current) => ({ ...current, model: event.target.value }))}
                    placeholder="Uses directory default when empty"
                  />
                </label>
              </div>
              <div className="feature-grid">
                <article className="panel">
                  <div className="panel-header">
                    <h3>Bootstrap options</h3>
                  </div>
                  <div className="panel-body">
                    <label className="checkbox-row">
                      <input
                        type="checkbox"
                        checked={createVmForm.dotnet}
                        onChange={(event) => setCreateVmForm((current) => ({ ...current, dotnet: event.target.checked }))}
                      />
                      <span>Bootstrap .NET</span>
                    </label>
                    <label className="checkbox-row">
                      <input
                        type="checkbox"
                        checked={createVmForm.nuget}
                        onChange={(event) => setCreateVmForm((current) => ({ ...current, nuget: event.target.checked }))}
                      />
                      <span>Bootstrap NuGet</span>
                    </label>
                    <label className="checkbox-row">
                      <input
                        type="checkbox"
                        checked={createVmForm.playwright}
                        onChange={(event) => setCreateVmForm((current) => ({ ...current, playwright: event.target.checked }))}
                      />
                      <span>Bootstrap Playwright</span>
                    </label>
                    <label className="checkbox-row">
                      <input
                        type="checkbox"
                        checked={createVmForm.mssql}
                        onChange={(event) => setCreateVmForm((current) => ({ ...current, mssql: event.target.checked }))}
                      />
                      <span>Bootstrap MSSQL</span>
                    </label>
                    {createVmForm.mssql ? (
                      <label className="field-stack">
                        <span>MSSQL init root</span>
                        <input
                          value={createVmForm.mssqlInitRoot}
                          onChange={(event) => setCreateVmForm((current) => ({ ...current, mssqlInitRoot: event.target.value }))}
                          placeholder="/path/to/mssql-init"
                        />
                      </label>
                    ) : null}
                  </div>
                </article>
              </div>
              <div className="inline-actions">
                <button className="primary" type="button" onClick={() => void handleCreateVm()} disabled={actionDisabled}>
                  {busyAction === "create-vm" ? "Creating..." : "Create VM"}
                </button>
              </div>
            </div>
          </article>
        ) : null}

        {selectedVm ? (
          <>
            {showResumeForm ? (
              <article className="panel compact-panel">
                <div className="panel-header">
                  <h3>Resume agent</h3>
                  <span className="panel-meta">Send a prompt into the current session.</span>
                </div>
                <div className="config-field-grid">
                  <label className="field-stack">
                    <span>Prompt</span>
                    <textarea
                      rows={5}
                      value={resumePrompt}
                      onChange={(event) => setResumePrompt(event.target.value)}
                      placeholder="Prompt"
                    />
                  </label>
                  <label className="field-stack">
                    <span>Model override</span>
                    <input
                      value={resumeModel}
                      onChange={(event) => setResumeModel(event.target.value)}
                      placeholder="Optional; uses session or directory default"
                    />
                  </label>
                </div>
                <div className="inline-actions compact-actions">
                  <button
                    className="primary"
                    type="button"
                    disabled={actionDisabled}
                    onClick={() => {
                      if (!resumePrompt.trim()) {
                        setInlineValidation("Resume prompt is required.");
                        return;
                      }
                      void runVmAction("resume-agent", {
                        prompt: resumePrompt.trim(),
                        model: resumeModel.trim() || null,
                        resume_mode: "same_context",
                      });
                    }}
                  >
                    {busyAction === "resume-agent" ? "Resuming..." : "Resume agent"}
                  </button>
                </div>
              </article>
            ) : null}

            <div className="feature-grid">
              <article className="panel">
                <div className="panel-header">
                  <h3>Machine status</h3>
                  <span className="panel-meta">{selectedVm.issue_number ? `Issue ${selectedVm.issue_number}` : "Standalone VM"}</span>
                </div>
                <dl className="property-grid">
                  <div>
                    <dt>Sessions</dt>
                    <dd>{selectedVm.session_ids.length > 0 ? selectedVm.session_ids.join(", ") : "None"}</dd>
                  </div>
                  <div>
                    <dt>Directories</dt>
                    <dd>{selectedVm.directory_paths.length > 0 ? selectedVm.directory_paths.join(", ") : "None"}</dd>
                  </div>
                  <div>
                    <dt>IP address</dt>
                    <dd>{selectedVm.ip_address ?? "Unknown"}</dd>
                  </div>
                  <div>
                    <dt>Agent pid</dt>
                    <dd>{selectedVm.agent.pid ?? "Not running"}</dd>
                  </div>
                  <div>
                    <dt>Repositories</dt>
                    <dd>{selectedVm.agent.repo_names.length > 0 ? selectedVm.agent.repo_names.join(", ") : "None"}</dd>
                  </div>
                  <div>
                    <dt>Updated</dt>
                    <dd>{formatTimestamp(selectedVm.last_state_update_at)}</dd>
                  </div>
                </dl>
              </article>

              {Object.keys(selectedVm.labels).length > 0 ? (
                <article className="panel compact-panel">
                  <div className="panel-header">
                    <h3>Labels</h3>
                    <span className="panel-meta">{Object.keys(selectedVm.labels).length} entries</span>
                  </div>
                  <dl className="property-grid vm-label-grid">
                    {Object.entries(selectedVm.labels)
                      .sort(([leftKey], [rightKey]) => leftKey.localeCompare(rightKey, undefined, { sensitivity: "base" }))
                      .map(([key, value]) => (
                        <div key={key}>
                          <dt>{key}</dt>
                          <dd>{value}</dd>
                        </div>
                      ))}
                  </dl>
                </article>
              ) : null}
            </div>

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
                <h3>Agent output</h3>
                <span className="panel-meta">{selectedVmLogName}</span>
              </div>
              <pre className="log-view vm-agent-log">{agentLog}</pre>
            </article>
          </>
        ) : (
          <div className="empty-state">Select a VM to inspect details and logs.</div>
        )}
      </section>
    </div>
  );
}
