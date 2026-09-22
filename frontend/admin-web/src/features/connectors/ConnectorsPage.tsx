import { useEffect, useMemo, useRef, useState } from "react";

import { fetchJson } from "../admin/api";
import type {CredentialsSnapshot} from "../configuration/CredentialsConfiguration";

type ConnectorOperation = {
  description: string;
  input_schema: Record<string, unknown>;
  access: "read" | "write";
  requires_approval: boolean;
  result_mode: "json" | "text" | "artifact";
  timeout_seconds: number;
  max_response_bytes: number;
  disabled?: boolean;
  config: Record<string, unknown>;
};

type ConnectorRecord = {
  connector_id: string;
  display_name: string;
  description: string;
  driver: string;
  auth_profile?: string | null;
  disabled?: boolean;
  health_operation?: string | null;
  operations: Record<string, ConnectorOperation>;
  auth_status: {
    available: boolean;
    type: string;
    missing_secret_count?: number;
  };
};

type ConnectorState = {
  directory_path: string;
  revision: string;
  config: Record<string, unknown>;
  effective_config: Record<string, unknown>;
  connectors: ConnectorRecord[];
  driver_names: string[];
  provenance: Record<string, unknown>;
};

type ConnectorTestResult = {
  status: string;
  connector_id: string;
  operation: string;
  result: {
    result_mode: string;
    upstream_status?: number;
    duration_ms?: number;
  };
};

function configText(config: Record<string, unknown>): string {
  return JSON.stringify(config, null, 2);
}

