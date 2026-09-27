import { useEffect, useState } from "react";

import { Tooltip } from "../admin/Tooltip";
import {
  approvalSettingsEqual,
  normalizeApprovalSettings,
  parseNullablePositiveInteger,
  parsePositiveInteger,
  type ApprovalGrantSettings,
  type McpToolPolicyRule,
} from "./sessionApprovalSettings";

export function RenewSessionModal({
  open,
  sessionId,
  renewExtension,
  presets,
  busy,
  locked,
  onChange,
  onClose,
  onSubmit,
}: {
  open: boolean;
  sessionId: string;
  renewExtension: string;
  presets: string[];
  busy: boolean;
  locked: boolean;
  onChange: (value: string) => void;
  onClose: () => void;
  onSubmit: () => void;
}) {
  if (!open) return null;
  return (
    <div className="modal-backdrop" onClick={onClose} role="presentation">
      <div
        className="modal-card session-renew-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="session-renew-modal-title"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="modal-header">
          <div>
            <div className="eyebrow">Session renewal</div>
            <h2 id="session-renew-modal-title">Extend session</h2>
            <p>
              Choose how long to extend <strong>{sessionId}</strong>.
            </p>
          </div>
          <div className="inline-actions">
            <button type="button" className="admin-shell-button admin-shell-button-muted" onClick={onClose}>
              Cancel
            </button>
          </div>
        </div>
        <div className="modal-body">
          <label className="field-stack">
            <span>Extend by</span>
            <input
              autoFocus
              list="renew-extension-presets-modal"
              value={renewExtension}
              onChange={(event) => onChange(event.target.value)}
              disabled={locked || busy}
            />
            <datalist id="renew-extension-presets-modal">
              {presets.map((preset) => (
                <option key={preset} value={preset} />
              ))}
            </datalist>
          </label>
          <div className="feature-toolbar-actions session-renew-actions">
            <button type="button" className="primary" onClick={onSubmit} disabled={locked || busy}>
              {busy ? "Extending..." : "Extend"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export function ApprovalSettingsModal({
  open,
  toolLabel,
  toolValue,
  settings,
  inheritedSettings,
  hardRule,
  busy,
  locked,
  onApply,
  onReset,
  onClose,
  hasExplicitOverride,
}: {
  open: boolean;
  toolLabel: string;
  toolValue: string;
  settings: ApprovalGrantSettings;
  inheritedSettings: ApprovalGrantSettings;
  hardRule: McpToolPolicyRule | null;
  busy: boolean;
  locked: boolean;
  onApply: (settings: ApprovalGrantSettings) => void;
  onReset: () => void;
  onClose: () => void;
  hasExplicitOverride: boolean;
}) {
  const [ttlDraft, setTtlDraft] = useState("");
  const [usesDraft, setUsesDraft] = useState("");
  const [draftSettings, setDraftSettings] = useState<ApprovalGrantSettings>(normalizeApprovalSettings(settings));
  const [error, setError] = useState<string | null>(null);

  function clampSettings(next: ApprovalGrantSettings): ApprovalGrantSettings {
    const ttlLimit = hardRule?.max_ttl_seconds;
    const usesLimit = hardRule?.max_uses;
    return {
      ...next,
      ttl_seconds: ttlLimit == null ? next.ttl_seconds : Math.min(next.ttl_seconds, ttlLimit),
      max_uses:
        usesLimit == null
          ? next.max_uses
          : next.max_uses == null
            ? usesLimit
            : Math.min(next.max_uses, usesLimit),
      payload_mode: hardRule?.require_exact_payload ? "exact" : next.payload_mode,
    };
  }

  useEffect(() => {
    if (!open) return;
    const normalized = clampSettings(normalizeApprovalSettings(settings));
    setDraftSettings(normalized);
    setTtlDraft(String(normalized.ttl_seconds));
    setUsesDraft(normalized.max_uses == null ? "" : String(normalized.max_uses));
    setError(null);
  }, [
    hardRule?.max_ttl_seconds,
    hardRule?.max_uses,
    hardRule?.require_exact_payload,
    open,
    settings.max_uses,
    settings.payload_mode,
    settings.ttl_seconds,
  ]);

  if (!open) return null;

  const effectiveSettings = draftSettings;
  const inherited = clampSettings(normalizeApprovalSettings(inheritedSettings));
  const initialSettings = clampSettings(normalizeApprovalSettings(settings));

  function updateDraft(update: Partial<ApprovalGrantSettings>) {
    setDraftSettings((current) => clampSettings({ ...current, ...update }));
    setError(null);
  }

  function commitTtl() {
    try {
      const ttlSeconds = parsePositiveInteger(ttlDraft, "Grant expires after");
      const next = clampSettings({ ...effectiveSettings, ttl_seconds: ttlSeconds });
      setDraftSettings(next);
      setTtlDraft(String(next.ttl_seconds));
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  function commitUses() {
    try {
      const maxUses = parseNullablePositiveInteger(usesDraft, "Max uses");
      const next = clampSettings({ ...effectiveSettings, max_uses: maxUses });
      setDraftSettings(next);
      setUsesDraft(next.max_uses == null ? "" : String(next.max_uses));
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  function handleResetToInheritedDefaults() {
    if (hasExplicitOverride) {
      onReset();
      onClose();
      return;
    }

    const next = inherited;
    setDraftSettings(next);
    setTtlDraft(String(next.ttl_seconds));
    setUsesDraft(next.max_uses == null ? "" : String(next.max_uses));
    setError(null);
  }

  function handleDone() {
    try {
      const ttlSeconds = parsePositiveInteger(ttlDraft, "Grant expires after");
      const maxUses = parseNullablePositiveInteger(usesDraft, "Max uses");
      const next = clampSettings({
        ...effectiveSettings,
        ttl_seconds: ttlSeconds,
        max_uses: maxUses,
      });
      setDraftSettings(next);
      setTtlDraft(String(next.ttl_seconds));
      setUsesDraft(next.max_uses == null ? "" : String(next.max_uses));

      if (approvalSettingsEqual(next, initialSettings)) {
        onClose();
        return;
      }

      if (approvalSettingsEqual(next, inherited)) {
        if (hasExplicitOverride && !approvalSettingsEqual(initialSettings, inherited)) {
          onReset();
        }
        onClose();
        return;
      }

      onApply(next);
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose} role="presentation">
      <div
        className="modal-card session-approval-settings-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="session-approval-settings-title"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="modal-header">
          <div>
            <div className="eyebrow">MCP approval settings</div>
            <h2 id="session-approval-settings-title">{toolLabel}</h2>
            <p>{toolValue}</p>
          </div>
          <div className="inline-actions">
            <button type="button" className="admin-shell-button admin-shell-button-muted" onClick={onClose}>
              Cancel
            </button>
          </div>
        </div>
        <div className="modal-body session-approval-settings-body">
          {error ? <div className="callout error-callout">{error}</div> : null}
          <dl className="property-grid">
            <div>
              <dt><Tooltip content="This is the current grant expiry inherited from the parent or effective policy."><span>Grant expiry from parent policy</span></Tooltip></dt>
              <dd>{inherited.ttl_seconds}s</dd>
            </div>
            <div>
              <dt><Tooltip content="This is the current use limit inherited from the parent or effective policy. Blank means unlimited."><span>Use limit from parent policy</span></Tooltip></dt>
              <dd>{inherited.max_uses == null ? "unlimited" : inherited.max_uses}</dd>
            </div>
            <div>
              <dt><Tooltip content="This is the current argument matching mode inherited from the parent or effective policy. Exact arguments only reuse a grant when submitted arguments match the approved arguments. May vary allows future calls with different arguments within the same grant limits."><span>Argument matching from parent policy</span></Tooltip></dt>
              <dd>{inherited.payload_mode === "any" ? "May vary" : "Exact arguments"}</dd>
            </div>
            <div>
              <dt><Tooltip content="This is the policy maximum for grant expiry. Saved values are clamped to this limit."><span>Grant expiry capped by policy maximum</span></Tooltip></dt>
              <dd>{hardRule?.max_ttl_seconds == null ? "None" : `${hardRule.max_ttl_seconds}s`}</dd>
            </div>
            <div>
              <dt><Tooltip content="This is the policy maximum for use count. Saved values are clamped to this limit."><span>Use limit capped by policy maximum</span></Tooltip></dt>
              <dd>{hardRule?.max_uses == null ? "None" : hardRule.max_uses}</dd>
            </div>
            <div>
              <dt><Tooltip content="This is the policy maximum for argument matching. When set, exact arguments are required and the select is locked to Exact arguments."><span>Argument matching capped by policy maximum</span></Tooltip></dt>
              <dd>{hardRule?.require_exact_payload ? "Exact arguments required" : "None"}</dd>
            </div>
          </dl>
          <div className="session-approval-settings-grid">
            <label className="field-stack">
              <Tooltip content="How long the approval grant remains valid before it expires. The value is stored in seconds."><span>Grant expires after (seconds)</span></Tooltip>
              <input autoFocus type="number" min="1" step="1" value={ttlDraft} onChange={(event) => setTtlDraft(event.target.value)} onBlur={commitTtl} disabled={locked || busy} />
            </label>
            <label className="field-stack">
              <Tooltip content="How many times this approval grant can be reused. Leave blank for unlimited reuse unless the policy sets a maximum."><span>Max uses (blank = unlimited)</span></Tooltip>
              <input type="number" min="1" step="1" value={usesDraft} onChange={(event) => setUsesDraft(event.target.value)} onBlur={commitUses} placeholder="unlimited" disabled={locked || busy} />
            </label>
            <label className="field-stack">
              <Tooltip content="Exact arguments only reuse the grant when submitted arguments match the approved arguments. May vary allows future calls with different arguments within the same grant limits."><span>Argument matching</span></Tooltip>
              <select value={effectiveSettings.payload_mode} onChange={(event) => updateDraft({ payload_mode: event.target.value === "any" ? "any" : "exact" })} disabled={locked || busy || Boolean(hardRule?.require_exact_payload)}>
                <option value="exact">Exact arguments</option>
                <option value="any">May vary</option>
              </select>
            </label>
          </div>
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={effectiveSettings.max_uses == null}
              onChange={(event) => {
                const maxUses = event.target.checked ? null : 1;
                setUsesDraft(maxUses == null ? "" : String(maxUses));
                updateDraft({ max_uses: maxUses });
              }}
              disabled={locked || busy || hardRule?.max_uses != null}
            />
            <span>Unlimited uses</span>
          </label>
          <div className="feature-toolbar-actions session-approval-settings-actions">
            <button type="button" className="admin-shell-button admin-shell-button-muted" onClick={onClose} disabled={locked || busy}>Discard changes</button>
            <button type="button" onClick={handleResetToInheritedDefaults} disabled={locked || busy}>Reset to inherited defaults</button>
            <button type="button" className="primary" onClick={handleDone} disabled={locked || busy}>Done</button>
          </div>
        </div>
      </div>
    </div>
  );
}
