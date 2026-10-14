import { useEffect, useRef, useState, type FormEvent } from "react";
import { ArrowRight, CheckCircle2, Clock3, MessageSquare, Sparkles } from "lucide-react";
import { adminHash, hashQueryParam } from "../../adminRouting";
import { fetchJson } from "../admin/api";
import { useAdminUi } from "../admin/AdminContext";
import "./WorkflowStarters.css";

type Field = { type?: string; title?: string; description?: string; default?: string | number | boolean; enum?: string[]; format?: string; pattern?: string; minLength?: number; maxLength?: number };
type Starter = { id: string; label: string; description: string; provider: string; input_schema: { properties: Record<string, Field>; required?: string[] } };
type Verification = { instructions: string; manual_trigger_node_id: string | null };
type Instance = { id: string; starter_id: string; workflow_pack: string; created_at: string; verification: Verification | null };
type Catalog = { starters: Starter[]; agents: { value: string; label: string }[]; instances: Instance[] };
type Preview = { ready: boolean; preview_revision: string; workflow_pack: string; checks: { id: string; label: string; status: string; code?: string }[]; pack?: { graphs: { directory_path: string }[]; ir: { nodes: { node: { id: string; display_name: string; type: string; config: Record<string, unknown> } }[]; edges: unknown[] } }; verification?: Verification };
type Status = { state: string; message: string; event_id: string | null };
const endpoint = "/api/admin/workflow-starters";

export default function WorkflowStartersPage() {
  const { selectedDirectoryPath } = useAdminUi();
  return <StarterWorkspace key={selectedDirectoryPath} directory={selectedDirectoryPath} />;
}

