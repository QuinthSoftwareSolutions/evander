import { useEffect, useRef, useState, type FormEvent } from "react";
import { type WorkflowEditorDocument, type WorkflowNodeConfig } from "../../types";
import { fetchJson } from "./editorApi";
import { ApiError } from "../admin/api";
import { EventPayloadForm, initialEventValue, type EventFailure, type EventSchema } from "./EventPayloadForm";

type QueuedRun = { event_id: string; trace_id: string };

function requestFingerprint(body: { workflow_pack?: string | null; payload: unknown; correlation_key?: string | null }) {
  return JSON.stringify({
    workflow_pack: body.workflow_pack ?? null,
    payload: body.payload,
    correlation_key: body.correlation_key ?? null,
  }, (_key, value: unknown) => value && typeof value === "object" && !Array.isArray(value)
    ? Object.fromEntries(Object.entries(value).sort(([left], [right]) => left < right ? -1 : left > right ? 1 : 0)) : value);
}

type Inputs = {
  node: WorkflowNodeConfig | undefined;
  savedDocument: WorkflowEditorDocument | null;
  hasUnsavedChanges: boolean;
  canControl: boolean;
  canReadRuns: boolean;
  userId: string;
  unavailableReason: string | null;
  inspectRun: (eventId: string) => void;
};

