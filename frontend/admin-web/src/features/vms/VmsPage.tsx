import { useCallback, useEffect, useMemo, useState } from "react";
import type { Dispatch, SetStateAction } from "react";

import { fetchJson, formatTimestamp } from "../admin/api";
import { useAdminUi } from "../admin/AdminContext";
import { RecordWorkspace } from "../admin/RecordWorkspace";
import { readAdminHashQuery } from "../admin/hash";
import { DestroyIcon, PowerIcon, StartIcon, StopIcon, VmRunningIcon, VmStoppedIcon } from "../admin/PageActionIcons";
import { Tooltip } from "../admin/Tooltip";
import { type VmActionResponse, type VmListResponse, type VmRecord } from "../admin/types";

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

function appendLogEntry(
  setActionLog: Dispatch<SetStateAction<ActionLogEntry[]>>,
  message: string,
  tone: ActionLogEntry["tone"],
) {
  setActionLog((current) => [{ at: new Date().toISOString(), message, tone }, ...current].slice(0, 30));
}

export function VmsPage() {
  const { privilegedUnlocked, selectedDirectoryPath } = useAdminUi();
  const [items, setItems] = useState<VmRecord[]>([]);
  const [selectedVmName, setSelectedVmName] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [inlineValidation, setInlineValidation] = useState<string>("");
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [actionLog, setActionLog] = useState<ActionLogEntry[]>([]);
  const [showCreateVm, setShowCreateVm] = useState(false);
  const [createVmForm, setCreateVmForm] = useState<VmCreateFormState>(EMPTY_VM_FORM);
  const [selectionHydrated, setSelectionHydrated] = useState(false);

  useEffect(() => {
    const saved = readAdminHashQuery().get("vm_name") ?? window.localStorage.getItem(VM_SELECTION_STORAGE_KEY);
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
      await refreshVms();
      if (action !== "destroy") {
        setSelectedVmName(selectedVm.vm_name);
      }
    } catch (reason: unknown) {
      const message = reason instanceof Error ? reason.message : String(reason);
      setError(message);
      setInlineValidation(action === "start" ? message : "");
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

  const showStart = selectedVm?.libvirt_state !== "running";
  const showShutdown = selectedVm?.libvirt_state === "running";
  const actionDisabled = busyAction !== null || !privilegedUnlocked;

  return (
    <RecordWorkspace
      recordLabel="VM"
      title="VMs"
      description="Compare virtual machine state and linked sessions. Select a VM for controls and guest details."
      columns={["VM", "Power state", "Sessions", "Address"]}
      records={sortedItems.map((item) => ({
        id: item.vm_name,
        title: item.vm_name,
        href: `#vms?${new URLSearchParams({ vm_name: item.vm_name })}`,
        cells: [
          {
            value: item.libvirt_state,
            content: (
              <span className={`status-pill state-${item.libvirt_state === "running" ? "running" : "idle"}`}>
                {item.libvirt_state}
              </span>
            ),
          },
          { value: item.session_ids.length },
          { value: item.ip_address, content: item.ip_address ?? "Unknown" },
        ],
        facts: [
          ["VM", item.vm_name],
          ["Provider", item.provider],
          ["Power state", item.libvirt_state],
          ["Sessions", item.session_ids.join(", ") || "None"],
          ["Directory", item.directory_paths.join(", ")],
          ["Address", item.ip_address],
          ["Updated", formatTimestamp(item.last_state_update_at)],
        ],
      }))}
      selectedId={selectedVmName}
      onSelect={(id) => {
        setSelectedVmName(id);
        setInlineValidation("");
      }}
      loading={loading}
      error={error}
      initialRecordOpen={Boolean(readAdminHashQuery().get("vm_name"))}
      recordRouteKeys="vm_name"
      actions={(openRecord) => (
        <>
          <button type="button" disabled={loading} onClick={() => void handleManualRefresh()}>
            Refresh
          </button>
          <button
            type="button"
            className="primary"
            disabled={actionDisabled}
            title={!privilegedUnlocked ? "Unlock privileged operations to create VMs" : undefined}
            onClick={() => {
              setShowCreateVm(true);
              setInlineValidation("");
              openRecord();
            }}
          >
            New VM
          </button>
        </>
      )}
    >
      <div className="feature-page vms-page">
        <section className="feature-sidebar">
          <div className="feature-sidebar-header">
            <div>
              <div className="eyebrow">VM host lifecycle</div>
              <h1>VMs</h1>
            </div>
            <div className="inline-actions compact-actions">
              <button type="button" onClick={() => void handleManualRefresh()} disabled={loading}>
                Refresh
              </button>
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
            <div className="callout warning-callout">
              Unlock privileged operations to create VMs or change VM state.
            </div>
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
                  <Tooltip
                    content={item.libvirt_state === "running" ? "VM is currently running." : "VM is currently stopped."}
                  >
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
            {!loading && items.length === 0 ? (
              <div className="empty-state">No VMs found for the selected directory scope.</div>
            ) : null}
          </div>
        </section>

        <section className="feature-content">
          <div className="feature-toolbar">
            <div>
              <h2>{selectedVm?.vm_name ?? "Select a VM"}</h2>
              <p className="muted-text">
                {selectedVm
                  ? "Inspect machine state and manage the VM host lifecycle."
                  : "Choose a VM from the left to inspect host details and run VM actions."}
              </p>
            </div>
            {selectedVm ? (
              <div className="feature-toolbar-actions icon-actions">
                <Tooltip content={`Host libvirt state for ${selectedVm.vm_name}: ${selectedVm.libvirt_state}.`}>
                  <span className={`status-pill state-${selectedVm.libvirt_state === "running" ? "running" : "idle"}`}>
                    VM {selectedVm.libvirt_state}
                  </span>
                </Tooltip>
                {showStart ? (
                  <Tooltip content="Start the selected VM through libvirt on the host.">
                    <button
                      type="button"
                      onClick={() => void runVmAction("start")}
                      disabled={actionDisabled}
                      aria-label="Start VM"
                    >
                      {busyAction === "start" ? "..." : <StartIcon />}
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
                      aria-label="Shut down VM"
                    >
                      {busyAction === "shutdown" ? "..." : <PowerIcon />}
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
                      onChange={(event) =>
                        setCreateVmForm((current) => ({ ...current, sessionId: event.target.value }))
                      }
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
                          onChange={(event) =>
                            setCreateVmForm((current) => ({ ...current, dotnet: event.target.checked }))
                          }
                        />
                        <span>Bootstrap .NET</span>
                      </label>
                      <label className="checkbox-row">
                        <input
                          type="checkbox"
                          checked={createVmForm.nuget}
                          onChange={(event) =>
                            setCreateVmForm((current) => ({ ...current, nuget: event.target.checked }))
                          }
                        />
                        <span>Bootstrap NuGet</span>
                      </label>
                      <label className="checkbox-row">
                        <input
                          type="checkbox"
                          checked={createVmForm.playwright}
                          onChange={(event) =>
                            setCreateVmForm((current) => ({ ...current, playwright: event.target.checked }))
                          }
                        />
                        <span>Bootstrap Playwright</span>
                      </label>
                      <label className="checkbox-row">
                        <input
                          type="checkbox"
                          checked={createVmForm.mssql}
                          onChange={(event) =>
                            setCreateVmForm((current) => ({ ...current, mssql: event.target.checked }))
                          }
                        />
                        <span>Bootstrap MSSQL</span>
                      </label>
                      {createVmForm.mssql ? (
                        <label className="field-stack">
                          <span>MSSQL init root</span>
                          <input
                            value={createVmForm.mssqlInitRoot}
                            onChange={(event) =>
                              setCreateVmForm((current) => ({ ...current, mssqlInitRoot: event.target.value }))
                            }
                            placeholder="/path/to/mssql-init"
                          />
                        </label>
                      ) : null}
                    </div>
                  </article>
                </div>
                <div className="inline-actions">
                  <button
                    className="primary"
                    type="button"
                    onClick={() => void handleCreateVm()}
                    disabled={actionDisabled}
                  >
                    {busyAction === "create-vm" ? "Creating..." : "Create VM"}
                  </button>
                </div>
              </div>
            </article>
          ) : null}

          {selectedVm ? (
            <>
              <div className="feature-grid">
                <article className="panel">
                  <div className="panel-header">
                    <h3>Machine status</h3>
                    <span className="panel-meta">
                      {selectedVm.issue_number ? `Issue ${selectedVm.issue_number}` : "Standalone VM"}
                    </span>
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
                      <dt>Provider</dt>
                      <dd>{selectedVm.provider}</dd>
                    </div>
                    <div>
                      <dt>IP address</dt>
                      <dd>{selectedVm.ip_address ?? "Unknown"}</dd>
                    </div>
                    <div>
                      <dt>Power state</dt>
                      <dd>{selectedVm.libvirt_state}</dd>
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
                        .sort(([leftKey], [rightKey]) =>
                          leftKey.localeCompare(rightKey, undefined, { sensitivity: "base" }),
                        )
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
            </>
          ) : (
            <div className="empty-state">Select a VM to inspect host details.</div>
          )}
        </section>
      </div>
    </RecordWorkspace>
  );
}