function StarterWorkspace({ directory }: { directory: string }) {
  const { privilegedUnlocked, canWriteWorkflows, canManageWorkflowPacks, canReadWorkflowRuns, canControlWorkflowRuns } = useAdminUi();
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [selected, setSelected] = useState<Starter | null>(null);
  const [values, setValues] = useState<Record<string, string | number | boolean>>({});
  const [preview, setPreview] = useState<Preview | null>(null);
  const [instance, setInstance] = useState<Instance | null>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [eventId, setEventId] = useState<string | null>(null);
  const [verificationCheck, setVerificationCheck] = useState(0);
  const testRequest = useRef<string | null>(null);
  const sequence = useRef(0);
  const directoryQuery = new URLSearchParams({ directory_path: directory }).toString();
  const canActivate = privilegedUnlocked && canWriteWorkflows && canManageWorkflowPacks;

  useEffect(() => {
    let disposed = false;
    fetchJson<Catalog>(`${endpoint}?${directoryQuery}`).then((data) => {
      if (disposed) return;
      setCatalog(data);
      const wanted = hashQueryParam(window.location.hash, "instance");
      setInstance(data.instances.find((item) => item.id === wanted) ?? null);
    }).catch((reason: Error) => { if (!disposed) setError(reason.message); });
    return () => { disposed = true; sequence.current += 1; };
  }, [directoryQuery]);

  useEffect(() => {
    if (!instance || !canReadWorkflowRuns) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function check() {
      try {
        const data = await fetchJson<Status>(`${endpoint}/${instance!.id}/verify?${directoryQuery}${eventId ? `&event_id=${encodeURIComponent(eventId)}` : ""}`, { signal: controller.signal });
        if (controller.signal.aborted) return;
        setStatus(data);
        if (["waiting", "running"].includes(data.state)) timer = setTimeout(check, 4000);
      } catch (reason) { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason)); }
    }
    void check();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [instance, eventId, directoryQuery, canReadWorkflowRuns, verificationCheck]);

  function choose(starter: Starter) {
    sequence.current += 1;
    setBusy(false); setSelected(starter); setInstance(null); setPreview(null); setStatus(null); setError(null); setEventId(null);
    testRequest.current = null;
    setValues(Object.fromEntries(Object.entries(starter.input_schema.properties).map(([key, field]) => [key, field.default ?? ""])));
  }

  async function review(event: FormEvent) {
    event.preventDefault();
    if (!selected) return;
    const request = ++sequence.current;
    setBusy(true); setError(null); setPreview(null);
    try {
      const result = await fetchJson<Preview>(`${endpoint}/preview`, { method: "POST", body: JSON.stringify({ starter_id: selected.id, directory_path: directory, values }) });
      if (request === sequence.current) setPreview(result);
    } catch (reason) { if (request === sequence.current) setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { if (request === sequence.current) setBusy(false); }
  }

  async function activate() {
    if (!preview?.ready || !selected || !canActivate) return;
    const request = ++sequence.current;
    setBusy(true); setError(null);
    try {
      const result = await fetchJson<Instance>(`${endpoint}/activate`, { method: "POST", body: JSON.stringify({ starter_id: selected.id, directory_path: directory, values, preview_revision: preview.preview_revision }) });
      if (request !== sequence.current) return;
      setInstance(result); setCatalog((current) => current && ({ ...current, instances: [...current.instances.filter((item) => item.id !== result.id), result] }));
      window.history.replaceState(null, "", adminHash("starters", { instance: result.id }));
    } catch (reason) { if (request === sequence.current) setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { if (request === sequence.current) setBusy(false); }
  }

  async function runTest() {
    if (!instance) return;
    const request = ++sequence.current;
    setBusy(true); setError(null);
    testRequest.current ??= crypto.randomUUID();
    try {
      const result = await fetchJson<{ event_id: string }>(`${endpoint}/${instance.id}/test`, { method: "POST", body: JSON.stringify({ directory_path: directory, request_id: testRequest.current }) });
      if (request !== sequence.current) return;
      setEventId(result.event_id);
      setStatus({ state: "running", event_id: result.event_id, message: "Test queued. Waiting for the agent to finish." });
    } catch (reason) { if (request === sequence.current) setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { if (request === sequence.current) setBusy(false); }
  }

  const step = instance ? 3 : preview?.ready ? 2 : selected ? 1 : 0;
  return <div className="workflow-starters">
    <header className="starters-header">
      <div><p className="starters-eyebrow"><Sparkles size={16} /> WORKFLOW STARTERS</p><h1>Start with an outcome.</h1><p>Choose a common workflow, connect what it needs, and make it your own.</p></div>
      <a className="starters-editor-link" href={adminHash("workflow")}>Open workflow editor <ArrowRight size={16} /></a>
    </header>
    <ol className="starters-steps" aria-label="Setup progress">{["Choose", "Configure", "Preview", "Activate & verify"].map((label, index) => <li key={label} aria-current={step === index ? "step" : undefined} className={step >= index ? "is-current" : ""}><span>{index + 1}</span>{label}</li>)}</ol>
    {error && <div role="alert" className="starters-notice error">{error}</div>}
    {!catalog && !error && <p role="status">Loading starters…</p>}
    {catalog && !instance && <>
      <div className="starters-catalog">{catalog.starters.map((starter) => <button key={starter.id} type="button" className={`starter-card ${selected?.id === starter.id ? "selected" : ""}`} aria-pressed={selected?.id === starter.id} onClick={() => choose(starter)}>
        {starter.id === "core/scheduled-task" ? <Clock3 size={24} /> : <MessageSquare size={24} />}<small>{starter.provider}</small><h2>{starter.label}</h2><p>{starter.description}</p><span>Configure <ArrowRight size={15} /></span>
      </button>)}</div>
      <p className="starters-caption">More starters come from enabled <a href={adminHash("addons")}>add-ons</a>. All workflows remain editable in the full editor.</p>
      {!catalog.agents.length && <div className="starters-notice"><strong>First, create an agent.</strong> Starters use an existing agent in {directory}. <a href={adminHash("agents")}>Open Agents to create one <ArrowRight size={14} /></a></div>}
      {selected && <div className="starters-workspace">
        <form onSubmit={review} className="starters-panel">
          <p className="starters-eyebrow">CONFIGURE · {directory}</p><h2>{selected.label}</h2>
          {Object.entries(selected.input_schema.properties).map(([key, field]) => <label key={key} className="starters-field">
            <span>{field.title ?? key}{!selected.input_schema.required?.includes(key) && " (optional)"}</span>
            {field.format === "evander-session" || field.enum ? <select aria-label={field.title ?? key} value={String(values[key] ?? "")} required={selected.input_schema.required?.includes(key)} onChange={(event) => { sequence.current++; setBusy(false); setValues({ ...values, [key]: event.target.value }); setPreview(null); }}>
              {!field.enum && <option value="">Choose an agent…</option>}
              {(field.enum?.map((value) => ({ value, label: value })) ?? catalog.agents).map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select> : field.format === "textarea" ? <textarea aria-label={field.title ?? key} rows={5} value={String(values[key] ?? "")} required={selected.input_schema.required?.includes(key)} minLength={field.minLength} maxLength={field.maxLength} onChange={(event) => { sequence.current++; setBusy(false); setValues({ ...values, [key]: event.target.value }); setPreview(null); }} /> :
              <input aria-label={field.title ?? key} value={String(values[key] ?? "")} required={selected.input_schema.required?.includes(key)} pattern={field.pattern} minLength={field.minLength} maxLength={field.maxLength} onChange={(event) => { sequence.current++; setBusy(false); setValues({ ...values, [key]: event.target.value }); setPreview(null); }} />}
            {field.description && <small>{field.description}</small>}
          </label>)}
          <button type="submit" disabled={busy || !canWriteWorkflows || !canManageWorkflowPacks || !catalog.agents.length}>{busy ? "Checking…" : "Check & preview"}<ArrowRight size={16} /></button>
        </form>
        <section className="starters-panel starters-preview" aria-label="Workflow preview">
          <p className="starters-eyebrow">PREVIEW</p><h2>{preview ? preview.ready ? "Ready to activate" : "Setup needed" : "See what will run"}</h2>
          {!preview && <p>Check your settings to see the workflow and its prerequisites before activating it.</p>}
          {preview && <><ul className="starters-checks">{preview.checks.map((check) => <li key={check.id}><span className={`starter-check ${check.status}`}>{check.status === "ready" ? "✓" : "!"}</span><span>{check.label}<small>{check.status === "ready" ? "Ready" : (check.code ?? check.status).replace(/_/g, " ")}</small></span></li>)}</ul>
            {!preview.ready && <p>Configure the integration in <a href={adminHash("configuration")}>Configuration</a>, then check again. <a href={adminHash("addons")}>Manage add-ons</a>.</p>}
            {preview.pack && <><p><strong>{preview.workflow_pack}</strong><br />Directory: {directory}</p><ol className="starters-flow">{preview.pack.ir.nodes.map(({ node }) => <li key={node.id}>{node.display_name}<small>{node.type}{node.config.expression ? ` · ${node.config.expression} (${node.config.timezone})` : ""}</small></li>)}</ol>
              <p>{preview.verification?.instructions}</p><details><summary>Inspect generated workflow</summary><pre>{JSON.stringify(preview.pack, null, 2)}</pre></details>
              <p>Activation enables this workflow. Existing workflows and agent permissions stay as configured.</p>
              {!canActivate && <p className="starters-notice">Activation requires workflow pack management permission and unlocked privileged operations.</p>}
              <button type="button" onClick={() => void activate()} disabled={busy || !canActivate}>Activate workflow <ArrowRight size={16} /></button>
            </>}
          </>}
        </section>
      </div>}
    </>}
    {instance && <section className="starters-panel starters-activated" aria-live="polite">
      <CheckCircle2 size={34} /><p className="starters-eyebrow">WORKFLOW ACTIVATED</p><h2>{instance.workflow_pack}</h2><p>{instance.verification?.instructions ?? "Open the run report to check the workflow's result."}</p>
      <div className={`starters-notice ${status?.state === "verified" ? "success" : ""}`} role="status"><strong>{status?.state === "verified" ? "Verified" : "Verification"}</strong><p>{status?.message ?? (canReadWorkflowRuns ? "Checking for a run…" : "Workflow run read permission is needed to check completion.")}</p></div>
      <div className="starters-actions">{instance.verification?.manual_trigger_node_id && <button type="button" onClick={() => void runTest()} disabled={busy || !!eventId || !privilegedUnlocked || !canControlWorkflowRuns}>Run task once now</button>}
        <button type="button" onClick={() => { setError(null); setVerificationCheck((value) => value + 1); }} disabled={busy || !canReadWorkflowRuns}>Check again</button>
        <a href={adminHash("workflow", { workflow_pack: instance.workflow_pack, event_id: status?.event_id })}>Open workflow {status?.event_id ? "& run" : "editor"} <ArrowRight size={16} /></a>
        <button type="button" onClick={() => { sequence.current++; setInstance(null); setSelected(null); setPreview(null); setEventId(null); setStatus(null); testRequest.current = null; window.history.replaceState(null, "", adminHash("starters")); }}>Choose another starter</button>
      </div>
    </section>}
    {!!catalog?.instances.length && !instance && <section className="starters-recent"><h2>Started workflows</h2>{catalog.instances.map((item) => <button type="button" key={item.id} onClick={() => { setInstance(item); setStatus(null); setEventId(null); testRequest.current = null; }}>{item.workflow_pack}<ArrowRight size={16} /></button>)}</section>}
  </div>;
}