export function useManualEventTrigger({
  node, savedDocument, hasUnsavedChanges, canControl, canReadRuns, userId,
  unavailableReason, inspectRun,
}: Inputs) {
  const [open, setOpen] = useState(false);
  const [payloadText, setPayloadText] = useState("{}");
  const [definition, setDefinition] = useState<{ payload_schema?: EventSchema | null; schema_revision: string } | null>(null);
  const [schemaLoading, setSchemaLoading] = useState(false);
  const [schemaError, setSchemaError] = useState<string | null>(null);
  const [schemaRefresh, setSchemaRefresh] = useState(0);
  const [checking, setChecking] = useState(false);
  const [validated, setValidated] = useState(false);
  const [formValue, setFormValue] = useState<Record<string, unknown>>({});
  const [fieldFailures, setFieldFailures] = useState<EventFailure[]>([]);
  const [invalidFields, setInvalidFields] = useState<Record<string, string>>({});
  const [correlationKey, setCorrelationKey] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const submittingRef = useRef(false);
  const [error, setError] = useState<string | null>(null);
  const [queued, setQueued] = useState<QueuedRun | null>(null);
  const savedNode = savedDocument?.config.triggers.find((item) => item.id === node?.id);
  const configuredCorrelationKey = String(savedNode?.config.correlation_key ?? "");
  const eventType = String(savedNode?.config.event_type ?? "");
  const pack = savedDocument?.selected_workflow_pack?.reference ?? null;
  const contextKey = JSON.stringify([node?.id, pack, savedDocument?.revision]);
  const contextRef = useRef(contextKey);
  contextRef.current = contextKey;

  useEffect(() => {
    setOpen(false);
    setPayloadText("{}");
    setCorrelationKey(configuredCorrelationKey);
    setError(null);
    setQueued(null);
    setDefinition(null);
    setFieldFailures([]);
    setInvalidFields({});
    setValidated(false);
  }, [contextKey, configuredCorrelationKey]);

  useEffect(() => {
    if (!open || !node || !canControl) return;
    let active = true;
    setSchemaLoading(true); setSchemaError(null); setValidated(false);
    const query = pack ? `?workflow_pack=${encodeURIComponent(pack)}` : "";
    fetchJson<{ payload_schema?: EventSchema | null; schema_revision: string }>(`/api/workflow-editor/nodes/${encodeURIComponent(node.id)}/event-schema${query}`)
      .then((result) => {
        if (!active) return;
        if (typeof result.schema_revision !== "string") throw new Error("Unable to load the event definition.");
        setDefinition(result);
        if (result.payload_schema && !definition?.payload_schema) {
          setFormValue(initialEventValue(result.payload_schema) as Record<string, unknown> ?? {});
          setInvalidFields({});
        }
        if (!result.payload_schema && definition?.payload_schema) {
          setPayloadText(JSON.stringify(formValue, null, 2));
          setInvalidFields({});
        }
        setFieldFailures([]);
      }).catch((cause) => { if (active) setSchemaError(cause instanceof Error ? cause.message : String(cause)); })
      .finally(() => { if (active) setSchemaLoading(false); });
    return () => { active = false; };
  }, [open, contextKey, canControl, schemaRefresh]);

  if (node?.type !== "trigger:event") return { button: null, dialog: null };

  const blockedReason = !canControl
    ? "Workflow run-control permission is required."
    : unavailableReason
      ?? (hasUnsavedChanges ? "Save or discard workflow changes before triggering this node." : null)
      ?? (!savedDocument?.config_exists || !savedDocument.revision || !savedNode
        ? "Save the workflow before triggering this node." : null)
      ?? (savedNode?.config.enabled === false ? "Enable and save this trigger before running it." : null);

  let payload: Record<string, unknown> | null = null;
  let payloadError: string | null = null;
  if (definition?.payload_schema) {
    payload = formValue;
    payloadError = Object.values(invalidFields)[0] ?? null;
  } else try {
    const parsed: unknown = JSON.parse(payloadText);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      payloadError = "Event payload must be a JSON object.";
    } else {
      payload = parsed as Record<string, unknown>;
    }
  } catch {
    payloadError = "Enter valid JSON for the event payload.";
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (submittingRef.current || checking || schemaLoading || schemaError || !definition || blockedReason || payloadError || !payload || !node || !savedDocument?.revision) return;
    submittingRef.current = true;
    setSubmitting(true);
    setError(null);
    setQueued(null);
    setValidated(false);
    try {
      const body = {
        workflow_pack: pack,
        expected_revision: savedDocument.revision,
        payload,
        correlation_key: configuredCorrelationKey || correlationKey.trim() || null,
        schema_revision: definition.schema_revision,
      };
      const fingerprint = requestFingerprint(body);
      const storageKey = `evander:manual-event-trigger:${JSON.stringify([userId, pack, node.id])}`;
      const storedText = sessionStorage.getItem(storageKey);
      let stored: { fingerprint?: string; requestId?: string } | null = null;
      try { stored = storedText ? JSON.parse(storedText) : null; } catch { /* Replace an invalid retry entry. */ }
      let storedFingerprint: string | null = null;
      try {
        // Existing retry entries included expected_revision; normalize them without losing the ID.
        if (typeof stored?.fingerprint === "string") storedFingerprint = requestFingerprint(JSON.parse(stored.fingerprint));
      } catch { /* Replace an invalid retry fingerprint. */ }
      const requestId = storedFingerprint === fingerprint && typeof stored?.requestId === "string"
        ? stored.requestId : Array.from(crypto.getRandomValues(new Uint8Array(16)), (byte) => byte.toString(16).padStart(2, "0")).join("");
      // Persist before sending so a lost response or reload can safely retry the same submission.
      sessionStorage.setItem(storageKey, JSON.stringify({ fingerprint, requestId }));
      const result = await fetchJson<QueuedRun>(`/api/workflow-editor/nodes/${encodeURIComponent(node.id)}/trigger`, {
        method: "POST",
        body: JSON.stringify({ ...body, request_id: requestId }),
      });
      if (contextRef.current === contextKey) setQueued(result);
      sessionStorage.removeItem(storageKey);
    } catch (cause) {
      if (contextRef.current === contextKey) {
        setError(cause instanceof Error ? cause.message : String(cause));
        if (cause instanceof ApiError) {
          const details = (cause.payload as { details?: { failures?: EventFailure[] } })?.details;
          setFieldFailures(details?.failures ?? []);
        }
      }
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  }

  async function checkPayload() {
    if (!node || !definition || !payload || payloadError || checking || submitting) return;
    setChecking(true); setError(null); setValidated(false);
    try {
      const result = await fetchJson<{ valid: boolean; failures: EventFailure[] }>(`/api/workflow-editor/nodes/${encodeURIComponent(node.id)}/validate-event`, {
        method: "POST", body: JSON.stringify({ workflow_pack: pack, schema_revision: definition.schema_revision, payload }),
      });
      if (contextRef.current === contextKey) {
        setFieldFailures(result.failures);
        setValidated(result.valid);
        setError(result.valid ? null : "Check the highlighted fields.");
      }
    } catch (cause) { if (contextRef.current === contextKey) setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setChecking(false); }
  }

  const button = (
    <div className="manual-event-trigger-action">
      <button type="button" disabled={Boolean(blockedReason) || submitting} onClick={() => {
        setInvalidFields({}); setValidated(false); setOpen(true);
      }}>
        Trigger manually
      </button>
      {blockedReason ? <p className="field-description">{blockedReason}</p> : null}
    </div>
  );
  const dialog = open ? (
    <div className="modal-backdrop" role="presentation" onClick={(event) => {
      if (event.target === event.currentTarget && !submitting) setOpen(false);
    }} onKeyDown={(event) => {
      if (event.key === "Escape" && !submitting) setOpen(false);
    }}>
      <div className="modal-card workflow-pack-modal" role="dialog" aria-modal="true" aria-label="Trigger event manually">
        <div className="modal-header">
          <div><h2>Trigger event manually</h2><p>{savedNode?.display_name || node.id}</p></div>
          <button type="button" onClick={() => setOpen(false)} disabled={submitting}>Close</button>
        </div>
        <form className="modal-body workflow-pack-form" onSubmit={(event) => void submit(event)}>
          <label className="inspector-label"><span>Event type</span><input value={eventType} readOnly /></label>
          <p className="field-description">Starts only this node. Event metadata is supplied automatically.</p>
          {schemaLoading ? <p role="status">Loading event definition…</p> : null}
          {schemaError ? <p role="alert" className="runtime-run-error">{schemaError}</p> : null}
          {definition?.payload_schema ? <EventPayloadForm schema={definition.payload_schema} value={formValue} disabled={submitting || checking || schemaLoading} failures={fieldFailures} onChange={(value) => {
            setFormValue(value); setError(null); setQueued(null); setFieldFailures([]); setValidated(false);
          }} onInvalid={(path, message, clearChildren) => {
            setValidated(false);
            setInvalidFields((current) => {
              const next = Object.fromEntries(Object.entries(current).filter(([key]) => key !== path && (!clearChildren || !key.startsWith(`${path}/`))));
              if (message) next[path] = message;
              return next;
            });
          }} /> : definition ? <>
          <p className="field-description">No schema registered. Enter the event payload as JSON.</p>
          <label className="inspector-label">
            <span>Event payload JSON</span>
            <textarea autoFocus rows={10} value={payloadText} disabled={submitting} aria-invalid={Boolean(payloadError)} onChange={(event) => {
              setPayloadText(event.target.value); setError(null); setQueued(null);
            }} />
          </label>
          </> : null}
          {payloadError ? <div className="runtime-run-error" role="alert">{payloadError}</div> : null}
          <label className="inspector-label">
            <span>Correlation key (optional)</span>
            <input value={configuredCorrelationKey || correlationKey} readOnly={Boolean(configuredCorrelationKey)} disabled={submitting} onChange={(event) => {
              setCorrelationKey(event.target.value); setError(null); setQueued(null);
            }} />
          </label>
          {blockedReason ? <p className="field-description">{blockedReason}</p> : null}
          {error ? <div className="runtime-run-error" role="alert">{error}</div> : null}
          {fieldFailures.length ? <ul>{fieldFailures.map((failure, index) => <li key={index}>{failure.path || "Event payload"}: {failure.message}</li>)}</ul> : null}
          {validated ? <p role="status">Payload matches the event schema.</p> : null}
          {queued ? <div role="status">
            <strong>Run queued</strong>
            {canReadRuns ? <button type="button" onClick={() => { setOpen(false); inspectRun(queued.event_id); }}>Inspect run</button> : null}
          </div> : null}
          <div className="workflow-pack-form-actions">
            <button type="button" disabled={submitting || checking || schemaLoading} onClick={() => setSchemaRefresh((value) => value + 1)}>Reload event definition</button>
            {definition?.payload_schema ? <button type="button" disabled={Boolean(payloadError || blockedReason) || schemaLoading || submitting || checking} onClick={() => void checkPayload()}>{checking ? "Checking…" : "Validate payload"}</button> : null}
            <button type="submit" className="primary" disabled={Boolean(blockedReason || payloadError || schemaError) || !definition || schemaLoading || submitting || checking}>
              {submitting ? "Queuing…" : queued ? "Trigger again" : "Trigger node"}
            </button>
          </div>
        </form>
      </div>
    </div>
  ) : null;
  return { button, dialog };
}
