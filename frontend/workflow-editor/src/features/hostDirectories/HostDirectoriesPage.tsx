import { useEffect, useMemo, useRef, useState } from "react";

import { fetchJson } from "../admin/api";

type HostDirectoryCapability = "list" | "import" | "delete";

type HostDirectoryDefinition = {
  display_name: string;
  description: string;
  root: string;
  capabilities: HostDirectoryCapability[];
  delete_requires_approval: boolean;
  max_import_bytes: number;
  max_import_entries: number;
  max_watch_entries: number;
  disabled: boolean;
};

type HostDirectoryHealth = {
  status?: string;
  available?: boolean;
  exists?: boolean;
  is_directory?: boolean;
  readable?: boolean;
  writable?: boolean;
  root_device?: number;
  checks?: {
    readable?: boolean;
    mount_boundary_verified?: boolean;
    quarantine_writable?: boolean;
  };
  message?: string | null;
  error?: string | null;
  error_class?: string | null;
};

type HostDirectoryRecord = HostDirectoryDefinition & {
  directory_id: string;
  defined_here?: boolean;
  source_directory_path?: string | null;
  health?: HostDirectoryHealth | null;
};

type HostDirectoryConfig = {
  host_directories?: Record<string, Partial<HostDirectoryDefinition>>;
};

type HostDirectoryState = {
  directory_path: string;
  revision: string;
  config: HostDirectoryConfig;
  effective_config: HostDirectoryConfig;
  host_directories: HostDirectoryRecord[];
  provenance: Record<string, unknown>;
};

type HostDirectoryHealthResponse = {
  directory_path: string;
  directory_id: string;
  health: HostDirectoryHealth;
};

type QuarantineEntry = {
  recovery_id: string;
  original_path?: string;
  relative_path?: string;
  deleted_at?: string;
  kind?: string;
  size?: number | null;
  session_id?: string | null;
  recovery_status?: "ready" | "missing_payload" | "corrupt_metadata" | "unsafe_payload";
  restore_available?: boolean;
  purge_available?: boolean;
};

type QuarantineState = {
  directory_path: string;
  directory_id: string;
  entries: QuarantineEntry[];
};

const DEFAULT_DEFINITION: HostDirectoryDefinition = {
  display_name: "Host directory",
  description: "Directory-scoped host filesystem resource.",
  root: "",
  capabilities: ["list", "import"],
  delete_requires_approval: true,
  max_import_bytes: 10 * 1024 * 1024,
  max_import_entries: 1_000,
  max_watch_entries: 10_000,
  disabled: false,
};

const DIRECTORY_ID_PATTERN = /^[a-z0-9][a-z0-9._-]{0,127}$/;
const CAPABILITIES: ReadonlyArray<HostDirectoryCapability> = ["list", "import", "delete"];

function stableConfigText(config: HostDirectoryConfig): string {
  return JSON.stringify(config);
}

function normalizeConfig(value: unknown): HostDirectoryConfig {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return value as HostDirectoryConfig;
}

