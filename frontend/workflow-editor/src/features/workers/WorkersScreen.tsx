import { useEffect, useMemo, useState } from "react";

import { ApiError, fetchJson } from "../admin/api";
import { useAdminUi } from "../admin/AdminContext";
import { Tooltip } from "../admin/Tooltip";
import { RefreshIcon, SaveIcon, StartIcon, StopIcon } from "../admin/PageActionIcons";
import { JsonTextEditor, StringListEditor } from "../admin/StructuredPolicyInputs";
import {
  encodeStringListDraftValue,
  formatJsonTextDraftValue,
  parseJsonTextDraftValue,
  parseStringListDraftValue,
} from "../admin/structuredPolicyInputs";

type WorkerSummary = {
  worker_id: string;
  name: string;
  unit: string;
  active_state: string;
  sub_state: string | null;
  result: string | null;
  unit_file_state: string | null;
  description: string | null;
  main_pid: number | null;
  running: boolean;
  failed: boolean;
  detail: string | null;
};

type WorkerConfigPayload = {
  worker_id: string;
  path: string;
  exists: boolean;
  config: Record<string, unknown>;
  revision: string;
};

type WorkerConfigField = {
  field_name: string;
  label: string;
  kind: "bool" | "int" | "float" | "literal" | "text" | "multiline_text" | "string_list" | "json";
  options: string[];
  tooltip: string;
};

type WorkerConfigSchema = {
  worker_id: string;
  fields: WorkerConfigField[];
};

type WorkerDetail = {
  summary: WorkerSummary;
  config: WorkerConfigPayload;
  config_schema: WorkerConfigSchema | null;
};

type WorkerLog = {
  worker_id: string;
  worker_name: string;
  unit: string;
  lines: number;
  text: string;
};

type WorkerActionResponse = {
  result: {
    success: boolean;
    worker_id: string;
    unit: string;
    action: string;
    message: string;
  };
  worker: WorkerDetail | null;
};

type WorkerListResponse = {
  workers: WorkerSummary[];
};

type DraftValue = string | boolean;
type WorkerDraft = Record<string, DraftValue>;

const POLL_INTERVAL_MS = 5000;
const LOG_LINES = 200;

function normalizeFieldValue(field: WorkerConfigField, value: unknown): DraftValue {
  if (field.kind === "bool") {
    return Boolean(value);
  }
  if (field.kind === "int" || field.kind === "float" || field.kind === "text") {
    return value == null ? "" : String(value);
  }
  if (field.kind === "multiline_text") {
    return value == null ? "" : String(value);
  }
  if (field.kind === "string_list") {
    if (Array.isArray(value)) {
      return value.map((item) => String(item)).join("\n");
    }
    if (value == null) {
      return "";
    }
    return String(value);
  }
  if (field.kind === "literal") {
    if (value == null) {
      return field.options[0] ?? "";
    }
    return String(value);
  }
  return formatJsonTextDraftValue(value);
}

function buildDraft(detail: WorkerDetail | null): WorkerDraft {
  const fields = detail?.config_schema?.fields ?? [];
  const next: WorkerDraft = {};
  for (const field of fields) {
    next[field.field_name] = normalizeFieldValue(field, detail?.config.config[field.field_name]);
  }
  return next;
}

function parseDraftValue(field: WorkerConfigField, rawValue: DraftValue): unknown {
  if (field.kind === "bool") {
    return Boolean(rawValue);
  }
  if (field.kind === "int") {
    const text = String(rawValue).trim();
    if (!text) {
      throw new Error(`${field.label} is required.`);
    }
    const parsed = Number.parseInt(text, 10);
    if (!Number.isFinite(parsed)) {
      throw new Error(`${field.label} must be an integer.`);
    }
    return parsed;
  }
  if (field.kind === "float") {
    const text = String(rawValue).trim();
    if (!text) {
      throw new Error(`${field.label} is required.`);
    }
    const parsed = Number.parseFloat(text);
    if (!Number.isFinite(parsed)) {
      throw new Error(`${field.label} must be a number.`);
    }
    return parsed;
  }
  if (field.kind === "text") {
    return String(rawValue).trim();
  }
  if (field.kind === "multiline_text") {
    return String(rawValue).trim();
  }
  if (field.kind === "string_list") {
    return parseStringListDraftValue(rawValue);
  }
  if (field.kind === "literal") {
    return String(rawValue);
  }
  return parseJsonTextDraftValue(rawValue, field.label);
}

