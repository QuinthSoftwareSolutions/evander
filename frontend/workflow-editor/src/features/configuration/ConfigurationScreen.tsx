import { useEffect, useState } from "react";

import { fetchJson } from "../admin/api";
import type { AdminResult } from "../admin/AdminContext";
import { useAdminUi } from "../admin/AdminContext";
import { SaveIcon } from "../admin/PageActionIcons";
import { Tooltip } from "../admin/Tooltip";

type Diagnostic = { code: string; message: string; field?: string | null };

type ConfigurationField = {
  source_domain: string;
  field_name: string;
  label: string;
  kind: "bool" | "int" | "float" | "literal" | "text" | "multiline_text" | "string_list" | "json";
  options: string[];
  scope?: "directory" | "global";
  root_starts_editable: boolean;
};

type ConfigurationSection = {
  title: string;
  fields: ConfigurationField[];
};

type ConfigurationTab = {
  key: string;
  title: string;
  help_text: string | null;
  underlying_domains: string[];
  sections: ConfigurationSection[];
};

type TabSnapshot = {
  key: string;
  title: string;
  raw: {
    exists: boolean;
    config: Record<string, unknown>;
  };
  effective: {
    config: Record<string, unknown>;
    validation_diagnostics: Diagnostic[];
    errors: Diagnostic[];
    payload: unknown;
  };
  parent_defaults: {
    directory_path: string | null;
    config: Record<string, unknown>;
    payload: unknown;
  };
};

type ConfigurationState = {
  directory_scoped_config_enabled: boolean;
  selected_directory_path: string;
  selected_directory: {
    path: string;
    parent_path: string | null;
    can_create: boolean;
    can_delete: boolean;
  };
  tabs: ConfigurationTab[];
  tab_snapshots: Record<string, TabSnapshot>;
};

type DraftFieldState = {
  include: boolean;
  clear: boolean;
  value: string | boolean;
};

type TabDraft = Record<string, DraftFieldState>;
type TabDrafts = Record<string, TabDraft>;

function formatJson(value: unknown): string {
  return JSON.stringify(value ?? {}, null, 2);
}

function normalizeFieldValue(field: ConfigurationField, value: unknown): string | boolean {
  if (field.kind === "bool") {
    return Boolean(value);
  }
  if (field.kind === "string_list") {
    return Array.isArray(value) ? value.map((item) => String(item)).join("\n") : value == null ? "" : String(value);
  }
  if (field.kind === "json") {
    return value == null ? "" : JSON.stringify(value, null, 2);
  }
  return value == null ? "" : String(value);
}

function isGlobalField(field: ConfigurationField): boolean {
  return field.scope === "global";
}

function buildTabDraft(tab: ConfigurationTab | undefined, snapshot: TabSnapshot | undefined, directoryPath: string): TabDraft {
  const draft: TabDraft = {};
  const isRoot = directoryPath === "root";
  for (const section of tab?.sections ?? []) {
    for (const field of section.fields) {
      const rawValue = snapshot?.raw.config[field.field_name];
      const hasExplicitValue = Object.prototype.hasOwnProperty.call(snapshot?.raw.config ?? {}, field.field_name);
      if (isGlobalField(field)) {
        draft[field.field_name] = {
          include: true,
          clear: false,
          value: normalizeFieldValue(field, hasExplicitValue ? rawValue : snapshot?.effective.config[field.field_name]),
        };
        continue;
      }
      if (isRoot) {
        const clear = !hasExplicitValue && !field.root_starts_editable;
        draft[field.field_name] = {
          include: !clear,
          clear,
          value: normalizeFieldValue(field, hasExplicitValue ? rawValue : field.root_starts_editable ? "" : null),
        };
        continue;
      }
      draft[field.field_name] = {
        include: hasExplicitValue,
        clear: hasExplicitValue && rawValue === null,
        value: normalizeFieldValue(field, rawValue),
      };
    }
  }
  return draft;
}