export function ConnectorsPage({
  selectedDirectoryPath,
  privilegedUnlocked,
}: {
  selectedDirectoryPath: string;
  privilegedUnlocked: boolean;
}) {
  const [state, setState] = useState<ConnectorState | null>(null);
  const [credentials, setCredentials] = useState<CredentialsSnapshot | null>(null);
  const [draft, setDraft] = useState("{}");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const activeDirectoryPathRef = useRef(selectedDirectoryPath);
  const refreshRequestIdRef = useRef(0);
  const saveRequestIdRef = useRef(0);
  const testRequestIdRef = useRef(0);
  activeDirectoryPathRef.current = selectedDirectoryPath;

  function queryForDirectory(directoryPath: string): string {
    return `directory_path=${encodeURIComponent(directoryPath)}`;
  }

  function applyState(payload: ConnectorState, requestedDirectoryPath: string): boolean {
    if (
      requestedDirectoryPath !== activeDirectoryPathRef.current ||
      payload.directory_path !== requestedDirectoryPath
    ) {
      return false;
    }
    setState(payload);
    setDraft(configText(payload.config));
    setSelectedId((current) => {
      if (current && payload.connectors.some((connector) => connector.connector_id === current)) {
        return current;
      }
      return payload.connectors[0]?.connector_id ?? null;
    });
    return true;
  }

  async function refresh(
    requestedDirectoryPath = selectedDirectoryPath,
    signal?: AbortSignal,
  ) {
    const requestId = ++refreshRequestIdRef.current;
    setLoading(true);
    setError(null);
    try {
      const payload = await fetchJson<ConnectorState>(
        `/api/admin/connectors?${queryForDirectory(requestedDirectoryPath)}`,
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
    saveRequestIdRef.current += 1;
    testRequestIdRef.current += 1;
    setState(null);
    setCredentials(null);
    const scope = selectedDirectoryPath;
    void fetchJson<CredentialsSnapshot>(`/api/admin/credentials?directory_path=${encodeURIComponent(scope)}`, {signal:controller.signal})
      .then(value => {if (activeDirectoryPathRef.current === scope && !controller.signal.aborted) setCredentials(value);})
      .catch(() => {});
    setDraft("{}");
    setSelectedId(null);
    setSaving(false);
    setTesting(false);
    setStatus(null);
    void refresh(selectedDirectoryPath, controller.signal);
    return () => {
      controller.abort();
      refreshRequestIdRef.current += 1;
    };
  }, [selectedDirectoryPath]);

  const activeState = state?.directory_path === selectedDirectoryPath ? state : null;
  const dirty = Boolean(activeState && draft !== configText(activeState.config));
  const visibleConnectors = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return activeState?.connectors ?? [];
    return (activeState?.connectors ?? []).filter((connector) =>
      [connector.connector_id, connector.display_name, connector.description, connector.driver]
        .join(" ")
        .toLowerCase()
        .includes(normalized),
    );
  }, [query, activeState]);
  const selected =
    activeState?.connectors.find((connector) => connector.connector_id === selectedId) ?? null;

  async function save() {
    if (!activeState || !dirty) return;
    const requestId = ++saveRequestIdRef.current;
    const requestedDirectoryPath = selectedDirectoryPath;
    const baseState = activeState;
    setSaving(true);
    setError(null);
    setStatus(null);
    try {
      const parsed = JSON.parse(draft) as unknown;
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        throw new Error("Connector configuration must be a JSON object.");
      }
      const payload = await fetchJson<ConnectorState>(
        `/api/admin/connectors?${queryForDirectory(requestedDirectoryPath)}`,
        {
          method: "PUT",
          body: JSON.stringify({
            expected_revision: baseState.revision,
            config: parsed,
          }),
        },
      );
      if (requestId !== saveRequestIdRef.current) return;
      if (applyState(payload, requestedDirectoryPath)) {
        setStatus("Connector configuration saved and validated.");
      }
    } catch (reason: unknown) {
      if (
        requestId !== saveRequestIdRef.current ||
        requestedDirectoryPath !== activeDirectoryPathRef.current
      ) {
        return;
      }
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      if (
        requestId === saveRequestIdRef.current &&
        requestedDirectoryPath === activeDirectoryPathRef.current
      ) {
        setSaving(false);
      }
    }
  }

  async function testConnector() {
    if (!selected) return;
    const requestId = ++testRequestIdRef.current;
    const requestedDirectoryPath = selectedDirectoryPath;
    const connectorId = selected.connector_id;
    setTesting(true);
    setError(null);
    setStatus(null);
    try {
      const payload = await fetchJson<ConnectorTestResult>(
        `/api/admin/connectors/${encodeURIComponent(connectorId)}/test?${queryForDirectory(requestedDirectoryPath)}`,
        { method: "POST", body: "{}" },
      );
      if (
        requestId !== testRequestIdRef.current ||
        requestedDirectoryPath !== activeDirectoryPathRef.current
      ) {
        return;
      }
      const upstream = payload.result.upstream_status
        ? ` HTTP ${payload.result.upstream_status}`
        : "";
      const duration = payload.result.duration_ms == null ? "" : ` in ${payload.result.duration_ms} ms`;
      setStatus(`Health operation ${payload.operation} succeeded${upstream}${duration}.`);
    } catch (reason: unknown) {
      if (
        requestId !== testRequestIdRef.current ||
        requestedDirectoryPath !== activeDirectoryPathRef.current
      ) {
        return;
      }
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      if (
        requestId === testRequestIdRef.current &&
        requestedDirectoryPath === activeDirectoryPathRef.current
      ) {
        setTesting(false);
      }
    }
  }

  return (
    <div className="feature-page connectors-page">
      <section className="feature-sidebar">
        <div className="feature-sidebar-header">
          <div>
            <div className="eyebrow">Approved host integrations</div>
            <h1>Connectors</h1>
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
            placeholder="Name, ID, or driver"
          />
        </label>
        <div className="feature-list">
          {visibleConnectors.map((connector) => (
            <button
              key={connector.connector_id}
              type="button"
              className={`feature-list-item${selected?.connector_id === connector.connector_id ? " selected" : ""}`}
              onClick={() => setSelectedId(connector.connector_id)}
            >
              <div className="feature-list-title-row">
                <strong>{connector.display_name}</strong>
                <span className={`status-pill ${connector.auth_status.available ? "state-running" : "state-failed"}`}>
                  {connector.auth_status.available ? "Available" : "Auth missing"}
                </span>
              </div>
              <div className="feature-list-meta">{connector.connector_id} · {connector.driver}</div>
            </button>
          ))}
          {!loading && visibleConnectors.length === 0 ? (
            <div className="empty-state">No connectors match this directory and filter.</div>
          ) : null}
        </div>
      </section>

      <section className="feature-content">
        <div className="feature-toolbar">
          <div>
            <h2>{selected?.display_name ?? "Connector configuration"}</h2>
            <p className="muted-text">
              Definitions inherit by directory. Authentication can use managed credentials or existing host environment references.
            </p>
          </div>
          <div className="feature-toolbar-actions">
            <button
              type="button"
              onClick={() => activeState && setDraft(configText(activeState.config))}
              disabled={!dirty || saving}
            >
              Reset
            </button>
            <button
              type="button"
              onClick={() => void testConnector()}
              disabled={!selected?.health_operation || testing || saving || !privilegedUnlocked}
            >
              {testing ? "Testing..." : "Run health test"}
            </button>
            <button
              type="button"
              className="primary"
              onClick={() => void save()}
              disabled={!dirty || saving || !activeState || !privilegedUnlocked}
            >
              {saving ? "Saving..." : "Save configuration"}
            </button>
          </div>
        </div>

        {!privilegedUnlocked ? (
          <div className="callout warning-callout">Unlock privileged operations to save configuration or run health tests.</div>
        ) : null}
        {error ? <div className="callout warning-callout">{error}</div> : null}
        {status ? <div className="callout">{status}</div> : null}

        {selected ? (
          <article className="panel">
            <div className="panel-header">
              <h3>Selected connector</h3>
              <span className={`status-pill ${selected.disabled ? "state-idle" : "state-running"}`}>
                {selected.disabled ? "Disabled" : "Enabled"}
              </span>
            </div>
            <div className="detail-grid">
              <div><span>ID</span><strong>{selected.connector_id}</strong></div>
              <div><span>Driver</span><strong>{selected.driver}</strong></div>
              <div><span>Authentication</span><strong>{selected.auth_status.type}</strong></div>
              <div><span>Auth profile</span><strong>{selected.auth_profile ?? "None"}</strong></div>
              <div><span>Health operation</span><strong>{selected.health_operation ?? "Not configured"}</strong></div>
              <div><span>Operations</span><strong>{Object.keys(selected.operations).length}</strong></div>
            </div>
            <p>{selected.description}</p>
            {selected.auth_profile ? <label className="field-stack">Managed credential
              <select aria-label="Managed connector credential" disabled={!privilegedUnlocked || saving} defaultValue="" key={`${selectedDirectoryPath}:${selected.connector_id}`} onChange={event => {
                if (!event.target.value || !activeState || !selected.auth_profile) return;
                try {
                  const parsed = JSON.parse(draft) as {auth_profiles?: Record<string, Record<string, unknown>>};
                  const effective = activeState.effective_config.auth_profiles as Record<string, Record<string, unknown>>;
                  const profile = {...(parsed.auth_profiles?.[selected.auth_profile] ?? effective[selected.auth_profile])};
                  for (const field of ["token_env","username_env","password_env","client_id_env","client_secret_env"]) profile[field] = null;
                  profile.type = String(profile.type).replace(/_env$/, "");
                  profile.credential_ref = event.target.value;
                  parsed.auth_profiles = {...parsed.auth_profiles,[selected.auth_profile]:profile};
                  setDraft(configText(parsed));
                } catch {setError("Fix the configuration JSON before selecting a credential.");}
              }}><option value="">Select an approved credential</option>
                {credentials?.directory_path === selectedDirectoryPath ? credentials.credentials.filter(c => c.local && c.status === "active" && c.bindings.some(b => b.profile_id === selected.auth_profile && b.connector_id === selected.connector_id)).map(c => <option key={c.id} value={c.id}>{c.label}</option>) : null}
              </select><span>Manage values and approvals in Configuration → Access, approvals and audit → Credentials.</span>
            </label> : null}
          </article>
        ) : null}

        {selected ? (
          <article className="panel">
            <div className="panel-header">
              <h3>Agent-visible operations</h3>
              <span className="status-pill">Per-session grants required</span>
            </div>
            <div className="detail-stack">
              {Object.entries(selected.operations).map(([name, operation]) => (
                <div className="detail-block" key={name}>
                  <div className="feature-list-title-row">
                    <strong>{name}</strong>
                    <div className="status-pills">
                      <span className={`status-pill ${operation.access === "write" ? "state-waiting" : "state-idle"}`}>
                        {operation.access}
                      </span>
                      <span className="status-pill state-idle">{operation.result_mode}</span>
                      {operation.requires_approval ? <span className="status-pill state-waiting">Approval</span> : null}
                    </div>
                  </div>
                  <p className="muted-text">{operation.description}</p>
                  <details>
                    <summary>Input schema</summary>
                    <pre>{JSON.stringify(operation.input_schema, null, 2)}</pre>
                  </details>
                </div>
              ))}
            </div>
          </article>
        ) : null}

        <article className="panel">
          <div className="panel-header">
            <div>
              <h3>Directory override JSON</h3>
              <p className="muted-text">Available drivers: {activeState?.driver_names.join(", ") || "loading"}</p>
            </div>
            {dirty ? <span className="status-pill state-pending">Unsaved</span> : <span className="status-pill state-idle">Current</span>}
          </div>
          <label className="field-stack">
            <span>Connector definitions and auth profile references</span>
            <textarea
              className="json-editor"
              rows={24}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              spellCheck={false}
              disabled={saving}
            />
          </label>
        </article>
      </section>
    </div>
  );
}