function draftToPayload(detail: WorkerDetail | null, draft: WorkerDraft): Record<string, unknown> {
  const fields = detail?.config_schema?.fields ?? [];
  const payload: Record<string, unknown> = {};
  for (const field of fields) {
    payload[field.field_name] = parseDraftValue(field, draft[field.field_name]);
  }
  return payload;
}

export function WorkersScreen() {
  const { privilegedUnlocked } = useAdminUi();
  const [workers, setWorkers] = useState<WorkerSummary[]>([]);
  const [selectedWorkerId, setSelectedWorkerId] = useState<string>("");
  const [detail, setDetail] = useState<WorkerDetail | null>(null);
  const [draft, setDraft] = useState<WorkerDraft>({});
  const [isDirty, setIsDirty] = useState(false);
  const [logText, setLogText] = useState("");
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [acting, setActing] = useState<string | null>(null);

  async function loadWorkers(preferredWorkerId?: string, currentWorkerId?: string) {
    const payload = await fetchJson<WorkerListResponse>("/api/workers");
    setWorkers(payload.workers);
    const nextWorkerId =
      preferredWorkerId && payload.workers.some((worker) => worker.worker_id === preferredWorkerId)
        ? preferredWorkerId
        : currentWorkerId && payload.workers.some((worker) => worker.worker_id === currentWorkerId)
          ? currentWorkerId
          : payload.workers[0]?.worker_id ?? "";
    if (nextWorkerId !== currentWorkerId) {
      setSelectedWorkerId(nextWorkerId);
    }
    return nextWorkerId;
  }

  async function loadWorkerDetail(workerId: string, options?: { preserveDraft?: boolean }) {
    const [workerDetail, workerLog] = await Promise.all([
      fetchJson<WorkerDetail>(`/api/workers/${encodeURIComponent(workerId)}`),
      fetchJson<WorkerLog>(`/api/workers/${encodeURIComponent(workerId)}/log?lines=${LOG_LINES}`),
    ]);
    setDetail((current) =>
      options?.preserveDraft && current
        ? { ...workerDetail, config: current.config }
        : workerDetail,
    );
    setLogText(workerLog.text);
    if (!options?.preserveDraft) {
      setDraft(buildDraft(workerDetail));
      setIsDirty(false);
    }
  }

  useEffect(() => {
    let cancelled = false;

    async function refresh(showLoading: boolean) {
      try {
        if (!cancelled && showLoading) {
          setLoading(true);
        }
        const nextWorkerId = await loadWorkers(selectedWorkerId, selectedWorkerId);
        if (!nextWorkerId || cancelled) {
          if (!cancelled) {
            setDetail(null);
            setDraft({});
            setIsDirty(false);
            setLogText("");
          }
          return;
        }
        await loadWorkerDetail(nextWorkerId, { preserveDraft: isDirty && nextWorkerId === selectedWorkerId });
        if (!cancelled) {
          setError(null);
        }
      } catch (reason) {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : String(reason));
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    void refresh(true);
    const timer = window.setInterval(() => {
      void refresh(false);
    }, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [isDirty, selectedWorkerId]);

  const selectedSummary = useMemo(
    () => workers.find((worker) => worker.worker_id === selectedWorkerId) ?? detail?.summary ?? null,
    [detail?.summary, selectedWorkerId, workers],
  );
  const workerCountLabel = `${workers.length} ${workers.length === 1 ? "worker" : "workers"}`;
  const mutationLocked = !privilegedUnlocked;

  async function reloadSelectedWorker(options?: { preserveDraft?: boolean }) {
    if (!selectedWorkerId) return;
    try {
      setLoading(true);
      await loadWorkers(selectedWorkerId, selectedWorkerId);
      await loadWorkerDetail(selectedWorkerId, options);
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  }

  async function handleWorkerAction(action: "start" | "stop") {
    if (!selectedWorkerId) return;
    if (action === "stop" && !window.confirm(`Stop ${selectedSummary?.name ?? selectedWorkerId}?`)) {
      return;
    }
    setActing(action);
    setStatusMessage(null);
    try {
      const payload = await fetchJson<WorkerActionResponse>(
        `/api/workers/${encodeURIComponent(selectedWorkerId)}/actions/${action}`,
        { method: "POST" },
      );
      if (payload.worker) {
        setDetail(payload.worker);
        if (!isDirty) {
          setDraft(buildDraft(payload.worker));
        }
      }
      await loadWorkers(selectedWorkerId);
      await reloadSelectedWorker({ preserveDraft: isDirty });
      setStatusMessage(payload.result.message);
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setActing(null);
    }
  }

  function handleSelectWorker(workerId: string) {
    if (workerId === selectedWorkerId) {
      return;
    }
    if (isDirty && !window.confirm("Discard unsaved worker settings and switch workers?")) {
      return;
    }
    setStatusMessage(null);
    setError(null);
    setIsDirty(false);
    setSelectedWorkerId(workerId);
  }

  async function handleSaveConfig() {
    if (!selectedWorkerId || !detail) return;
    let parsed: Record<string, unknown>;
    try {
      parsed = draftToPayload(detail, draft);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      return;
    }
    setSaving(true);
    setStatusMessage(null);
    try {
      const payload = await fetchJson<WorkerDetail>(`/api/workers/${encodeURIComponent(selectedWorkerId)}/config`, {
        method: "PUT",
        body: JSON.stringify({ config: parsed, expected_revision: detail?.config.revision }),
      });
      setDetail(payload);
      setDraft(buildDraft(payload));
      setIsDirty(false);
      await loadWorkers(selectedWorkerId);
      setStatusMessage(`Saved ${payload.summary.name} global settings.`);
      setError(null);
    } catch (reason) {
      setError(
        reason instanceof ApiError && reason.status === 409
          ? "This worker configuration changed on the server. Reload the worker settings before saving again; your draft is still visible."
          : reason instanceof Error
            ? reason.message
            : String(reason),
      );
    } finally {
      setSaving(false);
    }
  }

  function updateField(fieldName: string, value: DraftValue) {
    setDraft((current) => ({ ...current, [fieldName]: value }));
    setIsDirty(true);
  }

  function renderField(field: WorkerConfigField) {
    const value = draft[field.field_name] ?? normalizeFieldValue(field, undefined);
    const commonProps = {
      title: field.tooltip || undefined,
      disabled: saving || acting !== null || loading,
    };
    if (field.kind === "bool") {
      return (
        <select
          {...commonProps}
          value={String(Boolean(value))}
          onChange={(event) => updateField(field.field_name, event.target.value === "true")}
        >
          <option value="false">false</option>
          <option value="true">true</option>
        </select>
      );
    }
    if (field.kind === "literal") {
      return (
        <select
          {...commonProps}
          value={String(value)}
          onChange={(event) => updateField(field.field_name, event.target.value)}
        >
          {field.options.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      );
    }
    if (field.kind === "string_list") {
      return (
        <StringListEditor
          values={parseStringListDraftValue(value)}
          onChange={(nextValues) => updateField(field.field_name, encodeStringListDraftValue(nextValues))}
          disabled={commonProps.disabled}
          itemLabel="Value"
          suggestions={field.options}
          placeholder="Enter a value"
        />
      );
    }
    if (field.kind === "json") {
      return (
        <JsonTextEditor
          value={String(value)}
          onChange={(nextValue) => updateField(field.field_name, nextValue)}
          fieldLabel={field.label}
          disabled={commonProps.disabled}
        />
      );
    }
    if (field.kind === "multiline_text") {
      return (
        <textarea
          {...commonProps}
          className="json-editor compact-editor"
          value={String(value)}
          spellCheck={false}
          onChange={(event) => updateField(field.field_name, event.target.value)}
        />
      );
    }
    return (
      <input
        {...commonProps}
        type={field.kind === "int" || field.kind === "float" ? "number" : "text"}
        value={String(value)}
        onChange={(event) => updateField(field.field_name, event.target.value)}
      />
    );
  }

  return (
    <div className="feature-page">
      <section className="feature-sidebar">
        <div className="feature-sidebar-header">
          <div>
            <div className="eyebrow">Host workers</div>
            <h1>Workers</h1>
          </div>
          <button
            className="admin-action-button compact"
            type="button"
            onClick={() => void reloadSelectedWorker({ preserveDraft: isDirty })}
            disabled={!selectedWorkerId || loading || acting !== null || saving}
          >
            {loading ? "Refreshing..." : "Refresh"}
          </button>
        </div>
        {mutationLocked ? <div className="callout warning-callout">Unlock privileged operations to start, stop, or save worker settings.</div> : null}
        <div className="panel section-panel">
          <div className="feature-list-title-row">
            <strong>Fleet</strong>
            <span className="panel-meta">{workerCountLabel}</span>
          </div>
          <div className="feature-list-meta">Systemd-backed host services and background workers.</div>
          <div className="feature-list-meta">Select one to inspect unit health, configuration, and the current journal tail.</div>
        </div>
        <div className="feature-list">
          {workers.map((worker) => (
            <button
              key={worker.worker_id}
              type="button"
              className={`feature-list-item${worker.worker_id === selectedWorkerId ? " selected" : ""}`}
              onClick={() => handleSelectWorker(worker.worker_id)}
              disabled={loading && worker.worker_id === selectedWorkerId}
            >
              <div className="feature-list-title-row">
                <span>{worker.name}</span>
                <span className={`status-pill state-${worker.failed ? "failed" : worker.running ? "running" : "idle"}`}>
                  {worker.failed ? "failed" : worker.running ? "running" : worker.active_state}
                </span>
              </div>
              <div className="feature-list-meta">{worker.unit}</div>
              <div className="feature-list-meta">
                {worker.sub_state ?? "unknown"}
                {worker.main_pid ? ` · pid ${worker.main_pid}` : ""}
              </div>
            </button>
          ))}
          {loading && workers.length === 0 ? <div className="empty-state">Loading workers…</div> : null}
          {!loading && workers.length === 0 ? (
            <div className="empty-state">No workers are available yet. Check host services or refresh after startup.</div>
          ) : null}
        </div>
      </section>

      <section className="feature-content">
        <div className="feature-toolbar">
          <div>
            <h2>{selectedSummary?.name ?? "Select a worker"}</h2>
            <p className="muted-text">
              {selectedSummary?.description ?? "Inspect status, tail logs, and edit worker-global settings."}
            </p>
          </div>
          <div className="feature-toolbar-actions icon-actions">
            {selectedSummary?.running ? (
              <Tooltip content="Stop the selected background worker service.">
                <button
                  className="admin-icon-button"
                  type="button"
                  onClick={() => void handleWorkerAction("stop")}
                  disabled={!selectedWorkerId || acting !== null || saving || loading || mutationLocked}
                  aria-label={acting === "stop" ? "Stopping worker" : "Stop worker"}
                >
                  {acting === "stop" ? "…" : <StopIcon />}
                </button>
              </Tooltip>
            ) : (
              <Tooltip content="Start the selected background worker service.">
                <button
                  className="admin-icon-button"
                  type="button"
                  onClick={() => void handleWorkerAction("start")}
                  disabled={!selectedWorkerId || acting !== null || saving || loading || mutationLocked}
                  aria-label={acting === "start" ? "Starting worker" : "Start worker"}
                >
                  {acting === "start" ? "…" : <StartIcon />}
                </button>
              </Tooltip>
            )}
            <Tooltip content="Reload the selected worker summary, config, and recent log.">
              <button
                className="admin-icon-button"
                type="button"
                onClick={() => void reloadSelectedWorker({ preserveDraft: isDirty })}
                disabled={!selectedWorkerId || loading || acting !== null || saving}
                aria-label={loading ? "Reloading worker" : "Reload worker"}
              >
                {loading ? "…" : <RefreshIcon />}
              </button>
            </Tooltip>
            <Tooltip content="Save the current worker-global settings back to the host config file.">
              <button
                type="button"
                className="admin-icon-button primary"
                onClick={() => void handleSaveConfig()}
                disabled={!selectedWorkerId || saving || acting !== null || loading || !detail?.config_schema || !isDirty || mutationLocked}
                aria-label={saving ? "Saving worker settings" : "Save worker settings"}
              >
                {saving ? "…" : <SaveIcon />}
              </button>
            </Tooltip>
          </div>
        </div>

        {statusMessage ? <div className="callout success-callout">{statusMessage}</div> : null}
        {error ? <div className="callout error-callout">{error}</div> : null}
        {isDirty ? <div className="callout warning-callout">Unsaved worker settings are being preserved across refreshes. Switching workers will discard them.</div> : null}

        <article className="panel detail-panel worker-overview-panel">
          <div className="panel-header">
            <div>
              <h3>Overview</h3>
              <p className="muted-text">
                Current unit state and service health for the selected worker.
              </p>
            </div>
            {selectedSummary ? (
              <span className={`status-pill state-${selectedSummary.failed ? "failed" : selectedSummary.running ? "running" : "idle"}`}>
                {selectedSummary.failed ? "failed" : selectedSummary.running ? "running" : selectedSummary.active_state}
              </span>
            ) : null}
          </div>
          {selectedSummary ? (
            <div className="detail-grid">
              <div><span>Name</span><strong>{selectedSummary.name}</strong></div>
              <div><span>Unit</span><strong>{selectedSummary.unit}</strong></div>
              <div><span>Main PID</span><strong>{selectedSummary.main_pid ?? "n/a"}</strong></div>
              <div><span>Active state</span><strong>{selectedSummary.active_state}</strong></div>
              <div><span>Sub state</span><strong>{selectedSummary.sub_state ?? "n/a"}</strong></div>
              <div><span>Result</span><strong>{selectedSummary.result ?? "n/a"}</strong></div>
            </div>
          ) : (
            <div className="empty-state">Select a worker to inspect it.</div>
          )}
        </article>

        <article className="panel">
          <div className="panel-header">
            <div>
              <h3>Global settings</h3>
              <p className="muted-text">
                Edit the worker-global configuration file that backs this service.
              </p>
            </div>
            <span className="panel-meta">
              {detail?.config.path ?? ""}
              {detail?.config.exists ? " · file exists" : " · using current defaults"}
            </span>
          </div>
          {detail?.config_schema ? (
            <div className="config-field-grid">
              {detail.config_schema.fields.map((field) => (
                <div key={field.field_name} className="config-field-card">
                  <div className="feature-list-title-row">
                    <strong>{field.label}</strong>
                    <span className="panel-meta">{field.kind}</span>
                  </div>
                  {renderField(field)}
                  {field.tooltip ? <div className="panel-meta">{field.tooltip}</div> : null}
                </div>
              ))}
            </div>
          ) : (
            <div className="empty-state">Global settings are unavailable for this worker. It may not expose an editable schema.</div>
          )}
        </article>

        <article className="panel">
          <div className="panel-header">
            <div>
              <h3>Recent log</h3>
              <p className="muted-text">Latest journal output for the selected worker unit.</p>
            </div>
            <button
              className="admin-action-button compact"
              type="button"
              onClick={() => void reloadSelectedWorker({ preserveDraft: isDirty })}
              disabled={!selectedWorkerId || loading || acting !== null || saving}
            >
              {loading ? "Reloading..." : "Reload log"}
            </button>
          </div>
          <pre className="log-view worker-log-view">
            {selectedWorkerId
              ? logText || "No recent log lines were returned for this worker."
              : "Select a worker to load the current journal tail."}
          </pre>
        </article>
      </section>
    </div>
  );
}
