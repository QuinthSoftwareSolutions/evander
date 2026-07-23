import { useEffect } from "react";

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
  legacyFallback?: boolean;
  sourceError?: string | null;
  sessionId?: string | null;
};

export function HttpTargetsEditor({
  entries = [],
  onChange,
  onValidationChange,
  disabled = false,
  legacyFallback = false,
  sourceError = null,
  sessionId = null,
}: HttpTargetsEditorProps) {
  const validationError = httpTargetsValidationError(entries, sourceError);

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
        separate from Web search, and the agent supplies the target application credentials.
        Existing workflow metadata is preserved while an alias remains unchanged.
      </p>
      {legacyFallback ? (
        <div className="callout">
          These targets come from legacy APP environment metadata. Saving a change converts them
          to canonical HTTP targets.
        </div>
      ) : null}
      {validationError ? <div className="callout error-callout">{validationError}</div> : null}
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
