import { useEffect, useRef, useState } from "react";

import { fetchJson } from "../admin/api";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";

type RecordSummary = Record<string, unknown>;
type Dataset = RecordSummary & {
  dataset_id: string;
  label?: string;
  status?: string;
  capabilities?: Record<string, boolean>;
};
type PrivacyState = {
  directory_path: string;
  revision: string;
  permissions?: string[];
  inventory?: Dataset[];
  holds?: RecordSummary[];
  jobs?: RecordSummary[];
  cases?: RecordSummary[];
  incidents?: RecordSummary[];
  followups?: RecordSummary[];
  holds_has_more?: boolean;
  jobs_has_more?: boolean;
  cases_has_more?: boolean;
  incidents_has_more?: boolean;
  followups_has_more?: boolean;
  config?: RecordSummary;
  coverage_complete?: boolean;
  readiness?: { ready: boolean; blockers: unknown[] };
};
type JobDetail = RecordSummary & {
  id: string;
  scope: string;
  revision: string;
  status: string;
  records: RecordSummary[];
};
type EvidenceDetail = RecordSummary & {
  id: string; scope: string; directory_path: string; record_kind: string; revision: string;
};
const EVIDENCE_KINDS: Record<string, { label: string; permission: string }> = {
  case: { label: "Data-subject request", permission: "privacy.cases" },
  hold: { label: "Hold", permission: "privacy.approve" },
  incident: { label: "Incident", permission: "privacy.cases" },
  followup: { label: "External follow-up", permission: "privacy.cases" },
};
type FormField = { name: string; label: string; required?: boolean; multiline?: boolean };
type ActionDefinition = { label: string; permission: string; fields: FormField[]; destructive?: boolean };

const ACTIONS: Record<string, ActionDefinition> = {
  "retention.preview": {
    label: "Preview retention", permission: "privacy.configure",
    fields: [{ name: "dataset", label: "Dataset ID", required: true }],
  },
  "jobs.approve": {
    label: "Approve reviewed job", permission: "privacy.approve",
    fields: [{ name: "job_id", label: "Reviewed job ID", required: true }],
  },
  "jobs.run": {
    label: "Run approved job", permission: "privacy.configure", destructive: true,
    fields: [{ name: "job_id", label: "Approved job ID", required: true }],
  },
  "jobs.cancel": {
    label: "Cancel future job work", permission: "privacy.approve", destructive: true,
    fields: [{ name: "job_id", label: "Job ID to cancel (completed erasure cannot be undone)", required: true }],
  },
  "exports.release": {
    label: "Record independent export review", permission: "privacy.approve",
    fields: [
      { name: "job_id", label: "Export job ID", required: true },
      { name: "review_reference", label: "Independent third-party-content review reference", required: true },
    ],
  },
  "holds.create": {
    label: "Create hold", permission: "privacy.approve",
    fields: [
      { name: "dataset", label: "Dataset ID", required: true },
      { name: "record_ids", label: "Exact record IDs (one per line)", required: true, multiline: true },
      { name: "reason", label: "Reason reference (avoid personal content)", required: true },
      { name: "review_at", label: "Review date (ISO 8601 UTC)", required: true },
    ],
  },
  "holds.release": {
    label: "Release hold", permission: "privacy.approve", destructive: true,
    fields: [{ name: "hold_id", label: "Hold ID", required: true }],
  },
  "cases.create": {
    label: "Create data-subject request", permission: "privacy.cases",
    fields: [
      { name: "request_type", label: "Request type (access, portability, rectification, erasure, restriction, objection)", required: true },
      { name: "controller_ref", label: "Controller reference", required: true },
      { name: "instruction_ref", label: "Controller instruction reference (required before execution)" },
      { name: "subject_keys", label: 'Exact subject identifiers as JSON (for example {"user_id":"123"})', required: true, multiline: true },
      { name: "deadline_at", label: "Approved deadline (ISO 8601 UTC)", required: true },
    ],
  },
  "cases.locate": {
    label: "Locate case records", permission: "privacy.cases",
    fields: [{ name: "case_id", label: "Case ID", required: true }],
  },
  "cases.preview": {
    label: "Preview reviewed case selection", permission: "privacy.cases",
    fields: [
      { name: "case_id", label: "Case ID", required: true },
      { name: "operation", label: "Operation (erase, export, restrict)", required: true },
      { name: "selections", label: 'Reviewed selections as JSON ({"dataset_id":["record_id"]})', required: true, multiline: true },
    ],
  },
  "cases.update": {
    label: "Update case", permission: "privacy.cases",
    fields: [
      { name: "case_id", label: "Case ID", required: true },
      { name: "status", label: "Status (open, awaiting_instruction, fulfilled_locally, closed_with_exceptions)", required: true },
      { name: "instruction_ref", label: "Instruction reference" },
      { name: "verification_reference", label: "Identity verification evidence reference (required before export/erasure preview)" },
      { name: "closure_reference", label: "Controller-approved closure or exception evidence reference" },
    ],
  },
  "followups.create": {
    label: "Record external follow-up", permission: "privacy.cases",
    fields: [
      { name: "case_id", label: "Case ID", required: true },
      { name: "kind", label: "Kind (provider, backup, manual_store, rectification, restriction)", required: true },
      { name: "owner", label: "Responsible owner", required: true },
      { name: "deadline_at", label: "Deadline (ISO 8601 UTC)", required: true },
      { name: "reference", label: "Evidence or ticket reference", required: true },
    ],
  },
  "followups.update": {
    label: "Update external follow-up", permission: "privacy.cases",
    fields: [
      { name: "followup_id", label: "Follow-up ID", required: true },
      { name: "status", label: "Follow-up status (pending, complete, exception)", required: true },
      { name: "evidence_reference", label: "Evidence reference", required: true },
    ],
  },
  "incidents.create": {
    label: "Record incident", permission: "privacy.cases",
    fields: [
      { name: "discovered_at", label: "Discovery time (ISO 8601 UTC)", required: true },
      { name: "controller_ref", label: "Controller reference", required: true },
      { name: "evidence_reference", label: "Evidence reference", required: true },
      { name: "containment_reference", label: "Containment reference" },
      { name: "notification_reference", label: "Notification decision reference" },
    ],
  },
  "incidents.update": {
    label: "Update incident", permission: "privacy.cases",
    fields: [
      { name: "incident_id", label: "Incident ID", required: true },
      { name: "status", label: "Incident status (open, contained, closed)", required: true },
      { name: "containment_reference", label: "Containment reference" },
      { name: "notification_reference", label: "Notification decision reference" },
    ],
  },
  configure: {
    label: "Publish reviewed configuration", permission: "privacy.configure", destructive: true,
    fields: [{ name: "config", label: "Reviewed privacy configuration (JSON)", required: true, multiline: true }],
  },
};

