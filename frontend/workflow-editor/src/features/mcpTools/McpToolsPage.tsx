import { useEffect, useMemo, useState } from "react";

import { fetchJson } from "../admin/api";
import { useAdminUi } from "../admin/AdminContext";
import { SaveIcon } from "../admin/PageActionIcons";
import { Tooltip } from "../admin/Tooltip";

type ApprovalPayloadMode = "exact" | "any";

type ApprovalGrantSettings = {
  ttl_seconds: number;
  max_uses: number | null;
  payload_mode: ApprovalPayloadMode;
};

type McpToolPolicyRule = {
  denied?: boolean;
  require_approval?: boolean;
  default_settings?: ApprovalGrantSettings | null;
  max_ttl_seconds?: number | null;
  max_uses?: number | null;
  require_exact_payload?: boolean;
};

type McpToolPolicyItem = {
  value: string;
  label: string;
  description?: string;
  local_policy?: McpToolPolicyRule | null;
  effective_policy?: McpToolPolicyRule | null;
  effective_settings: ApprovalGrantSettings;
  approval_required: boolean;
  denied: boolean;
};

type McpToolsResponse = {
  directory_path: string;
  revision: string;
  mcp_tool_policies: Record<string, McpToolPolicyRule>;
  items: McpToolPolicyItem[];
};

const DEFAULT_APPROVAL_SETTINGS: ApprovalGrantSettings = {
  ttl_seconds: 3600,
  max_uses: 1,
  payload_mode: "exact",
};

function positiveIntegerOrNull(value: string, label: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  if (!Number.isInteger(parsed) || parsed < 1) {
    throw new Error(`${label} must be a positive whole number or blank.`);
  }
  return parsed;
}

function positiveInteger(value: string, label: string): number {
  const parsed = positiveIntegerOrNull(value, label);
  if (parsed == null) {
    throw new Error(`${label} is required.`);
  }
  return parsed;
}

function normalizeSettings(settings: ApprovalGrantSettings | null | undefined): ApprovalGrantSettings {
  return {
    ttl_seconds: Math.max(1, Math.trunc(Number(settings?.ttl_seconds ?? DEFAULT_APPROVAL_SETTINGS.ttl_seconds))),
    max_uses: settings?.max_uses == null ? null : Math.max(1, Math.trunc(Number(settings.max_uses))),
    payload_mode: settings?.payload_mode === "any" ? "any" : "exact",
  };
}

function clonePolicies(policies: Record<string, McpToolPolicyRule> | null | undefined): Record<string, McpToolPolicyRule> {
  const result: Record<string, McpToolPolicyRule> = {};
  for (const [toolName, rule] of Object.entries(policies ?? {})) {
    result[toolName] = {
      ...rule,
      default_settings: rule.default_settings ? normalizeSettings(rule.default_settings) : null,
    };
  }
  return result;
}

function ruleHasLocalContent(rule: McpToolPolicyRule | undefined): boolean {
  if (!rule) return false;
  return Boolean(
    rule.denied ||
      rule.require_approval ||
      rule.default_settings ||
      rule.max_ttl_seconds != null ||
      rule.max_uses != null ||
      rule.require_exact_payload,
  );
}

function compactPolicies(policies: Record<string, McpToolPolicyRule>): Record<string, McpToolPolicyRule> {
  const result: Record<string, McpToolPolicyRule> = {};
  for (const [toolName, rule] of Object.entries(policies)) {
    if (!ruleHasLocalContent(rule)) continue;
    result[toolName] = {
      denied: Boolean(rule.denied),
      require_approval: Boolean(rule.require_approval),
      default_settings: rule.default_settings ? normalizeSettings(rule.default_settings) : null,
      max_ttl_seconds: rule.max_ttl_seconds ?? null,
      max_uses: rule.max_uses ?? null,
      require_exact_payload: Boolean(rule.require_exact_payload),
    };
  }
  return result;
}

function formatUses(value: number | null | undefined): string {
  return value == null ? "unlimited" : String(value);
}