function parseFieldValue(field: ConfigurationField, rawValue: string | boolean): unknown {
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
  if (field.kind === "string_list") {
    return String(rawValue)
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean);
  }
  if (field.kind === "literal") {
    return String(rawValue);
  }
  if (field.kind === "json") {
    const text = String(rawValue).trim();
    if (!text) {
      throw new Error(`${field.label} must be valid JSON.`);
    }
    return JSON.parse(text);
  }
  return String(rawValue).trim();
}

function inheritedPreview(value: unknown): string {
  if (value === undefined) return "Inherited: not set";
  if (value === null) return "Inherited: null";
  if (value === "") return 'Inherited: ""';
  const rendered = typeof value === "string" ? JSON.stringify(value) : JSON.stringify(value);
  if (!rendered) return "Inherited";
  return rendered.length > 120 ? `Inherited: ${rendered.slice(0, 117)}...` : `Inherited: ${rendered}`;
}

function hasDirtyDrafts(dirtyTabs: Record<string, boolean>): boolean {
  return Object.values(dirtyTabs).some(Boolean);
}

export function ConfigurationScreen() {
  const {
    privilegedUnlocked,
    selectedDirectoryPath: globalSelectedDirectoryPath,
    setSelectedDirectoryPath: setGlobalSelectedDirectoryPath,
    refreshAdminState,
  } = useAdminUi();
  const [state, setState] = useState<ConfigurationState | null>(null);
  const [selectedDirectoryPath, setSelectedDirectoryPath] = useState(globalSelectedDirectoryPath);
  const [selectedTab, setSelectedTab] = useState("communications");
  const [collapsedSections, setCollapsedSections] = useState<Record<string, boolean>>({});
  const [drafts, setDrafts] = useState<TabDrafts>({});
  const [dirtyTabs, setDirtyTabs] = useState<Record<string, boolean>>({});
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  async function loadState(directoryPath: string, options?: { preserveDirty?: boolean }) {
    const payload = await fetchJson<ConfigurationState>(
      `/api/configuration/state?directory_path=${encodeURIComponent(directoryPath)}`,
    );
    setState(payload);
    setSelectedDirectoryPath(payload.selected_directory_path);
    if (payload.selected_directory_path !== globalSelectedDirectoryPath) {
      setGlobalSelectedDirectoryPath(payload.selected_directory_path);
    }
    setSelectedTab((current) => (payload.tabs.some((tab) => tab.key === current) ? current : payload.tabs[0]?.key ?? "communications"));
    setDrafts((current) => {
      const next = { ...current };
      for (const tab of payload.tabs) {
        if (options?.preserveDirty && dirtyTabs[tab.key]) {
          continue;
        }
        next[tab.key] = buildTabDraft(tab, payload.tab_snapshots[tab.key], payload.selected_directory_path);
      }
      return next;
    });
    if (!options?.preserveDirty) {
      setDirtyTabs({});
    }
  }

  useEffect(() => {
    let cancelled = false;
    async function refresh() {
      if (state !== null && globalSelectedDirectoryPath === selectedDirectoryPath) {
        return;
      }
      if (
        state !== null
        && globalSelectedDirectoryPath !== selectedDirectoryPath
        && hasDirtyDrafts(dirtyTabs)
        && !window.confirm("Discard unsaved configuration edits and switch directories?")
      ) {
        setGlobalSelectedDirectoryPath(selectedDirectoryPath);
        return;
      }
      try {
        if (!cancelled) {
          setLoading(true);
        }
        await loadState(globalSelectedDirectoryPath);
        if (!cancelled) {
          setError(null);
          setStatusMessage(null);
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
    void refresh();
    return () => {
      cancelled = true;
    };
  }, [globalSelectedDirectoryPath]);

  const selectedTabDefinition = state?.tabs.find((tab) => tab.key === selectedTab);
  const snapshot = state?.tab_snapshots[selectedTab] ?? null;
  const tabDraft = drafts[selectedTab] ?? {};
  const isDirty = dirtyTabs[selectedTab] ?? false;
  const hasUnsavedTabs = hasDirtyDrafts(dirtyTabs);
  const isRoot = selectedDirectoryPath === "root";
  const mutationLocked = !privilegedUnlocked;
  const selectedTabKey = selectedTabDefinition?.key ?? selectedTab;
  const selectedTabHasGlobalFields = Boolean(
    selectedTabDefinition?.sections.some((section) => section.fields.some(isGlobalField)),
  );
  const saveBlockedByConfigMode = !state?.directory_scoped_config_enabled && !selectedTabHasGlobalFields;

  async function reloadCurrentDirectory(options?: { preserveDirty?: boolean }) {
    try {
      setLoading(true);
      await loadState(selectedDirectoryPath, options);
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  }

  async function handleSaveTab() {
    if (!selectedTabDefinition || !snapshot) return;
    const currentDraft = drafts[selectedTab] ?? {};
    const payload: Record<string, unknown> = {};
    try {
      for (const section of selectedTabDefinition.sections) {
        for (const field of section.fields) {
          const fieldState = currentDraft[field.field_name];
          if (!fieldState) continue;
          if (isGlobalField(field)) {
            payload[field.field_name] = parseFieldValue(field, fieldState.value);
            continue;
          }
          if (!state?.directory_scoped_config_enabled) {
            continue;
          }
          if (isRoot) {
            if (fieldState.clear) {
              continue;
            }
            payload[field.field_name] = parseFieldValue(field, fieldState.value);
            continue;
          }
          if (!fieldState.include) {
            continue;
          }
          if (fieldState.clear) {
            payload[field.field_name] = field.kind === "string_list" ? [] : null;
            continue;
          }
          payload[field.field_name] = parseFieldValue(field, fieldState.value);
        }
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      return;
    }

    setSaving(true);
    setStatusMessage(null);
    try {
      const payloadResponse = await fetchJson<{
        write_result: AdminResult<unknown>;
        tab_snapshot: TabSnapshot | null;
      }>(`/api/configuration/domains/${encodeURIComponent(selectedTab)}?directory_path=${encodeURIComponent(selectedDirectoryPath)}`, {
        method: "PUT",
        body: JSON.stringify({ config: payload }),
      });
      if (!payloadResponse.write_result.ok || !payloadResponse.tab_snapshot) {
        throw new Error(payloadResponse.write_result.errors[0]?.message ?? "Save failed.");
      }
      setState((current) =>
        current
          ? {
              ...current,
              tab_snapshots: {
                ...current.tab_snapshots,
                [selectedTab]: payloadResponse.tab_snapshot as TabSnapshot,
              },
            }
          : current,
      );
      setDrafts((current) => ({
        ...current,
        [selectedTab]: buildTabDraft(selectedTabDefinition, payloadResponse.tab_snapshot ?? undefined, selectedDirectoryPath),
      }));
      setDirtyTabs((current) => ({ ...current, [selectedTab]: false }));
      setStatusMessage(
        selectedTabHasGlobalFields
          ? `Saved ${selectedTabDefinition.title} settings.`
          : `Saved ${selectedTabDefinition.title} overrides for ${selectedDirectoryPath}.`,
      );
      setError(null);
      if (selectedTabHasGlobalFields) {
        await refreshAdminState();
      }
      await reloadCurrentDirectory();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSaving(false);
    }
  }

  function updateFieldState(fieldName: string, next: Partial<DraftFieldState>) {
    setDrafts((current) => ({
      ...current,
      [selectedTab]: {
        ...(current[selectedTab] ?? {}),
        [fieldName]: {
          ...(current[selectedTab]?.[fieldName] ?? { include: false, clear: false, value: "" }),
          ...next,
        },
      },
    }));
    setDirtyTabs((current) => ({ ...current, [selectedTab]: true }));
  }

  function sectionStorageKey(sectionTitle: string) {
    return `${selectedTabKey}:${sectionTitle}`;
  }

  function isSectionCollapsed(sectionTitle: string) {
    return collapsedSections[sectionStorageKey(sectionTitle)] ?? true;
  }

  function toggleSection(sectionTitle: string) {
    const key = sectionStorageKey(sectionTitle);
    setCollapsedSections((current) => ({ ...current, [key]: !(current[key] ?? false) }));
  }

  function renderFieldInput(field: ConfigurationField, fieldState: DraftFieldState) {
    const disabled = loading || saving || (!isGlobalField(field) && !state?.directory_scoped_config_enabled);
    if (field.kind === "bool") {
      return (
        <select
          value={String(Boolean(fieldState.value))}
          onChange={(event) => updateFieldState(field.field_name, { value: event.target.value === "true" })}
          disabled={disabled}
        >
          <option value="false">false</option>
          <option value="true">true</option>
        </select>
      );
    }
    if (field.kind === "literal") {
      return (
        <select
          value={String(fieldState.value)}
          onChange={(event) => updateFieldState(field.field_name, { value: event.target.value })}
          disabled={disabled}
        >
          {field.options.map((option) => (
            <option key={option} value={option}>
              {option || "default"}
            </option>
          ))}
        </select>
      );
    }
    if (field.kind === "multiline_text" || field.kind === "string_list" || field.kind === "json") {
      return (
        <textarea
          className="json-editor compact-editor"
          value={String(fieldState.value)}
          spellCheck={false}
          onChange={(event) => updateFieldState(field.field_name, { value: event.target.value })}
          disabled={disabled}
        />
      );
    }
    return (
      <input
        type={field.kind === "int" || field.kind === "float" ? "number" : "text"}
        value={String(fieldState.value)}
        onChange={(event) => updateFieldState(field.field_name, { value: event.target.value })}
        disabled={disabled}
      />
    );
  }

  return (
    <div className="feature-page configuration-page">
      <section className="feature-sidebar">
        <div className="feature-sidebar-header">
          <div>
            <div className="eyebrow">Directory-scoped configuration</div>
            <h1>Configuration</h1>
          </div>
        </div>
        {mutationLocked ? (
          <div className="callout warning-callout">Unlock privileged operations to save configuration overrides.</div>
        ) : null}

        <div className="feature-list configuration-tab-list">
          {(state?.tabs ?? []).map((tab) => (
            <button
              key={tab.key}
              type="button"
              className={`feature-list-item configuration-tab-item${tab.key === selectedTab ? " selected" : ""}`}
              onClick={() => setSelectedTab(tab.key)}
              disabled={loading || saving}
            >
              <div className="feature-list-title-row">
                <strong>{tab.title}</strong>
                {dirtyTabs[tab.key] ? <span className="panel-meta">Unsaved</span> : null}
              </div>
              <div className="feature-list-meta">{tab.help_text ?? "Configuration controls for this domain."}</div>
            </button>
          ))}
          {!state?.tabs.length ? <div className="empty-state">No configuration tabs are available for this directory.</div> : null}
        </div>
      </section>

      <section className="feature-content">
        <div className="feature-toolbar">
          <div>
            <h2>{selectedTabDefinition?.title ?? "Configuration"}</h2>
            <p className="muted-text">
              Editing {selectedDirectoryPath}. Inspect inherited defaults, adjust explicit overrides, and save when the selected tab is ready.
            </p>
          </div>
          <div className="feature-toolbar-actions icon-actions">
            <Tooltip content={selectedTabHasGlobalFields ? "Save the current tab's settings." : "Save the current tab's explicit overrides for the selected directory."}>
              <button
                type="button"
                className="primary"
                onClick={() => void handleSaveTab()}
                disabled={saving || loading || saveBlockedByConfigMode || !selectedTabDefinition || !snapshot || !isDirty || mutationLocked}
                aria-label={saving ? "Saving tab" : "Save tab"}
              >
                {saving ? "…" : <SaveIcon />}
              </button>
            </Tooltip>
          </div>
        </div>

        {!state?.directory_scoped_config_enabled ? (
          <div className="callout warning-callout">
            Directory-scoped config is disabled. You can inspect current values, but create, delete, and save actions are unavailable.
          </div>
        ) : null}
        {mutationLocked ? (
          <div className="callout warning-callout">
            Privileged operations are locked. You can inspect configuration, but save actions are unavailable.
          </div>
        ) : null}
        {statusMessage ? <div className="callout success-callout">{statusMessage}</div> : null}
        {error ? <div className="callout error-callout">{error}</div> : null}
        {hasUnsavedTabs ? (
          <div className="callout warning-callout">
            Unsaved override edits are being preserved during refresh. Switching directories will discard them.
          </div>
        ) : null}

        <div className="feature-grid configuration-summary-grid">
          <article className="panel detail-panel configuration-summary-panel">
            <div className="panel-header">
              <div>
                <h3>Directory summary</h3>
                <p className="muted-text">
                  Active location, inheritance boundaries, and save scope for the selected configuration tab.
                </p>
              </div>
              <span className="panel-meta">{selectedTabDefinition?.title ?? "No tab selected"}</span>
            </div>
            <div className="detail-stack compact-detail-stack">
              <div className="detail-grid">
                <div><span>Directory</span><strong>{selectedDirectoryPath}</strong></div>
                <div><span>Parent</span><strong>{state?.selected_directory.parent_path ?? "root has no parent"}</strong></div>
                <div><span>Scope</span><strong>{isRoot ? "Root defaults" : "Directory override"}</strong></div>
                <div><span>Config mode</span><strong>{state?.directory_scoped_config_enabled ? "Writable" : "Read only"}</strong></div>
                <div><span>Selected tab</span><strong>{selectedTabDefinition?.title ?? "None"}</strong></div>
                <div><span>Underlying domains</span><strong>{selectedTabDefinition?.underlying_domains.join(", ") || "n/a"}</strong></div>
              </div>
            </div>
          </article>
        </div>

        {(selectedTabDefinition?.sections ?? []).map((section) => {
          const sectionIsGlobal = section.fields.every(isGlobalField);
          return (
            <article key={section.title} className="panel section-panel">
              <button
                type="button"
                className="panel-header panel-toggle"
                onClick={() => toggleSection(section.title)}
                aria-expanded={!isSectionCollapsed(section.title)}
              >
                <div>
                  <h3>{section.title}</h3>
                  <p className="muted-text">
                    {sectionIsGlobal
                      ? "Edit runtime-global settings for this section."
                      : isRoot
                      ? "Edit root-level defaults for this section."
                      : "Enable overrides field-by-field to write only the values that should diverge from the parent directory."}
                  </p>
                </div>
                <span className="panel-toggle-indicator">{isSectionCollapsed(section.title) ? "Show" : "Hide"}</span>
              </button>
              {!isSectionCollapsed(section.title) ? (
                <div className="config-field-grid">
                  {section.fields.map((field) => {
                    const fieldIsGlobal = isGlobalField(field);
                    const fieldState = tabDraft[field.field_name] ?? {
                      include: false,
                      clear: false,
                      value: normalizeFieldValue(field, undefined),
                    };
                    const inherited = snapshot?.parent_defaults.config?.[field.field_name];
                    const inputDisabled = fieldIsGlobal
                      ? false
                      : isRoot
                      ? fieldState.clear
                      : !fieldState.include || fieldState.clear;
                    return (
                      <div key={field.field_name} className="config-field-card">
                        <div className="feature-list-title-row">
                          <strong>{field.label}</strong>
                          <Tooltip content={fieldIsGlobal ? "Runtime-global admin setting." : `Underlying config domain: ${field.source_domain}.`}>
                            <span className="panel-meta">{field.source_domain}</span>
                          </Tooltip>
                        </div>
                        {!fieldIsGlobal ? (
                          <div className="config-toggle-row">
                            {!isRoot ? (
                              <label className="checkbox-row">
                                <input
                                  type="checkbox"
                                  checked={fieldState.include}
                                  onChange={(event) => updateFieldState(field.field_name, { include: event.target.checked })}
                                  disabled={!state?.directory_scoped_config_enabled || loading || saving}
                                />
                                <span>Override</span>
                              </label>
                            ) : null}
                            <label className="checkbox-row">
                              <input
                                type="checkbox"
                                checked={fieldState.clear}
                                onChange={(event) =>
                                  updateFieldState(field.field_name, isRoot ? { clear: event.target.checked, include: !event.target.checked } : { clear: event.target.checked })
                                }
                                disabled={!state?.directory_scoped_config_enabled || loading || saving || (!isRoot && !fieldState.include)}
                              />
                              <span>{isRoot ? "Unset" : "Clear"}</span>
                            </label>
                          </div>
                        ) : null}
                        <div className={inputDisabled ? "disabled-block" : ""}>
                          {renderFieldInput(field, fieldState)}
                        </div>
                        {fieldIsGlobal ? (
                          <div className="panel-meta">Runtime-global setting.</div>
                        ) : !isRoot ? (
                          <Tooltip content={inheritedPreview(inherited)} inline={false}>
                            <div className="panel-meta">{inheritedPreview(inherited)}</div>
                          </Tooltip>
                        ) : null}
                      </div>
                    );
                  })}
                </div>
              ) : null}
            </article>
          );
        })}
        {selectedTabDefinition && selectedTabDefinition.sections.length === 0 ? (
          <article className="panel">
            <div className="empty-state">This tab has no editable fields yet.</div>
          </article>
        ) : null}
        {!selectedTabDefinition ? (
          <article className="panel">
            <div className="empty-state">Select a configuration tab to inspect and edit it.</div>
          </article>
        ) : null}

        <article className="panel">
          <div className="panel-header">
            <h3>Validation</h3>
            <span className="panel-meta">{snapshot?.raw.exists ? "Saved overrides present" : "Inherited only"}</span>
          </div>
          <div className="diagnostic-list">
            {!snapshot ? (
              <div className="empty-state">No validation snapshot is available for the selected tab.</div>
            ) : snapshot.effective.validation_diagnostics.length ? (
              snapshot.effective.validation_diagnostics.map((diagnostic, index) => (
                <div key={`${diagnostic.code}-${index}`} className="diagnostic-item">
                  {diagnostic.message}
                </div>
              ))
            ) : (
              <div className="empty-state">No validation errors.</div>
            )}
            {snapshot?.effective.errors.length
              ? snapshot.effective.errors.map((diagnostic, index) => (
                  <div key={`${diagnostic.code}-error-${index}`} className="diagnostic-item error-item">
                    {diagnostic.message}
                  </div>
                ))
              : null}
          </div>
        </article>

        <div className="feature-grid configuration-diagnostics-grid">
          <article className="panel section-panel">
            <button
              type="button"
              className="panel-header panel-toggle"
              onClick={() => toggleSection("__effective_config")}
              aria-expanded={!isSectionCollapsed("__effective_config")}
            >
              <div>
                <h3>Effective config</h3>
                <p className="muted-text">Resolved output across {selectedTabDefinition?.underlying_domains.join(", ") || "the selected domains"}.</p>
              </div>
              <span className="panel-toggle-indicator">{isSectionCollapsed("__effective_config") ? "Show" : "Hide"}</span>
            </button>
            {!isSectionCollapsed("__effective_config") ? (
              <pre className="json-view">{formatJson(snapshot?.effective.payload ?? {})}</pre>
            ) : null}
          </article>

          <article className="panel section-panel">
            <button
              type="button"
              className="panel-header panel-toggle"
              onClick={() => toggleSection("__parent_defaults")}
              aria-expanded={!isSectionCollapsed("__parent_defaults")}
            >
              <div>
                <h3>Parent defaults</h3>
                <p className="muted-text">Inherited diagnostic payload from {snapshot?.parent_defaults.directory_path ?? "root"}.</p>
              </div>
              <span className="panel-toggle-indicator">{isSectionCollapsed("__parent_defaults") ? "Show" : "Hide"}</span>
            </button>
            {!isSectionCollapsed("__parent_defaults") ? (
              <pre className="json-view">{formatJson(snapshot?.parent_defaults.payload ?? {})}</pre>
            ) : null}
          </article>
        </div>
      </section>
    </div>
  );
}
