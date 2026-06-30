import { useEffect, useMemo, useState } from "react";

import { fetchJson } from "../admin/api";
import type { AdminAddonState, AdminAddonStatus } from "../admin/types";

async function loadAddonState(): Promise<AdminAddonState> {
  return fetchJson<AdminAddonState>("/api/admin/addons");
}

function sortedIds(values: ReadonlyArray<string>): string[] {
  return [...values].sort((left, right) => left.localeCompare(right));
}

function sameIds(left: ReadonlyArray<string>, right: ReadonlyArray<string>): boolean {
  const leftSorted = sortedIds(left);
  const rightSorted = sortedIds(right);
  return leftSorted.length === rightSorted.length && leftSorted.every((value, index) => value === rightSorted[index]);
}

function statusClass(addon: AdminAddonStatus, draftEnabled: boolean): string {
  if (addon.load_error) return "state-failed";
  if (draftEnabled && !addon.loaded) return "state-pending";
  if (!draftEnabled && addon.loaded) return "state-waiting";
  if (addon.loaded) return "state-running";
  return "state-idle";
}

function statusLabel(addon: AdminAddonStatus, draftEnabled: boolean): string {
  if (addon.load_error) return "Load error";
  if (draftEnabled && !addon.loaded) return "Pending enable";
  if (!draftEnabled && addon.loaded) return "Pending disable";
  if (addon.loaded) return "Loaded";
  return "Disabled";
}

function capabilityLabels(addon: AdminAddonStatus): string[] {
  const labels: string[] = [];
  if (addon.has_admin_ui) labels.push("Admin UI");
  if (addon.has_admin_api) labels.push("Admin API");
  if (addon.has_workflow_providers) labels.push("Workflow nodes");
  if (addon.has_mcp_tools) labels.push("MCP tools");
  return labels.length ? labels : ["Metadata only"];
}