function definitions(config: HostDirectoryConfig): Record<string, Partial<HostDirectoryDefinition>> {
  const value = config.host_directories;
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function definitionOnly(value: HostDirectoryDefinition): HostDirectoryDefinition {
  return {
    display_name: value.display_name,
    description: value.description,
    root: value.root,
    capabilities: [...value.capabilities],
    delete_requires_approval: value.delete_requires_approval,
    max_import_bytes: value.max_import_bytes,
    max_import_entries: value.max_import_entries,
    max_watch_entries: value.max_watch_entries,
    disabled: value.disabled,
  };
}

function healthLabel(health: HostDirectoryHealth | null | undefined): string {
  if (!health) return "Not checked";
  if (health.available === true || health.status === "ok" || health.status === "healthy") {
    return "Available";
  }
  if (health.exists === false || health.status === "missing") return "Missing";
  return health.status || "Unavailable";
}

function healthClass(health: HostDirectoryHealth | null | undefined): string {
  return health?.available === true || health?.status === "ok" || health?.status === "healthy"
    ? "state-running"
    : health
      ? "state-failed"
      : "state-idle";
}

function entryPath(entry: QuarantineEntry): string {
  return entry.original_path || entry.relative_path || "Unknown path";
}

function formatBytes(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "Unknown size";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MiB`;
}

export function HostDirectoriesPage({
  selectedDirectoryPath,
  privilegedUnlocked,
}: {
  selectedDirectoryPath: string;
  privilegedUnlocked: boolean;
}) {
  const [state, setState] = useState<HostDirectoryState | null>(null);
  const [draft, setDraft] = useState<HostDirectoryConfig>({});
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [newDirectoryId, setNewDirectoryId] = useState("");
  const [query, setQuery] = useState("");
  const [healthById, setHealthById] = useState<Record<string, HostDirectoryHealth | null>>({});
  const [quarantine, setQuarantine] = useState<QuarantineState | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [checkingHealth, setCheckingHealth] = useState(false);
  const [recoveryBusy, setRecoveryBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const activeDirectoryPathRef = useRef(selectedDirectoryPath);
  const refreshRequestIdRef = useRef(0);
  const mutationRequestIdRef = useRef(0);
  const quarantineRequestIdRef = useRef(0);
  activeDirectoryPathRef.current = selectedDirectoryPath;

  const queryForDirectory = (directoryPath: string) =>
    `directory_path=${encodeURIComponent(directoryPath)}`;

  function applyState(payload: HostDirectoryState, requestedDirectoryPath: string): boolean {
    if (
      requestedDirectoryPath !== activeDirectoryPathRef.current ||
      payload.directory_path !== requestedDirectoryPath
    ) {
      return false;
    }
    const config = normalizeConfig(payload.config);
    setState(payload);
    setDraft(config);
    setHealthById(
      Object.fromEntries(payload.host_directories.map((item) => [item.directory_id, item.health ?? null])),
    );
    setSelectedId((current) => {
      if (current && payload.host_directories.some((item) => item.directory_id === current)) {
        return current;
      }
      return payload.host_directories[0]?.directory_id ?? null;
    });
    return true;
  }

  async function refresh(requestedDirectoryPath = selectedDirectoryPath, signal?: AbortSignal) {
    const requestId = ++refreshRequestIdRef.current;
    setLoading(true);
    setError(null);
    try {
      const payload = await fetchJson<HostDirectoryState>(
        `/api/admin/host-directories?${queryForDirectory(requestedDirectoryPath)}`,
        { signal },
      );
      if (requestId !== refreshRequestIdRef.current) return;
      applyState(payload, requestedDirectoryPath);
    } catch (reason: unknown) {
      if (
        requestId !== refreshRequestIdRef.current ||
        requestedDirectoryPath !== activeDirectoryPathRef.current
      ) {
        return;
      }
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      if (
        requestId === refreshRequestIdRef.current &&
        requestedDirectoryPath === activeDirectoryPathRef.current
      ) {
        setLoading(false);
      }
    }
  }

  useEffect(() => {
    const controller = new AbortController();
    mutationRequestIdRef.current += 1;
    quarantineRequestIdRef.current += 1;
    setState(null);
    setDraft({});
    setSelectedId(null);
    setQuarantine(null);
    setStatus(null);
    setError(null);
    setSaving(false);
    setCheckingHealth(false);
    setRecoveryBusy(null);
    void refresh(selectedDirectoryPath, controller.signal);
    return () => {
      controller.abort();
      refreshRequestIdRef.current += 1;
    };
  }, [selectedDirectoryPath]);

  const activeState = state?.directory_path === selectedDirectoryPath ? state : null;
  const dirty = Boolean(activeState && stableConfigText(draft) !== stableConfigText(activeState.config));
  const rawDefinitions = definitions(draft);
  const recordsById = useMemo(
    () => Object.fromEntries((activeState?.host_directories ?? []).map((item) => [item.directory_id, item])),
    [activeState],
  );
  const visibleRecords = useMemo(() => {
    const ids = new Set([
      ...(activeState?.host_directories ?? []).map((item) => item.directory_id),
      ...Object.keys(rawDefinitions),
    ]);
    const normalized = query.trim().toLowerCase();
    return [...ids]
      .sort()
      .map((directoryId) => {
        const effective = recordsById[directoryId];
        const local = rawDefinitions[directoryId];
        return {
          ...(effective ?? DEFAULT_DEFINITION),
          ...(local ?? {}),
          directory_id: directoryId,
          defined_here: Boolean(local),
          source_directory_path: effective?.source_directory_path ?? null,
          health: healthById[directoryId] ?? effective?.health ?? null,
        } satisfies HostDirectoryRecord;
      })
      .filter((item) =>
        !normalized ||
        [item.directory_id, item.display_name, item.description, item.root]
          .join(" ")
          .toLowerCase()
          .includes(normalized),
      );
  }, [activeState, healthById, query, rawDefinitions, recordsById]);
  const selectedRecord = selectedId
    ? visibleRecords.find((item) => item.directory_id === selectedId) ??
      (recordsById[selectedId] ? { ...recordsById[selectedId], health: healthById[selectedId] } : null)
    : null;
  const selectedDefinition = selectedId
    ? recordsById[selectedId] || rawDefinitions[selectedId]
      ? {
          ...DEFAULT_DEFINITION,
          ...(recordsById[selectedId] ?? {}),
          ...(rawDefinitions[selectedId] ?? {}),
        }
      : null
    : null;

  useEffect(() => {
    setQuarantine(null);
    if (!selectedId || !activeState) return;
    if (!recordsById[selectedId]) {
      setQuarantine({
        directory_path: selectedDirectoryPath,
        directory_id: selectedId,
        entries: [],
      });
      return;
    }
    const requestId = ++quarantineRequestIdRef.current;
    const requestedDirectoryPath = selectedDirectoryPath;
    void (async () => {
      try {
        const payload = await fetchJson<QuarantineState>(
          `/api/admin/host-directories/${encodeURIComponent(selectedId)}/quarantine?${queryForDirectory(requestedDirectoryPath)}`,
        );
        if (
          requestId === quarantineRequestIdRef.current &&
          requestedDirectoryPath === activeDirectoryPathRef.current
        ) {
          setQuarantine(payload);
        }
      } catch (reason: unknown) {
        if (
          requestId === quarantineRequestIdRef.current &&
          requestedDirectoryPath === activeDirectoryPathRef.current
        ) {
          setError(reason instanceof Error ? reason.message : String(reason));
        }
      }
    })();
  }, [activeState, recordsById, selectedDirectoryPath, selectedId]);

  function updateDefinition(update: Partial<HostDirectoryDefinition>) {
    if (!selectedId || !selectedDefinition) return;
    setDraft((current) => ({
      ...current,
      host_directories: {
        ...definitions(current),
        [selectedId]: {
          ...DEFAULT_DEFINITION,
          ...definitionOnly(selectedDefinition),
          ...update,
        },
      },
    }));
    setStatus(null);
  }

  function addDirectory() {
    const directoryId = newDirectoryId.trim();
    if (!DIRECTORY_ID_PATTERN.test(directoryId)) {
      setError("Directory ID must start with a lowercase letter or digit and use only lowercase letters, digits, dots, underscores, or hyphens.");
      return;
    }
    if (recordsById[directoryId] || rawDefinitions[directoryId]) {
      setError(`Host directory ${directoryId} already exists.`);
      return;
    }
    setDraft((current) => ({
      ...current,
      host_directories: {
        ...definitions(current),
        [directoryId]: {
          ...DEFAULT_DEFINITION,
          display_name: directoryId,
        },
      },
    }));
    setNewDirectoryId("");
    setSelectedId(directoryId);
    setError(null);
    setStatus(`Added ${directoryId} to the unsaved directory override.`);
  }

  function removeDefinition() {
    if (!selectedId || !rawDefinitions[selectedId]) return;
    const nextDefinitions = { ...rawDefinitions };
    delete nextDefinitions[selectedId];
    setDraft((current) => ({ ...current, host_directories: nextDefinitions }));
    setSelectedId(Object.keys(nextDefinitions).sort()[0] ?? activeState?.host_directories[0]?.directory_id ?? null);
    setStatus(`Removed the local override for ${selectedId}. Save to apply the change.`);
  }

  async function save() {
    if (!activeState || !dirty) return;
    const requestId = ++mutationRequestIdRef.current;
    const requestedDirectoryPath = selectedDirectoryPath;
    const baseState = activeState;
    setSaving(true);
    setError(null);
    setStatus(null);
    try {
      const payload = await fetchJson<HostDirectoryState>(
        `/api/admin/host-directories?${queryForDirectory(requestedDirectoryPath)}`,
        {
          method: "PUT",
          body: JSON.stringify({
            expected_revision: baseState.revision,
            config: draft,
          }),
        },
      );
      if (requestId !== mutationRequestIdRef.current) return;
      if (applyState(payload, requestedDirectoryPath)) {
        setStatus("Host directory configuration saved and validated.");
      }
    } catch (reason: unknown) {
      if (
        requestId !== mutationRequestIdRef.current ||
        requestedDirectoryPath !== activeDirectoryPathRef.current
      ) {
        return;
      }
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      if (
        requestId === mutationRequestIdRef.current &&
        requestedDirectoryPath === activeDirectoryPathRef.current
      ) {
        setSaving(false);
      }
    }
  }

  async function checkHealth() {
    if (!selectedId) return;
    const requestId = ++mutationRequestIdRef.current;
    const requestedDirectoryPath = selectedDirectoryPath;
    setCheckingHealth(true);
    setError(null);
    setStatus(null);
    try {
      const payload = await fetchJson<HostDirectoryHealthResponse>(
        `/api/admin/host-directories/${encodeURIComponent(selectedId)}/health?${queryForDirectory(requestedDirectoryPath)}`,
        { method: "POST", body: "{}" },
      );
      if (
        requestId !== mutationRequestIdRef.current ||
        requestedDirectoryPath !== activeDirectoryPathRef.current
      ) {
        return;
      }
      setHealthById((current) => ({ ...current, [selectedId]: payload.health }));
      setStatus(`Health check completed for ${selectedId}: ${healthLabel(payload.health)}.`);
    } catch (reason: unknown) {
      if (
        requestId === mutationRequestIdRef.current &&
        requestedDirectoryPath === activeDirectoryPathRef.current
      ) {
        setError(reason instanceof Error ? reason.message : String(reason));
      }
    } finally {
      if (
        requestId === mutationRequestIdRef.current &&
        requestedDirectoryPath === activeDirectoryPathRef.current
      ) {
        setCheckingHealth(false);
      }
    }
  }

  async function refreshQuarantine(directoryId = selectedId) {
    if (!directoryId) return;
    const requestId = ++quarantineRequestIdRef.current;
    const requestedDirectoryPath = selectedDirectoryPath;
    const payload = await fetchJson<QuarantineState>(
      `/api/admin/host-directories/${encodeURIComponent(directoryId)}/quarantine?${queryForDirectory(requestedDirectoryPath)}`,
    );
    if (
      requestId === quarantineRequestIdRef.current &&
      requestedDirectoryPath === activeDirectoryPathRef.current
    ) {
      setQuarantine(payload);
    }
  }

  async function recover(entry: QuarantineEntry, action: "restore" | "purge") {
    if (!selectedId) return;
    if (
      action === "purge" &&
      !window.confirm(`Permanently purge ${entryPath(entry)}? This cannot be undone.`)
    ) {
      return;
    }
    const key = `${action}:${entry.recovery_id}`;
    const requestId = ++mutationRequestIdRef.current;
    const requestedDirectoryPath = selectedDirectoryPath;
    const directoryId = selectedId;
    setRecoveryBusy(key);
    setError(null);
    setStatus(null);
    try {
      await fetchJson<Record<string, unknown>>(
        `/api/admin/host-directories/${encodeURIComponent(directoryId)}/quarantine/${encodeURIComponent(entry.recovery_id)}/${action}?${queryForDirectory(requestedDirectoryPath)}`,
        {
          method: action === "restore" ? "POST" : "DELETE",
          ...(action === "restore" ? { body: "{}" } : {}),
        },
      );
      if (
        requestId !== mutationRequestIdRef.current ||
        requestedDirectoryPath !== activeDirectoryPathRef.current
      ) {
        return;
      }
      await refreshQuarantine(directoryId);
      setStatus(`${action === "restore" ? "Restored" : "Purged"} ${entryPath(entry)}.`);
    } catch (reason: unknown) {
      if (
        requestId === mutationRequestIdRef.current &&
        requestedDirectoryPath === activeDirectoryPathRef.current
      ) {
        setError(reason instanceof Error ? reason.message : String(reason));
      }
    } finally {
      if (
        requestId === mutationRequestIdRef.current &&
        requestedDirectoryPath === activeDirectoryPathRef.current
      ) {
        setRecoveryBusy(null);
      }
    }
  }

  const selectedHealth = selectedId ? healthById[selectedId] ?? selectedRecord?.health : null;

  return (
    <div className="feature-page host-directories-page">
      <section className="feature-sidebar">
        <div className="feature-sidebar-header">
          <div>
            <div className="eyebrow">Scoped host filesystem access</div>
            <h1>Host Directories</h1>
          </div>
          <button type="button" onClick={() => void refresh()} disabled={loading || saving}>
            Refresh
          </button>
        </div>
        <label className="field-stack">
          <span>Search</span>
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Name, ID, or host path"
          />
        </label>
        <div className="feature-list">
          {visibleRecords.map((record) => (
            <button
              key={record.directory_id}
              type="button"
              className={`feature-list-item${selectedId === record.directory_id ? " selected" : ""}`}
              onClick={() => setSelectedId(record.directory_id)}
            >
              <div className="feature-list-title-row">
                <strong>{record.display_name || record.directory_id}</strong>
                <span className={`status-pill ${record.disabled ? "state-idle" : healthClass(record.health)}`}>
                  {record.disabled ? "Disabled" : healthLabel(record.health)}
                </span>
              </div>
              <div className="feature-list-meta">
                {record.directory_id} · {record.defined_here ? "Local override" : record.source_directory_path || "Inherited"}
              </div>
            </button>
          ))}
          {!loading && visibleRecords.length === 0 ? (
            <div className="empty-state">No host directories match this directory and filter.</div>
          ) : null}
        </div>
        <div className="host-directory-add">
          <label className="field-stack">
            <span>New directory ID</span>
            <input
              value={newDirectoryId}
              onChange={(event) => setNewDirectoryId(event.target.value)}
              placeholder="shared-inbox"
              disabled={saving || !privilegedUnlocked}
            />
          </label>
          <button
            type="button"
            onClick={addDirectory}
            disabled={!newDirectoryId.trim() || saving || !privilegedUnlocked}
          >
            Add directory
          </button>
        </div>
      </section>

      <section className="feature-content">
        <div className="feature-toolbar">
          <div>
            <h2>{selectedRecord?.display_name ?? "Host directory configuration"}</h2>
            <p className="muted-text">
              Definitions inherit by Evander directory. Agents see the resource ID and relative paths, never the absolute host root.
            </p>
          </div>
          <div className="feature-toolbar-actions">
            <button
              type="button"
              onClick={() => activeState && setDraft(activeState.config)}
              disabled={!dirty || saving}
            >
              Reset
            </button>
            <button
              type="button"
              onClick={() => void checkHealth()}
              disabled={!selectedId || !recordsById[selectedId] || checkingHealth || saving || !privilegedUnlocked}
            >
              {checkingHealth ? "Checking…" : "Check health"}
            </button>
            <button
              type="button"
              className="primary"
              onClick={() => void save()}
              disabled={!dirty || saving || !activeState || !privilegedUnlocked}
            >
              {saving ? "Saving…" : "Save configuration"}
            </button>
          </div>
        </div>

        {!privilegedUnlocked ? (
          <div className="callout warning-callout">
            Unlock privileged operations to edit definitions, check host paths, or recover quarantined entries.
          </div>
        ) : null}
        {error ? <div className="callout warning-callout">{error}</div> : null}
        {status ? <div className="callout">{status}</div> : null}

        {selectedId && selectedDefinition ? (
          <>
            <article className="panel">
              <div className="panel-header">
                <div>
                  <h3>Resource definition</h3>
                  <p className="muted-text">
                    ID <strong>{selectedId}</strong>
                    {rawDefinitions[selectedId] ? " is defined in this directory." : " is inherited; editing creates a local override."}
                  </p>
                </div>
                <div className="status-pills">
                  <span className={`status-pill ${healthClass(selectedHealth)}`}>{healthLabel(selectedHealth)}</span>
                  {dirty ? <span className="status-pill state-pending">Unsaved</span> : null}
                </div>
              </div>

              <div className="form-grid host-directory-form">
                <label className="field-stack">
                  <span>Display name</span>
                  <input
                    value={selectedDefinition.display_name}
                    onChange={(event) => updateDefinition({ display_name: event.target.value })}
                    disabled={saving || !privilegedUnlocked}
                  />
                </label>
                <label className="field-stack host-directory-root-field">
                  <span>Absolute host root</span>
                  <input
                    value={selectedDefinition.root}
                    onChange={(event) => updateDefinition({ root: event.target.value })}
                    placeholder="/srv/evander/inbox"
                    disabled={saving || !privilegedUnlocked}
                  />
                </label>
                <label className="field-stack host-directory-description-field">
                  <span>Description</span>
                  <textarea
                    rows={3}
                    value={selectedDefinition.description}
                    onChange={(event) => updateDefinition({ description: event.target.value })}
                    disabled={saving || !privilegedUnlocked}
                  />
                </label>
              </div>

              <fieldset className="host-directory-capabilities">
                <legend>Agent capabilities</legend>
                <div className="host-directory-capability-options">
                  {CAPABILITIES.map((capability) => (
                    <label className="checkbox-row" key={capability}>
                      <input
                        type="checkbox"
                        checked={selectedDefinition.capabilities.includes(capability)}
                        onChange={(event) => {
                          const next = event.target.checked
                            ? [...selectedDefinition.capabilities, capability]
                            : selectedDefinition.capabilities.filter((item) => item !== capability);
                          const capabilities = CAPABILITIES.filter((item) => next.includes(item));
                          updateDefinition({
                            capabilities,
                            ...(capability === "delete" && !event.target.checked
                              ? { delete_requires_approval: true }
                              : {}),
                          });
                        }}
                        disabled={saving || !privilegedUnlocked}
                      />
                      <span>{capability}</span>
                    </label>
                  ))}
                </div>
              </fieldset>

              <div className="form-grid">
                <label className="field-stack">
                  <span>Maximum import bytes</span>
                  <input
                    type="number"
                    min={1}
                    max={100 * 1024 * 1024}
                    value={selectedDefinition.max_import_bytes}
                    onChange={(event) => updateDefinition({ max_import_bytes: Number(event.target.value) })}
                    disabled={saving || !privilegedUnlocked}
                  />
                </label>
                <label className="field-stack">
                  <span>Maximum import entries</span>
                  <input
                    type="number"
                    min={1}
                    max={10_000}
                    value={selectedDefinition.max_import_entries}
                    onChange={(event) => updateDefinition({ max_import_entries: Number(event.target.value) })}
                    disabled={saving || !privilegedUnlocked}
                  />
                </label>
                <label className="field-stack">
                  <span>Maximum watched entries</span>
                  <input
                    type="number"
                    min={1}
                    max={100_000}
                    value={selectedDefinition.max_watch_entries}
                    onChange={(event) => updateDefinition({ max_watch_entries: Number(event.target.value) })}
                    disabled={saving || !privilegedUnlocked}
                  />
                </label>
              </div>

              <div className="host-directory-toggle-row">
                <label className="checkbox-row">
                  <input
                    type="checkbox"
                    checked={selectedDefinition.delete_requires_approval}
                    onChange={(event) => updateDefinition({ delete_requires_approval: event.target.checked })}
                    disabled={
                      saving ||
                      !privilegedUnlocked ||
                      !selectedDefinition.capabilities.includes("delete")
                    }
                  />
                  <span>Agent deletes require approval</span>
                </label>
                <label className="checkbox-row">
                  <input
                    type="checkbox"
                    checked={selectedDefinition.disabled}
                    onChange={(event) => updateDefinition({ disabled: event.target.checked })}
                    disabled={saving || !privilegedUnlocked}
                  />
                  <span>Disable this resource</span>
                </label>
              </div>

              {rawDefinitions[selectedId] ? (
                <div className="inline-actions">
                  <button
                    type="button"
                    className="danger"
                    onClick={removeDefinition}
                    disabled={saving || !privilegedUnlocked}
                  >
                    Remove local definition
                  </button>
                </div>
              ) : null}
            </article>

            <article className="panel">
              <div className="panel-header">
                <div>
                  <h3>Host path health</h3>
                  <p className="muted-text">Checks are run by the host and do not expose this root to agent sessions.</p>
                </div>
                <span className={`status-pill ${healthClass(selectedHealth)}`}>{healthLabel(selectedHealth)}</span>
              </div>
              <div className="detail-grid">
                <div><span>Availability</span><strong>{selectedHealth?.available == null ? "Not checked" : selectedHealth.available ? "Available" : "Unavailable"}</strong></div>
                <div><span>Safe directory open</span><strong>{selectedHealth?.status == null ? "Not checked" : selectedHealth.status === "ok" ? "Passed" : "Failed"}</strong></div>
                <div><span>Readable</span><strong>{selectedHealth?.checks?.readable == null ? "Not checked" : selectedHealth.checks.readable ? "Passed" : "Failed"}</strong></div>
                <div><span>Mount boundary checks</span><strong>{selectedHealth?.checks?.mount_boundary_verified == null ? "Not checked" : selectedHealth.checks.mount_boundary_verified ? "Available" : "Unavailable"}</strong></div>
                <div><span>Delete quarantine</span><strong>{selectedHealth?.checks?.quarantine_writable == null ? "Not checked" : selectedHealth.checks.quarantine_writable ? "Writable" : "Unavailable"}</strong></div>
                <div><span>Status</span><strong>{selectedHealth?.status ?? "Not checked"}</strong></div>
                <div><span>Error class</span><strong>{selectedHealth?.error_class ?? "None"}</strong></div>
              </div>
              {selectedHealth?.message || selectedHealth?.error ? (
                <div className="callout warning-callout">{selectedHealth.message || selectedHealth.error}</div>
              ) : null}
            </article>

            <article className="panel">
              <div className="panel-header">
                <div>
                  <h3>Quarantine recovery</h3>
                  <p className="muted-text">Agent deletes are recoverable until an operator permanently purges them.</p>
                </div>
                <button
                  type="button"
                  onClick={() => void refreshQuarantine()}
                  disabled={Boolean(recoveryBusy)}
                >
                  Refresh
                </button>
              </div>
              <div className="detail-stack">
                {quarantine?.entries.map((entry) => (
                  <div className="detail-block host-directory-quarantine-entry" key={entry.recovery_id}>
                    <div>
                      <strong>{entryPath(entry)}</strong>
                      <p className="muted-text">
                        {entry.kind || "entry"} · {formatBytes(entry.size)}
                        {entry.deleted_at ? ` · ${entry.deleted_at}` : ""}
                      </p>
                      {entry.recovery_status && entry.recovery_status !== "ready" ? (
                        <p className="callout warning-callout">
                          Recovery record needs attention: {entry.recovery_status.replace(/_/g, " ")}.
                          Restore is unavailable; purge can clean up this incomplete record.
                        </p>
                      ) : null}
                    </div>
                    <div className="inline-actions">
                      <button
                        type="button"
                        onClick={() => void recover(entry, "restore")}
                        disabled={
                          Boolean(recoveryBusy) ||
                          !privilegedUnlocked ||
                          entry.restore_available === false
                        }
                      >
                        {recoveryBusy === `restore:${entry.recovery_id}` ? "Restoring…" : "Restore"}
                      </button>
                      <button
                        type="button"
                        className="danger"
                        onClick={() => void recover(entry, "purge")}
                        disabled={
                          Boolean(recoveryBusy) ||
                          !privilegedUnlocked ||
                          entry.purge_available === false
                        }
                      >
                        {recoveryBusy === `purge:${entry.recovery_id}` ? "Purging…" : "Purge permanently"}
                      </button>
                    </div>
                  </div>
                ))}
                {quarantine && quarantine.entries.length === 0 ? (
                  <div className="empty-state">No quarantined entries for this resource.</div>
                ) : null}
                {!quarantine ? <div className="empty-state">Loading quarantine entries…</div> : null}
              </div>
            </article>
          </>
        ) : (
          <div className="empty-state">Add or select a host directory resource to configure it.</div>
        )}
      </section>
    </div>
  );
}
