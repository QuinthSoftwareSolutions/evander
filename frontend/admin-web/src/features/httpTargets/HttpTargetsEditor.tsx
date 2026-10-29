import { useEffect, useState } from "react";
import { fetchJson } from "../admin/api";

import {
  createHttpTargetDraftEntry,
  httpTargetsValidationError,
  type HttpTargetDraftEntry,
} from "./httpTargetDraft";

type HttpTargetsEditorProps = {
  entries: readonly HttpTargetDraftEntry[];
  onChange: (entries: HttpTargetDraftEntry[]) => void;
  onValidationChange?: (error: string | null) => void;
  disabled?: boolean;
  sourceError?: string | null;
  sessionId?: string | null;
  directoryPath?: string;
};

export function HttpTargetsEditor({
  entries = [],
  onChange,
  onValidationChange,
  disabled = false,
  sourceError = null,
  sessionId = null,
  directoryPath,
}: HttpTargetsEditorProps) {
  const validationError = httpTargetsValidationError(entries, sourceError);
  const [profiles, setProfiles] = useState<Array<{id: string; profile: {browser_origin: string; enabled: boolean}}>>([]);
  const [profileError, setProfileError] = useState<string | null>(null);
  useEffect(() => {
    setProfiles([]);
    setProfileError(null);
    if (!directoryPath) return;
    const controller = new AbortController();
    fetchJson<{profiles: Array<{id: string; profile: {browser_origin: string; enabled: boolean}}>}>(
      `/api/managed-website-sessions?directory_path=${encodeURIComponent(directoryPath)}`, {signal: controller.signal})
      .then(data => { if (!controller.signal.aborted) setProfiles(data.profiles ?? []); })
      .catch(() => { if (!controller.signal.aborted) setProfileError("Managed login profiles are unavailable. Existing bindings are preserved."); });
    return () => controller.abort();
  }, [directoryPath]);

  useEffect(() => {
    onValidationChange?.(validationError);
  }, [onValidationChange, validationError]);

  function updateEntry(id: string, update: Partial<HttpTargetDraftEntry>) {
    onChange(entries.map((entry) => entry.id === id ? { ...entry, ...update } : entry));
  }

  return (
    <div className="structured-input-editor http-targets-editor">
      <p className="field-description">
        Allow this session to reach specific HTTP(S) base URLs through the session relay. This is
        separate from Web search. Select a managed login profile to let the host authenticate the browser.
        Existing workflow metadata is preserved while an alias remains unchanged.
      </p>
      {validationError ? <div className="callout error-callout">{validationError}</div> : null}
      {profileError ? <div className="callout">{profileError}</div> : null}
      <div className="inline-actions compact-actions">
        <button
          type="button"
          onClick={() => onChange([...entries, createHttpTargetDraftEntry()])}
          disabled={disabled}
        >
          Add target
        </button>
      </div>
      {entries.length === 0 ? (
        <div className="empty-state">No external HTTP targets are granted.</div>
      ) : null}
      {entries.map((entry) => {
        const alias = entry.alias.trim();
        const relayPath = alias && sessionId
          ? `$HTTP_TARGET_RELAY_BASE_URL/session/${sessionId}/target/${encodeURIComponent(alias)}/`
          : null;
        return (
          <div key={entry.id} className="structured-input-json-row http-target-row">
            <div className="string-list-selector-row structured-input-row structured-input-map-row">
              <label className="field-stack structured-input-field">
                <span>Alias</span>
                <input
                  value={entry.alias}
                  onChange={(event) => {
                    const alias = event.target.value;
                    updateEntry(entry.id, {
                      alias,
                      ...(alias.trim() !== entry.alias.trim() ? { metadata: {} } : {}),
                    });
                  }}
                  placeholder="service-api"
                  disabled={disabled}
                />
              </label>
              <label className="field-stack structured-input-field">
                <span>Allowed base URL</span>
                <input
                  value={entry.baseUrl}
                  onChange={(event) => updateEntry(entry.id, { baseUrl: event.target.value })}
                  placeholder="https://api.example.com/v1"
                  spellCheck={false}
                  disabled={disabled}
                />
              </label>
              <div className="inline-actions compact-actions">
                <button
                  type="button"
                  className="danger"
                  onClick={() => onChange(entries.filter((candidate) => candidate.id !== entry.id))}
                  disabled={disabled}
                >
                  Remove
                </button>
              </div>
            </div>
            <label className="field-stack">
              <span>Managed website login</span>
              <select aria-label="Managed website login" value={entry.managedWebsiteSession ?? ""} disabled={disabled}
                onChange={event => updateEntry(entry.id, {managedWebsiteSession: event.target.value})}>
                <option value="">None — application authentication supplied by the agent</option>
                {entry.managedWebsiteSession && !profiles.some(p => p.id === entry.managedWebsiteSession) ?
                  <option value={entry.managedWebsiteSession}>{entry.managedWebsiteSession} (unavailable; retained)</option> : null}
                {profiles.map(profile => {
                  let sameOrigin = false;
                  try { sameOrigin = new URL(entry.baseUrl).origin === profile.profile.browser_origin; } catch { /* incomplete URL */ }
                  return <option key={profile.id} value={profile.id} disabled={!profile.profile.enabled || !sameOrigin}>
                    {profile.id}{!profile.profile.enabled ? " (disabled)" : !sameOrigin ? " (different website origin)" : ""}
                  </option>;
                })}
              </select>
              <span className="field-description">Profiles are managed in Configuration → Access, approvals &amp; audit → Website logins. The website origin must match this target.</span>
            </label>
            {relayPath ? (
              <div className="field-description">
                Agent relay path: <code>{relayPath}</code>
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