export function AddonsPage() {
  const [state, setState] = useState<AdminAddonState | null>(null);
  const [draftEnabledIds, setDraftEnabledIds] = useState<string[]>([]);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);

  async function refreshAddons(options?: { preserveDraft?: boolean }) {
    const payload = await loadAddonState();
    setState(payload);
    if (!options?.preserveDraft) {
      setDraftEnabledIds(payload.explicit_enabled_addon_ids);
    }
    setSelectedKey((current) => {
      if (current && payload.addons.some((addon) => addon.candidate_key === current)) return current;
      return payload.addons[0]?.candidate_key ?? null;
    });
  }

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const payload = await loadAddonState();
        if (cancelled) return;
        setState(payload);
        setDraftEnabledIds(payload.explicit_enabled_addon_ids);
        setSelectedKey(payload.addons[0]?.candidate_key ?? null);
        setError(null);
      } catch (reason: unknown) {
        if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  const draftEnabledSet = useMemo(() => new Set(draftEnabledIds), [draftEnabledIds]);
  const dirty = Boolean(state && !sameIds(draftEnabledIds, state.explicit_enabled_addon_ids));
  const selectedAddon = useMemo(
    () => state?.addons.find((addon) => addon.candidate_key === selectedKey) ?? state?.addons[0] ?? null,
    [selectedKey, state],
  );
  const draftRestartServices = useMemo(() => {
    if (!state) return [];
    const services = new Set<string>();
    for (const addon of state.addons) {
      const draftEnabled = addon.addon_id ? draftEnabledSet.has(addon.addon_id) : false;
      if (draftEnabled === addon.loaded) continue;
      for (const service of addon.required_restart_services.length ? addon.required_restart_services : state.required_restart_services) {
        services.add(service);
      }
      if (addon.has_mcp_tools) services.add("evander-gateway");
    }
    return [...services];
  }, [draftEnabledSet, state]);
  const draftWarnings = useMemo(() => {
    if (!state) return [];
    return state.addons.filter((addon) => {
      if (!addon.addon_id) return false;
      return addon.loaded && !draftEnabledSet.has(addon.addon_id) && addon.referenced_workflow_node_types.length > 0;
    });
  }, [draftEnabledSet, state]);

  function toggleAddon(addon: AdminAddonStatus) {
    if (!addon.addon_id || !addon.configurable) return;
    setDraftEnabledIds((current) => {
      if (current.includes(addon.addon_id as string)) {
        return current.filter((item) => item !== addon.addon_id);
      }
      return [...current, addon.addon_id as string];
    });
    setStatus(null);
  }

  async function saveAddons() {
    if (!state || !dirty) return;
    setSaving(true);
    setError(null);
    setStatus(null);
    try {
      const payload = await fetchJson<AdminAddonState>("/api/admin/addons/enabled", {
        method: "PUT",
        body: JSON.stringify({
          enabled_addon_ids: draftEnabledIds,
          expected_revision: state.revision,
        }),
      });
      setState(payload);
      setDraftEnabledIds(payload.explicit_enabled_addon_ids);
      setStatus("Saved add-on enablement. Restart the listed services for changes to take effect.");
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="feature-page addons-page">
      <section className="feature-sidebar">
        <div className="feature-sidebar-header">
          <div>
            <div className="eyebrow">Runtime extensions</div>
            <h1>Add-ons</h1>
          </div>
          <div className="inline-actions compact-actions">
            <button type="button" onClick={() => void refreshAddons({ preserveDraft: dirty })} disabled={loading || saving}>
              Refresh
            </button>
          </div>
        </div>
        <div className="feature-list addon-list">
          {state?.addons.map((addon) => {
            const draftEnabled = addon.addon_id ? draftEnabledSet.has(addon.addon_id) : false;
            return (
              <button
                key={addon.candidate_key}
                type="button"
                className={`feature-list-item addon-list-item${selectedAddon?.candidate_key === addon.candidate_key ? " selected" : ""}`}
                onClick={() => setSelectedKey(addon.candidate_key)}
              >
                <div className="feature-list-title-row">
                  <strong>{addon.display_name}</strong>
                  <span className={`status-pill ${statusClass(addon, draftEnabled)}`}>{statusLabel(addon, draftEnabled)}</span>
                </div>
                <div className="feature-list-meta">{addon.addon_id ?? addon.source}</div>
              </button>
            );
          })}
          {!loading && state?.addons.length === 0 ? <div className="empty-state">No configured add-ons found.</div> : null}
        </div>
      </section>

      <section className="feature-content">
        <div className="feature-toolbar">
          <div>
            <h2>{selectedAddon?.display_name ?? "Add-on management"}</h2>
            <p className="muted-text">Enable or disable configured add-ons. Changes are applied after service restart.</p>
          </div>
          <div className="feature-toolbar-actions">
            <button type="button" onClick={() => setDraftEnabledIds(state?.explicit_enabled_addon_ids ?? [])} disabled={!dirty || saving}>
              Reset
            </button>
            <button type="button" className="primary" onClick={() => void saveAddons()} disabled={!dirty || saving || !state}>
              {saving ? "Saving..." : "Save enablement"}
            </button>
          </div>
        </div>

        {error ? <div className="callout warning-callout">{error}</div> : null}
        {status ? <div className="callout">{status}</div> : null}
        {state?.catalog_error ? <div className="callout warning-callout">Loaded add-on catalog error: {state.catalog_error}</div> : null}
        {state?.workflow_reference_error ? <div className="callout warning-callout">Workflow reference check failed: {state.workflow_reference_error}</div> : null}
        {state?.config_mismatch ? <div className="callout warning-callout">Host and trigger-orchestrator add-on loader settings differ. Saving from this page will align them.</div> : null}
        {dirty ? (
          <div className="callout warning-callout">
            Add-on route, workflow, and MCP availability is restart-gated. Save writes config only; restart services to apply changes.
          </div>
        ) : null}
        {draftWarnings.length > 0 ? (
          <div className="callout warning-callout">
            Disabling {draftWarnings.map((addon) => addon.display_name).join(", ")} may break configured workflow packs that reference add-on node types.
          </div>
        ) : null}

        <div className="detail-grid addons-detail-grid">
          <article className="panel">
            <div className="panel-header">
              <h3>Enablement</h3>
              {dirty ? <span className="status-pill state-pending">Unsaved changes</span> : <span className="status-pill state-idle">Current</span>}
            </div>
            <div className="form-grid">
              {state?.addons.map((addon) => {
                const draftEnabled = addon.addon_id ? draftEnabledSet.has(addon.addon_id) : false;
                return (
                  <label key={addon.candidate_key} className="checkbox-row">
                    <input
                      type="checkbox"
                      checked={draftEnabled}
                      disabled={!addon.configurable || saving}
                      onChange={() => toggleAddon(addon)}
                    />
                    <span>
                      <strong>{addon.display_name}</strong>
                      <span className="muted-text"> {addon.addon_id ?? addon.source}</span>
                    </span>
                  </label>
                );
              })}
            </div>
          </article>

          <article className="panel">
            <div className="panel-header">
              <h3>Restart impact</h3>
              <span className={`status-pill ${dirty || state?.restart_required ? "state-waiting" : "state-running"}`}>
                {dirty || state?.restart_required ? "Restart required" : "In sync"}
              </span>
            </div>
            <p className="muted-text">Affected services:</p>
            <div className="status-pills">
              {(dirty ? draftRestartServices : state?.required_restart_services ?? []).map((service) => (
                <span key={service} className="status-pill state-waiting">{service}</span>
              ))}
              {!(dirty ? draftRestartServices : state?.required_restart_services ?? []).length ? (
                <span className="status-pill state-running">None</span>
              ) : null}
            </div>
          </article>

          {selectedAddon ? (
            <article className="panel">
              <div className="panel-header">
                <h3>Selected add-on</h3>
                <span className={`status-pill ${statusClass(selectedAddon, selectedAddon.addon_id ? draftEnabledSet.has(selectedAddon.addon_id) : false)}`}>
                  {statusLabel(selectedAddon, selectedAddon.addon_id ? draftEnabledSet.has(selectedAddon.addon_id) : false)}
                </span>
              </div>
              <dl className="metadata-grid">
                <div><dt>ID</dt><dd>{selectedAddon.addon_id ?? "Unavailable"}</dd></div>
                <div><dt>Source</dt><dd>{selectedAddon.source}</dd></div>
                <div><dt>Source type</dt><dd>{selectedAddon.source_type}</dd></div>
                <div><dt>Entry point</dt><dd>{selectedAddon.entry_point_name ?? "n/a"}</dd></div>
              </dl>
              {selectedAddon.load_error ? <div className="callout warning-callout">{selectedAddon.load_error}</div> : null}
            </article>
          ) : null}

          {selectedAddon ? (
            <article className="panel">
              <div className="panel-header">
                <h3>Capabilities</h3>
                <span className="status-pill">{capabilityLabels(selectedAddon).length}</span>
              </div>
              <div className="status-pills">
                {capabilityLabels(selectedAddon).map((label) => (
                  <span key={label} className="status-pill state-idle">{label}</span>
                ))}
              </div>
              {selectedAddon.workflow_node_types.length > 0 ? (
                <>
                  <p className="muted-text">Workflow node types:</p>
                  <div className="status-pills">
                    {selectedAddon.workflow_node_types.map((nodeType) => (
                      <span key={nodeType} className={`status-pill ${selectedAddon.referenced_workflow_node_types.includes(nodeType) ? "state-waiting" : "state-idle"}`}>
                        {nodeType}
                      </span>
                    ))}
                  </div>
                </>
              ) : null}
            </article>
          ) : null}
        </div>
      </section>
    </div>
  );
}
