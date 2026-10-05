import { useEffect, useRef, useState } from "react";
import { fetchJson } from "../admin/api";
import type { WorkflowInternalEventCatalogEntry } from "../../types";

type Definition = { label: string; description?: string | null; payload_schema: Record<string, unknown> };
type Snapshot = { revision: string; config: { events?: Record<string, Definition> }; events: WorkflowInternalEventCatalogEntry[]; inherited_events: WorkflowInternalEventCatalogEntry[] };
const owns = (value: object, key: string) => Object.prototype.hasOwnProperty.call(value, key);

export function EventsConfiguration({ directoryPath, disabled, onDirtyChange }: {
  directoryPath: string; disabled: boolean; onDirtyChange: (dirty: boolean) => void;
}) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [definitions, setDefinitions] = useState<Record<string, Definition>>({});
  const [schemaTexts, setSchemaTexts] = useState<Record<string, string>>({});
  const [invalid, setInvalid] = useState<Record<string, boolean>>({});
  const [selected, setSelected] = useState("");
  const [newType, setNewType] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const url = `/api/admin/event-schemas?directory_path=${encodeURIComponent(directoryPath)}`;
  const currentUrl = useRef(url);
  currentUrl.current = url;
  const dirty = Boolean(snapshot && (Object.values(invalid).some(Boolean) || JSON.stringify(definitions) !== JSON.stringify(snapshot.config.events ?? {})));
  useEffect(() => { onDirtyChange(dirty); }, [dirty, onDirtyChange]);
  function adopt(data: Snapshot) {
    setSnapshot(data); setDefinitions(data.config.events ?? {}); setSchemaTexts({}); setInvalid({});
  }
  useEffect(() => {
    let active = true;
    setSnapshot(null); setDefinitions({}); setSchemaTexts({}); setInvalid({});
    setSelected(""); setError(""); setNotice("");
    fetchJson<Snapshot>(url).then((data) => { if (active) adopt(data); }).catch((cause) => { if (active) setError(String(cause)); });
    return () => { active = false; };
  }, [url]);
  const effective: Record<string, WorkflowInternalEventCatalogEntry> = Object.fromEntries([
    ...(snapshot?.inherited_events ?? []).map((entry) => [entry.event_type, entry]),
    ...Object.entries(definitions).map(([eventType, definition]) => [eventType, { event_type: eventType, ...definition, source: directoryPath }]),
  ]);
  const entry = owns(effective, selected) ? effective[selected] : undefined;
  const local = owns(definitions, selected);
  const selectedInvalid = owns(invalid, selected) && invalid[selected];
  const locked = disabled || busy;
  function override(eventType: string) {
    const inherited = owns(effective, eventType) ? effective[eventType] : undefined;
    setDefinitions((current) => ({ ...current, [eventType]: { label: inherited?.label ?? eventType, description: inherited?.description, payload_schema: inherited?.payload_schema ?? { type: "object", properties: {} } } }));
    setSelected(eventType); setNotice("");
  }
  function patch(change: Partial<Definition>) { setDefinitions((current) => ({ ...current, [selected]: { ...current[selected], ...change } })); setNotice(""); }
  async function save() {
    if (!snapshot) return;
    setBusy(true); setError("");
    try {
      const data = await fetchJson<Snapshot>(url, { method: "PUT", body: JSON.stringify({ expected_revision: snapshot.revision, config: { events: definitions } }) });
      if (currentUrl.current === url) { adopt(data); setNotice("Event definitions saved."); }
    }
    catch (cause) { if (currentUrl.current === url) setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setBusy(false); }
  }
  async function reload() {
    if (dirty && !window.confirm("Discard unsaved event definitions and reload?")) return;
    setBusy(true); setError("");
    try { const data = await fetchJson<Snapshot>(url); if (currentUrl.current === url) { adopt(data); setNotice(""); } }
    catch (cause) { if (currentUrl.current === url) setError(String(cause)); }
    finally { setBusy(false); }
  }
  return <article className="panel configuration-utility-panel">
    <div className="panel-header"><h3>Events</h3><span>{directoryPath}</span></div>
    <p>Define event payloads once for manual trigger forms and workflow validation. Local definitions replace inherited definitions in full.</p>
    {error ? <p role="alert" className="error-text">{error}</p> : null}
    {notice ? <p role="status">{notice}</p> : null}
    {!snapshot ? <p>Loading event definitions…</p> : <>
      <div className="workflow-pack-form-actions">
        <button type="button" disabled={locked || !dirty || Object.values(invalid).some(Boolean)} onClick={() => void save()}>Save events</button>
        <button type="button" disabled={busy} onClick={() => void reload()}>Reload</button>
      </div>
      <label className="inspector-label"><span>Event</span><select aria-label="Event" value={selected} disabled={busy} onChange={(event) => setSelected(event.target.value)}><option value="">Select an event</option>{Object.values(effective).sort((a, b) => a.event_type.localeCompare(b.event_type)).map((item) => <option key={item.event_type} value={item.event_type}>{item.label} ({item.event_type})</option>)}</select></label>
      <div className="workflow-pack-form-actions"><label className="inspector-label"><span>New event type</span><input aria-label="New event type" value={newType} disabled={locked} placeholder="custom.answer" onChange={(event) => setNewType(event.target.value)} /></label><button type="button" disabled={locked || !newType.trim() || owns(effective, newType.trim())} onClick={() => { override(newType.trim()); setNewType(""); }}>Create event</button></div>
      {entry ? <>
        <p>Definition source: {entry.source ?? "Platform"}. {local ? "Local definition." : "Inherited definition."}</p>
        {!local ? <button type="button" disabled={locked} onClick={() => override(selected)}>Override in this directory</button> : <button type="button" disabled={locked} onClick={() => {
          setDefinitions((current) => { const next = { ...current }; delete next[selected]; return next; });
          setInvalid((current) => { const next = { ...current }; delete next[selected]; return next; });
          setSchemaTexts((current) => { const next = { ...current }; delete next[selected]; return next; });
          setNotice("Local definition removed from draft. Inherited definition will apply after saving.");
        }}>Remove local definition</button>}
        <fieldset disabled={locked || !local}>
          <label className="inspector-label"><span>Event label</span><input aria-label="Event label" value={entry.label} onChange={(event) => patch({ label: event.target.value })} /></label>
          <label className="inspector-label"><span>Description</span><textarea aria-label="Description" value={entry.description ?? ""} onChange={(event) => patch({ description: event.target.value })} /></label>
          <label className="inspector-label"><span>Payload JSON Schema</span><textarea aria-label="Payload JSON Schema" rows={18} spellCheck={false} value={owns(schemaTexts, selected) ? schemaTexts[selected] : JSON.stringify(entry.payload_schema ?? { type: "object" }, null, 2)} aria-invalid={selectedInvalid} onChange={(event) => {
            const text = event.target.value; setSchemaTexts((current) => ({ ...current, [selected]: text }));
            try {
              const schema: unknown = JSON.parse(text);
              if (!schema || typeof schema !== "object" || Array.isArray(schema)) throw new Error("Object required");
              patch({ payload_schema: schema as Record<string, unknown> }); setInvalid((current) => ({ ...current, [selected]: false }));
            } catch { setInvalid((current) => ({ ...current, [selected]: true })); }
          }} /></label>
          {selectedInvalid ? <p role="alert">Enter a valid JSON Schema object.</p> : null}
          <p className="field-description">Draft 2020-12, with type: object. References must stay within this document. Schemas are checked before saving.</p>
        </fieldset>
      </> : null}
    </>}
  </article>;
}