export function McpToolsPage() {
  const { selectedDirectoryPath, openDirectoryPicker, privilegedUnlocked } = useAdminUi();
  const [response, setResponse] = useState<McpToolsResponse | null>(null);
  const [draftPolicies, setDraftPolicies] = useState<Record<string, McpToolPolicyRule>>({});
  const [selectedTool, setSelectedTool] = useState("");
  const [filter, setFilter] = useState("");
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function run() {
      try {
        const query = new URLSearchParams({ directory_path: selectedDirectoryPath });
        const payload = await fetchJson<McpToolsResponse>(`/api/admin/mcp-tools?${query.toString()}`);
        if (cancelled) return;
        setResponse(payload);
        setDraftPolicies(clonePolicies(payload.mcp_tool_policies));
        setDirty(false);
        setStatus(null);
        setError(null);
        setSelectedTool((current) => current && payload.items.some((item) => item.value === current) ? current : payload.items[0]?.value ?? "");
      } catch (reason) {
        if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason));
      }
    }
    void run();
    return () => {
      cancelled = true;
    };
  }, [selectedDirectoryPath]);

  const visibleItems = useMemo(() => {
    const normalized = filter.trim().toLowerCase();
    const items = response?.items ?? [];
    if (!normalized) return items;
    return items.filter((item) =>
      [item.value, item.label, item.description ?? ""].join(" ").toLowerCase().includes(normalized),
    );
  }, [filter, response?.items]);

  const selectedItem = useMemo(
    () => response?.items.find((item) => item.value === selectedTool) ?? visibleItems[0] ?? null,
    [response?.items, selectedTool, visibleItems],
  );
  const selectedRule: McpToolPolicyRule = selectedItem ? draftPolicies[selectedItem.value] ?? {} : {};
  const effectiveRule = selectedItem?.effective_policy ?? null;
  const inheritedDeny = Boolean(selectedItem?.denied && !selectedRule.denied);
  const mutationLocked = !privilegedUnlocked;

  function updateRule(toolName: string, update: Partial<McpToolPolicyRule>) {
    setDraftPolicies((current) => ({
      ...current,
      [toolName]: {
        ...(current[toolName] ?? {}),
        ...update,
      },
    }));
    setDirty(true);
    setStatus(null);
    setError(null);
  }

  function updateDefaultSettings(toolName: string, update: Partial<ApprovalGrantSettings>) {
    const currentRule = draftPolicies[toolName] ?? {};
    const currentSettings = normalizeSettings(currentRule.default_settings ?? DEFAULT_APPROVAL_SETTINGS);
    updateRule(toolName, {
      default_settings: normalizeSettings({
        ...currentSettings,
        ...update,
      }),
    });
  }

  function clearRule(toolName: string) {
    setDraftPolicies((current) => {
      const next = { ...current };
      delete next[toolName];
      return next;
    });
    setDirty(true);
    setStatus(null);
    setError(null);
  }

  async function save() {
    if (!response || !dirty) return;
    setBusy(true);
    try {
      const payload = await fetchJson<McpToolsResponse>(
        `/api/admin/mcp-tools?${new URLSearchParams({ directory_path: selectedDirectoryPath }).toString()}`,
        {
          method: "PUT",
          body: JSON.stringify({
            expected_revision: response.revision,
            mcp_tool_policies: compactPolicies(draftPolicies),
          }),
        },
      );
      setResponse(payload);
      setDraftPolicies(clonePolicies(payload.mcp_tool_policies));
      setDirty(false);
      setStatus("Saved MCP tool policy.");
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      setStatus(null);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="feature-page mcp-tools-page">
      <section className="feature-sidebar">
        <div className="feature-sidebar-header">
          <div>
            <div className="eyebrow">Directory policy</div>
            <h1>MCP Tools</h1>
          </div>
        </div>
        {mutationLocked ? <div className="callout warning-callout">Unlock privileged operations to edit MCP tool policy.</div> : null}
        <label className="field-stack">
          <span>Directory</span>
          <button type="button" className="directory-picker-button" onClick={openDirectoryPicker}>
            {selectedDirectoryPath}
          </button>
        </label>
        <label className="field-stack">
          <span>Find tool</span>
          <input value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="tool name or description" />
        </label>
        <div className="feature-list mcp-tool-policy-list">
          {visibleItems.map((item) => {
            const localRule = draftPolicies[item.value];
            const localOverride = ruleHasLocalContent(localRule);
            return (
              <button
                key={item.value}
                type="button"
                className={`feature-list-item mcp-tool-policy-list-item${selectedItem?.value === item.value ? " selected" : ""}`}
                onClick={() => setSelectedTool(item.value)}
              >
                <div className="feature-list-title-row">
                  <strong>{item.label}</strong>
                  <span className={`status-pill state-${item.denied ? "failed" : item.approval_required ? "idle" : "running"}`}>
                    {item.denied ? "Denied" : item.approval_required ? "Approval" : "Allowed"}
                  </span>
                </div>
                <div className="feature-list-meta">{item.value}</div>
                <div className="feature-list-meta">
                  {localOverride ? "Local rule" : "Inherited/default policy"}
                </div>
              </button>
            );
          })}
          {visibleItems.length === 0 ? <div className="empty-state">No MCP tools matched the filter.</div> : null}
        </div>
      </section>
      <section className="feature-content">
        <div className="feature-toolbar">
          <div>
            <h2>{selectedItem?.label ?? "Select a tool"}</h2>
            <p className="muted-text">
              {selectedItem ? selectedItem.description || selectedItem.value : "Choose an MCP tool to configure directory policy."}
            </p>
          </div>
          <div className="feature-toolbar-actions icon-actions">
            <Tooltip content="Save the local MCP tool policy for this directory.">
              <button
                type="button"
                className="primary"
                onClick={() => void save()}
                disabled={busy || mutationLocked || !dirty}
                aria-label={busy ? "Saving MCP tool policy" : "Save MCP tool policy"}
              >
                {busy ? "..." : <SaveIcon />}
              </button>
            </Tooltip>
          </div>
        </div>
        {error ? <div className="callout error-callout">{error}</div> : null}
        {status ? <div className="callout success-callout">{status}</div> : null}
        {selectedItem ? (
          <div className="stack-layout">
            <article className="panel">
              <div className="panel-header">
                <h3>Effective policy</h3>
                <span className="panel-meta">{response?.directory_path ?? selectedDirectoryPath}</span>
              </div>
              <div className="panel-body">
                <dl className="property-grid">
                  <div><dt>Tool</dt><dd>{selectedItem.value}</dd></div>
                  <div><dt>Status</dt><dd>{selectedItem.denied ? "Denied" : selectedItem.approval_required ? "Approval required" : "Allowed by session policy"}</dd></div>
                  <div><dt>TTL</dt><dd>{selectedItem.effective_settings.ttl_seconds}s</dd></div>
                  <div><dt>Uses</dt><dd>{formatUses(selectedItem.effective_settings.max_uses)}</dd></div>
                  <div><dt>Payload</dt><dd>{selectedItem.effective_settings.payload_mode === "any" ? "May vary" : "Exact payload"}</dd></div>
                  <div><dt>Local rule</dt><dd>{ruleHasLocalContent(selectedRule) ? "Configured" : "None"}</dd></div>
                </dl>
                {inheritedDeny ? <div className="callout warning-callout">This tool is denied by an ancestor directory and cannot be re-enabled here.</div> : null}
              </div>
            </article>
            <article className="panel">
              <div className="panel-header">
                <h3>Local rule</h3>
                <div className="feature-toolbar-actions">
                  <button type="button" onClick={() => clearRule(selectedItem.value)} disabled={mutationLocked || !ruleHasLocalContent(selectedRule)}>
                    Clear local rule
                  </button>
                </div>
              </div>
              <div className="panel-body mcp-tool-policy-editor">
                <label className="checkbox-row">
                  <input
                    type="checkbox"
                    checked={Boolean(selectedRule.denied)}
                    onChange={(event) => updateRule(selectedItem.value, { denied: event.target.checked })}
                    disabled={mutationLocked}
                  />
                  <span>Disallow this tool in this directory and descendants</span>
                </label>
                <label className="checkbox-row">
                  <input
                    type="checkbox"
                    checked={Boolean(selectedRule.require_approval)}
                    onChange={(event) => updateRule(selectedItem.value, { require_approval: event.target.checked })}
                    disabled={mutationLocked || inheritedDeny}
                  />
                  <span>Always require per-invocation approval</span>
                </label>
                <div className="mcp-tool-policy-grid">
                  <label className="field-stack">
                    <span>Default TTL seconds</span>
                    <input
                      type="number"
                      min="1"
                      step="1"
                      value={normalizeSettings(selectedRule.default_settings ?? DEFAULT_APPROVAL_SETTINGS).ttl_seconds}
                      onChange={(event) => {
                        try {
                          updateDefaultSettings(selectedItem.value, { ttl_seconds: positiveInteger(event.target.value, "Default TTL seconds") });
                        } catch (reason) {
                          setError(reason instanceof Error ? reason.message : String(reason));
                        }
                      }}
                      disabled={mutationLocked || inheritedDeny}
                    />
                  </label>
                  <label className="field-stack">
                    <span>Default uses</span>
                    <input
                      type="number"
                      min="1"
                      step="1"
                      value={normalizeSettings(selectedRule.default_settings ?? DEFAULT_APPROVAL_SETTINGS).max_uses ?? ""}
                      placeholder="unlimited"
                      onChange={(event) => {
                        try {
                          updateDefaultSettings(selectedItem.value, { max_uses: positiveIntegerOrNull(event.target.value, "Default uses") });
                        } catch (reason) {
                          setError(reason instanceof Error ? reason.message : String(reason));
                        }
                      }}
                      disabled={mutationLocked || inheritedDeny}
                    />
                  </label>
                  <label className="field-stack">
                    <span>Default payload</span>
                    <select
                      value={normalizeSettings(selectedRule.default_settings ?? DEFAULT_APPROVAL_SETTINGS).payload_mode}
                      onChange={(event) => updateDefaultSettings(selectedItem.value, { payload_mode: event.target.value === "any" ? "any" : "exact" })}
                      disabled={mutationLocked || inheritedDeny}
                    >
                      <option value="exact">Exact payload</option>
                      <option value="any">May vary</option>
                    </select>
                  </label>
                  <label className="field-stack">
                    <span>Hard max TTL seconds</span>
                    <input
                      type="number"
                      min="1"
                      step="1"
                      value={selectedRule.max_ttl_seconds ?? ""}
                      placeholder="no hard cap"
                      onChange={(event) => {
                        try {
                          updateRule(selectedItem.value, { max_ttl_seconds: positiveIntegerOrNull(event.target.value, "Hard max TTL seconds") });
                        } catch (reason) {
                          setError(reason instanceof Error ? reason.message : String(reason));
                        }
                      }}
                      disabled={mutationLocked}
                    />
                  </label>
                  <label className="field-stack">
                    <span>Hard max uses</span>
                    <input
                      type="number"
                      min="1"
                      step="1"
                      value={selectedRule.max_uses ?? ""}
                      placeholder="no hard cap"
                      onChange={(event) => {
                        try {
                          updateRule(selectedItem.value, { max_uses: positiveIntegerOrNull(event.target.value, "Hard max uses") });
                        } catch (reason) {
                          setError(reason instanceof Error ? reason.message : String(reason));
                        }
                      }}
                      disabled={mutationLocked}
                    />
                  </label>
                  <label className="checkbox-row mcp-tool-policy-inline-toggle">
                    <input
                      type="checkbox"
                      checked={Boolean(selectedRule.require_exact_payload)}
                      onChange={(event) => updateRule(selectedItem.value, { require_exact_payload: event.target.checked })}
                      disabled={mutationLocked}
                    />
                    <span>Force exact payload</span>
                  </label>
                </div>
                <div className="feature-toolbar-actions">
                  <button
                    type="button"
                    onClick={() => updateRule(selectedItem.value, { default_settings: null })}
                    disabled={mutationLocked || !selectedRule.default_settings}
                  >
                    Clear local defaults
                  </button>
                  <button
                    type="button"
                    className="primary"
                    onClick={() => void save()}
                    disabled={busy || mutationLocked || !dirty}
                  >
                    {busy ? "Saving..." : "Save policy"}
                  </button>
                </div>
              </div>
            </article>
            <article className="panel">
              <details className="compact-disclosure">
                <summary className="compact-disclosure-summary feature-list-title-row">
                  <h3>Resolved JSON</h3>
                  <span className="panel-meta">{effectiveRule ? "Rule present" : "No effective rule"}</span>
                </summary>
                <pre className="log-view">{JSON.stringify(effectiveRule ?? {}, null, 2)}</pre>
              </details>
            </article>
          </div>
        ) : (
          <div className="empty-state">Select an MCP tool to inspect its policy.</div>
        )}
      </section>
    </div>
  );
}
