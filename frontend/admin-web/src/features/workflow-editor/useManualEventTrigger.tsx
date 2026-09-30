import { useEffect, useRef, useState, type FormEvent } from "react";
import { type WorkflowEditorDocument, type WorkflowNodeConfig } from "../../types";
import { fetchJson } from "./editorApi";

type QueuedRun = { event_id: string; trace_id: string };

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
  }, [contextKey, configuredCorrelationKey]);

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
  try {
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
    if (submittingRef.current || blockedReason || !payload || !node || !savedDocument?.revision) return;
    submittingRef.current = true;
    setSubmitting(true);
    setError(null);
    setQueued(null);
    try {
      const body = {
        workflow_pack: pack,
        expected_revision: savedDocument.revision,
        payload,
        correlation_key: configuredCorrelationKey || correlationKey.trim() || null,
      };
      const fingerprint = JSON.stringify(body);
      const storageKey = `evander:manual-event-trigger:${JSON.stringify([userId, pack, node.id])}`;
      const storedText = sessionStorage.getItem(storageKey);
      let stored: { fingerprint?: string; requestId?: string } | null = null;
      try { stored = storedText ? JSON.parse(storedText) : null; } catch { /* Replace an invalid retry entry. */ }
      const requestId = stored?.fingerprint === fingerprint && typeof stored.requestId === "string"
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
      if (contextRef.current === contextKey) setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  }

  const button = (
    <div className="manual-event-trigger-action">
      <button type="button" disabled={Boolean(blockedReason) || submitting} onClick={() => setOpen(true)}>
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
          <p className="field-description">Starts only this node. Enter the event payload below; event metadata is supplied automatically.</p>
          <label className="inspector-label">
            <span>Event payload JSON</span>
            <textarea autoFocus rows={10} value={payloadText} disabled={submitting} aria-invalid={Boolean(payloadError)} onChange={(event) => {
              setPayloadText(event.target.value); setError(null); setQueued(null);
            }} />
          </label>
          {payloadError ? <div className="runtime-run-error" role="alert">{payloadError}</div> : null}
          <label className="inspector-label">
            <span>Correlation key (optional)</span>
            <input value={configuredCorrelationKey || correlationKey} readOnly={Boolean(configuredCorrelationKey)} disabled={submitting} onChange={(event) => {
              setCorrelationKey(event.target.value); setError(null); setQueued(null);
            }} />
          </label>
          {blockedReason ? <p className="field-description">{blockedReason}</p> : null}
          {error ? <div className="runtime-run-error" role="alert">{error}</div> : null}
          {queued ? <div role="status">
            <strong>Run queued</strong>
            {canReadRuns ? <button type="button" onClick={() => { setOpen(false); inspectRun(queued.event_id); }}>Inspect run</button> : null}
          </div> : null}
          <div className="workflow-pack-form-actions">
            <button type="submit" className="primary" disabled={Boolean(blockedReason || payloadError) || submitting}>
              {submitting ? "Queuing…" : queued ? "Trigger again" : "Trigger node"}
            </button>
          </div>
        </form>
      </div>
    </div>
  ) : null;
  return { button, dialog };
}