function text(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value) ?? "";
}

function Summaries({ title, rows, hasMore, onInspect, busy }: {
  title: string; rows: RecordSummary[]; hasMore?: boolean; onInspect?: (id: string) => void; busy?: boolean;
}) {
  return <section className="panel">
    <h3>{title}</h3>
    {hasMore ? <p>Additional records exist; this view is limited to 100 summaries. Use a known record ID or narrow the directory scope.</p> : null}
    {rows.length ? rows.map((row, index) => <details key={index}>
      <summary>{text(row.id ?? row.job_id ?? row.hold_id ?? row.case_id ?? row.incident_id ?? row.followup_id ?? index + 1)} · {text(row.status ?? "recorded")}</summary>
      <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(row, null, 2)}</pre>
      {onInspect && typeof row.id === "string" ? <button type="button" disabled={busy}
        onClick={() => onInspect(row.id as string)}>Inspect evidence: {row.id}</button> : null}
    </details>) : <p>No records in this scope.</p>}
  </section>;
}

export function PrivacyPage({ selectedDirectoryPath, privilegedUnlocked }: {
  selectedDirectoryPath: string; privilegedUnlocked: boolean;
}) {
  const [state, setState] = useState<PrivacyState | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [action, setAction] = useState("retention.preview");
  const [values, setValues] = useState<Record<string, string>>({});
  const [result, setResult] = useState<unknown>(null);
  const [reviewedJob, setReviewedJob] = useState<JobDetail | null>(null);
  const [evidenceKind, setEvidenceKind] = useState("case");
  const [evidenceId, setEvidenceId] = useState("");
  const [evidence, setEvidence] = useState<EvidenceDetail | null>(null);
  const activeDirectoryPathRef = useRef(selectedDirectoryPath);
  const requestSequenceRef = useRef(0);
  activeDirectoryPathRef.current = selectedDirectoryPath;

  useEffect(() => {
    const requestedDirectoryPath = selectedDirectoryPath;
    const requestId = ++requestSequenceRef.current;
    const controller = new AbortController();
    setState(null);
    setValues({});
    setResult(null);
    setReviewedJob(null);
    setEvidence(null);
    setEvidenceId("");
    setError("");
    setBusy(false);
    void fetchJson<PrivacyState>(`/api/privacy?${new URLSearchParams({ directory_path: requestedDirectoryPath })}`, {
      signal: controller.signal, cache: "no-store",
    }).then((payload) => {
      if (requestId !== requestSequenceRef.current || activeDirectoryPathRef.current !== requestedDirectoryPath) return;
      if (payload.directory_path !== requestedDirectoryPath) throw new Error("Privacy response scope did not match the selected directory");
      setState(payload);
    }).catch((cause: unknown) => {
      if (!controller.signal.aborted && requestId === requestSequenceRef.current) setError(String(cause));
    });
    return () => { controller.abort(); ++requestSequenceRef.current; };
  }, [selectedDirectoryPath, refreshKey]);

  const definition = ACTIONS[action];
  const permitted = Boolean(state?.permissions?.includes(definition.permission))
    && (action !== "exports.release" || Boolean(state?.permissions?.includes("privacy.export")));
  const reviewExports = state?.permissions?.includes("privacy.export") && state.permissions.includes("privacy.cases")
    ? (state.jobs ?? []).filter((job) => job.scope === selectedDirectoryPath && typeof job.id === "string"
      && job.operation === "export" && ["awaiting_review", "released"].includes(String(job.status))
      && typeof job.expires_at === "string" && Date.parse(job.expires_at) > Date.now())
    : [];
  const reviewedApproval = action !== "jobs.approve" || Boolean(reviewedJob
    && reviewedJob.id === values.job_id?.trim() && reviewedJob.scope === selectedDirectoryPath
    && reviewedJob.status === "preview" && reviewedJob.revision === state?.revision);

  async function inspectEvidence(kind: string, id: string) {
    const baseState = state;
    const recordId = id.trim();
    if (!baseState || !recordId || busy || !baseState.permissions?.includes(EVIDENCE_KINDS[kind]?.permission)) return;
    const requestedDirectoryPath = baseState.directory_path;
    if (requestedDirectoryPath !== activeDirectoryPathRef.current) return;
    const requestId = ++requestSequenceRef.current;
    setEvidenceKind(kind);
    setEvidenceId(recordId);
    setEvidence(null);
    setBusy(true);
    setError("");
    try {
      const detail = await fetchJson<EvidenceDetail>(`/api/privacy/records/${kind}/${encodeURIComponent(recordId)}?${new URLSearchParams({ directory_path: requestedDirectoryPath })}`, { cache: "no-store" });
      if (requestId !== requestSequenceRef.current || activeDirectoryPathRef.current !== requestedDirectoryPath) return;
      if (detail.id !== recordId || detail.record_kind !== kind || detail.scope !== requestedDirectoryPath
        || detail.directory_path !== requestedDirectoryPath) throw new Error("Privacy evidence scope, kind or ID did not match");
      if (detail.revision !== baseState.revision) throw new Error("Privacy state changed; refresh and inspect the current evidence");
      setEvidence(detail);
    } catch (cause: unknown) {
      if (requestId === requestSequenceRef.current) setError(String(cause));
    } finally {
      if (requestId === requestSequenceRef.current) setBusy(false);
    }
  }

  async function inspectJob() {
    const baseState = state;
    const jobId = values.job_id?.trim();
    if (!baseState || !jobId || busy || !baseState.permissions?.includes("privacy.approve")) return;
    const requestedDirectoryPath = baseState.directory_path;
    if (requestedDirectoryPath !== activeDirectoryPathRef.current) return;
    const requestId = ++requestSequenceRef.current;
    setBusy(true);
    setError("");
    setReviewedJob(null);
    try {
      const detail = await fetchJson<JobDetail>(`/api/privacy/jobs/${encodeURIComponent(jobId)}?${new URLSearchParams({ directory_path: requestedDirectoryPath })}`, { cache: "no-store" });
      if (requestId !== requestSequenceRef.current || activeDirectoryPathRef.current !== requestedDirectoryPath) return;
      if (detail.id !== jobId || detail.scope !== requestedDirectoryPath) throw new Error("Privacy job selection scope or ID did not match");
      if (detail.revision !== baseState.revision) throw new Error("Privacy state changed; refresh and inspect the current selection before approval");
      setReviewedJob(detail);
    } catch (cause: unknown) {
      if (requestId === requestSequenceRef.current) setError(String(cause));
    } finally {
      if (requestId === requestSequenceRef.current) setBusy(false);
    }
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const baseState = state;
    if (!baseState || baseState.directory_path !== activeDirectoryPathRef.current || busy || !privilegedUnlocked || !permitted || !reviewedApproval) return;
    if (definition.destructive && !window.confirm(`${definition.label} in ${baseState.directory_path}? Review the exact record selection and current preview first. Completed erasure cannot be undone.`)) return;
    const requestedDirectoryPath = baseState.directory_path;
    const requestId = ++requestSequenceRef.current;
    setBusy(true);
    setError("");
    setResult(null);
    setEvidence(null);
    try {
      const payload: RecordSummary = {};
      for (const field of definition.fields) {
        const value = values[field.name]?.trim();
        if (!value) continue;
        payload[field.name] = ["subject_keys", "selections", "config"].includes(field.name)
          ? JSON.parse(value) : field.name === "record_ids"
            ? value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean) : value;
      }
      if (action === "cases.preview" && payload.operation === "export" && !baseState.permissions?.includes("privacy.export")) {
        throw new Error("The separate privacy.export permission is required for exports");
      }
      const query = new URLSearchParams({ directory_path: requestedDirectoryPath });
      const response = await fetchJson<unknown>(`/api/privacy/actions/${encodeURIComponent(action)}?${query}`, {
        method: "POST", cache: "no-store",
        body: JSON.stringify({ expected_revision: baseState.revision, payload }),
      });
      if (requestId !== requestSequenceRef.current || activeDirectoryPathRef.current !== requestedDirectoryPath) return;
      setResult(response);
      setReviewedJob(null);
      const refreshed = await fetchJson<PrivacyState>(`/api/privacy?${query}`, { cache: "no-store" });
      if (requestId !== requestSequenceRef.current || activeDirectoryPathRef.current !== requestedDirectoryPath) return;
      if (refreshed.directory_path !== requestedDirectoryPath) throw new Error("Privacy response scope mismatch");
      setState(refreshed);
    } catch (cause: unknown) {
      if (requestId === requestSequenceRef.current && activeDirectoryPathRef.current === requestedDirectoryPath) {
        setError(String(cause));
        setReviewedJob(null);
      }
    } finally {
      if (requestId === requestSequenceRef.current) setBusy(false);
    }
  }

  return <div className="admin-page">
    <header className="panel">
      <h2>Privacy readiness</h2>
      <p>Scope: <code>{selectedDirectoryPath}</code>. Coverage is technical evidence, not a compliance certification.</p>
      <p>Unsupported operations and external-provider or backup follow-ups require review. This page does not imply complete erasure, provider enforcement, or automatic legal notification.</p>
      <button type="button" disabled={busy} onClick={() => setRefreshKey((value) => value + 1)}>Refresh</button>
    </header>
    {error ? <div role="alert" className="admin-shell-alert">{error}</div> : null}
    {!state && !error ? <p role="status">Loading privacy coverage…</p> : null}
    {state?.directory_path === selectedDirectoryPath ? <>
      <section className="panel">
        <h3>Readiness: {state.readiness?.ready ? "technical checks satisfied" : "review required"}</h3>
        <p>Adapter coverage: {state.coverage_complete ? "complete for declared scope" : "incomplete — unsupported datasets remain"}.</p>
        <ul>{state.readiness?.blockers?.map((blocker, index) => <li key={index}>{text(blocker)}</li>)}</ul>
        <p>Revision: <code>{state.revision}</code></p>
      </section>
      <details className="panel" open={!state.coverage_complete}>
        <summary>Dataset coverage · {state.inventory?.length ?? 0} datasets</summary>
        <div style={{ overflowX: "auto" }}><table>
          <thead><tr><th>Dataset</th><th>Status</th><th>Supported operations</th></tr></thead>
          <tbody>{state.inventory?.map((dataset) => <tr key={dataset.dataset_id}>
            <td>{dataset.label ?? dataset.dataset_id}<br /><code>{dataset.dataset_id}</code></td>
            <td>{dataset.status ?? "Requires review"}{dataset.limitation ? <p>{text(dataset.limitation)}</p> : null}</td>
            <td>{Object.entries(dataset.capabilities ?? {}).filter(([, enabled]) => enabled).map(([name]) => name).join(", ") || "Manual / unsupported"}</td>
          </tr>)}</tbody>
        </table></div>
      </details>
      <section className="panel">
        <h3>Operator action</h3>
        {!privilegedUnlocked ? <p>Unlock privileged operations before submitting. Authentication and scoped permissions are also required.</p> : null}
        <p>Actions use the displayed revision. A conflict requires refresh and review; nothing is silently retried.</p>
        <form onSubmit={(event) => { void submit(event); }}>
          <label htmlFor="privacy-action">Action</label>
          <select id="privacy-action" value={action} disabled={busy} onChange={(event) => {
            setAction(event.target.value);
            setValues(event.target.value === "configure" ? { config: JSON.stringify(state.config ?? {}, null, 2) } : {});
            setResult(null);
            setReviewedJob(null);
          }}>{Object.entries(ACTIONS).map(([name, entry]) => <option key={name} value={name}>{entry.label}</option>)}</select>
          {definition.fields.map((field) => <div key={field.name}>
            <label>{field.label}{field.multiline
              ? <textarea value={values[field.name] ?? ""} required={field.required} disabled={busy}
                  onChange={(event) => setValues((previous) => ({ ...previous, [field.name]: event.target.value }))} rows={4} />
              : <input value={values[field.name] ?? ""} required={field.required} disabled={busy}
                  onChange={(event) => setValues((previous) => ({ ...previous, [field.name]: event.target.value }))} />}</label>
          </div>)}
          {action === "jobs.approve" ? <>
            <button type="button" disabled={busy || !permitted || !values.job_id?.trim()} onClick={() => { void inspectJob(); }}>Inspect persisted selection</button>
            <p>Inspect this exact job before approving. Record fingerprints, policy revision, and cutoff are checked again at execution. Source content is not included here.</p>
            {reviewedJob ? <div>
              <h4>Persisted selection: {reviewedJob.id}</h4>
              <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(reviewedJob, null, 2)}</pre>
            </div> : null}
            {!reviewedApproval ? <p>Approval requires a current preview for this exact job ID and directory.</p> : null}
          </> : null}
          <button type="submit" disabled={busy || !privilegedUnlocked || !permitted || !reviewedApproval}>{busy ? "Processing…" : definition.label}</button>
          {!permitted ? <p>Required permission: <code>{definition.permission}{action === "exports.release" ? " and privacy.export" : ""}</code></p> : null}
        </form>
        {result ? <details open><summary>Action result — review unsupported and pending outcomes</summary>
          <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(result, null, 2)}</pre>
        </details> : null}
      </section>
      <Summaries title="Retention and request jobs" rows={state.jobs ?? []} hasMore={state.jobs_has_more} />
      {reviewExports.length ? <section className="panel">
        <h3>Reviewer downloads</h3>
        <p>For authorized reviewers only. Awaiting-review files are not approved for subject delivery; inspect third-party content before independent release. These are not public download links.</p>
        <ul>{reviewExports.map((job) => <li key={String(job.id)}>
          <a href={`${API_BASE}/api/privacy/exports/${encodeURIComponent(String(job.id))}?${new URLSearchParams({ directory_path: selectedDirectoryPath })}`}>
            Download for review: {String(job.id)}
          </a> — {String(job.status)}; expires {String(job.expires_at)}
        </li>)}</ul>
      </section> : null}
      <section className="panel" aria-label="Stored privacy evidence">
        <h3>Inspect stored evidence</h3>
        <p>Open a record below or enter a known ID to review its saved references and selection in this directory.</p>
        <form onSubmit={(event) => { event.preventDefault(); void inspectEvidence(evidenceKind, evidenceId); }}>
          <label>Evidence type<select value={evidenceKind} disabled={busy} onChange={(event) => {
            setEvidenceKind(event.target.value); setEvidence(null);
          }}>{Object.entries(EVIDENCE_KINDS).map(([kind, entry]) => <option key={kind} value={kind}>{entry.label}</option>)}</select></label>
          <label>Evidence record ID<input value={evidenceId} required disabled={busy} onChange={(event) => {
            setEvidenceId(event.target.value); setEvidence(null);
          }} /></label>
          <button type="submit" disabled={busy || !evidenceId.trim() || !state.permissions?.includes(EVIDENCE_KINDS[evidenceKind].permission)}>Read stored evidence</button>
        </form>
        {evidence && evidence.scope === selectedDirectoryPath ? <div>
          <h4>Stored evidence: {evidence.id}</h4>
          <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(evidence, null, 2)}</pre>
        </div> : null}
      </section>
      {([
        ["hold", "Holds", state.holds, state.holds_has_more],
        ["case", "Data-subject requests", state.cases, state.cases_has_more],
        ["followup", "External follow-ups", state.followups, state.followups_has_more],
        ["incident", "Incidents", state.incidents, state.incidents_has_more],
      ] as const).map(([kind, title, rows, hasMore]) => <Summaries key={kind} title={title} rows={rows ?? []} hasMore={hasMore} busy={busy}
        onInspect={state.permissions?.includes(EVIDENCE_KINDS[kind].permission) ? (id) => { void inspectEvidence(kind, id); } : undefined} />)}
    </> : null}
  </div>;
}
