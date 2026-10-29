import {useEffect, useRef, useState} from "react";
import {fetchJson} from "../admin/api";
import {ManagedTeamsSignIn} from "./ManagedTeamsSignIn";

type Binding = {consumer: string; profile_id?: string; connector_id?: string; directory?: string; identity_id?: string;
  destination?:string; token_endpoint?:string; operations?:string[]; tenant?:string; client_id?:string; authority?:string; scopes?:string[]};
export type CredentialMetadata = {
  id: string; label: string; kind: "connector" | "teams_oauth"; owner_directory: string;
  status: string; revision: string; expires_at: number | null; local: boolean; bindings: Binding[];
  current_bindings?:Binding[]; bindings_revision?:string; binding_error?:string;
};
type Profile = {id: string; type: string; fields: string[]; credential_ref: string | null};
const describeBinding = (b:Binding) => b.consumer === "connector"
  ? `${b.connector_id}: ${b.destination ?? "configured destination"}; operations: ${b.operations?.join(", ") ?? "configured"}${b.token_endpoint ? `; token endpoint: ${b.token_endpoint}` : ""}`
  : `${b.directory}: ${b.identity_id}; client: ${b.client_id}; tenant: ${b.tenant}; authority: ${b.authority}; Graph destination: ${b.destination}; scopes: ${b.scopes?.join(", ")}`;
export type CredentialsSnapshot = {
  directory_path: string; credentials: CredentialMetadata[]; connector_profiles: Profile[];
  connector_revision: string; teams_revision: string; teams_configured: boolean;
  store: {initialized: boolean; available: boolean};
};

export function CredentialsConfiguration({directoryPath, disabled, onDirtyChange}: {
  directoryPath: string; disabled: boolean; onDirtyChange?: (dirty: boolean) => void;
}) {
  const [state, setState] = useState<CredentialsSnapshot | null>(null);
  const [profileId, setProfileId] = useState("");
  const [label, setLabel] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [replacement, setReplacement] = useState<string | null>(null);
  const [teamsCredential, setTeamsCredential] = useState<string | null>(null);
  const [expiry, setExpiry] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const current = useRef(directoryPath);
  const generation = useRef(0);
  current.current = directoryPath;
  const endpoint = (scope: string) => `/api/admin/credentials?directory_path=${encodeURIComponent(scope)}`;
  const active = state?.directory_path === directoryPath ? state : null;
  const record = active?.credentials.find(c => c.id === replacement);
  const teamsRecord = active?.credentials.find(c => c.id === teamsCredential && c.local && c.kind === "teams_oauth");
  const profile = active?.connector_profiles.find(p => replacement ? p.credential_ref === replacement : p.id === profileId);
  const dirty = Boolean(label || expiry || replacement || Object.values(values).some(Boolean));
  useEffect(() => {onDirtyChange?.(dirty);}, [dirty, onDirtyChange]);

  function clear() {setValues({}); setLabel(""); setExpiry(""); setReplacement(null); setProfileId(""); setTeamsCredential(null);}

  async function refresh() {
    const scope = directoryPath, request = ++generation.current;
    setBusy(true); setError(null);
    try {
      const snapshot = await fetchJson<CredentialsSnapshot>(endpoint(scope));
      if (current.current === scope && generation.current === request) setState(snapshot);
    } catch {
      if (current.current === scope && generation.current === request) setError("Could not load credential metadata.");
    } finally {
      if (current.current === scope && generation.current === request) setBusy(false);
    }
  }

  useEffect(() => {
    clear(); setState(null); setMessage(""); void refresh();
    return () => {generation.current++;};
  }, [directoryPath]);

  async function mutate(payload: Record<string, unknown>, ref?: string) {
    const scope = directoryPath, request = ++generation.current;
    setBusy(true); setError(null); setMessage("");
    try {
      const url = ref ? `/api/admin/credentials/${encodeURIComponent(ref)}?directory_path=${encodeURIComponent(scope)}` : endpoint(scope);
      const snapshot = await fetchJson<CredentialsSnapshot>(url, {method: ref ? "PUT" : "POST", body: JSON.stringify(payload)});
      if (current.current !== scope || generation.current !== request) return;
      setState(snapshot); clear(); setMessage("Credential changes saved.");
    } catch (reason: unknown) {
      if (current.current !== scope || generation.current !== request) return;
      // Server errors are deliberately value-free; never echo the submitted bundle.
      setError(reason instanceof Error ? reason.message : "Credential operation failed.");
    } finally {
      if (current.current === scope && generation.current === request) {
        setValues({}); setBusy(false);
      }
    }
  }

  const locked = disabled || busy || !active;
  const entryLocked = locked || !active?.store.available;
  const createProfiles = active?.connector_profiles.filter(p => !p.credential_ref || !active.credentials.some(c => c.id === p.credential_ref && c.local)) ?? [];
  return <article className="panel credentials-panel">
    <div className="panel-header"><h3>Credentials</h3><button disabled={busy} onClick={() => void refresh()}>Refresh credentials</button></div>
    <p>Manage credentials for {directoryPath}. Values are accepted when saving and are never returned by this page.</p>
    {disabled ? <p className="callout warning-callout">Unlock privileged operations to make changes.</p> : null}
    {active && !active.store.available ? <p className="callout warning-callout">{active.store.initialized ? "The credential store cannot access its key. Contact the host operator." : "The host operator must initialize credential storage before credentials can be saved."}</p> : null}
    {error ? <div role="alert" className="error-box">{error}</div> : null}
    {message ? <div role="status" className="callout success-callout">{message}</div> : null}
    <div className="table-scroll"><table><thead><tr><th>Credential</th><th>Owner</th><th>Status</th><th>Used by</th><th>Expiry</th><th>Actions</th></tr></thead><tbody>
      {active?.credentials.map(c => <tr key={c.id}>
        <td data-label="Credential">{c.label}<div className="muted-text">{c.kind === "teams_oauth" ? "Teams delegated sign-in" : "Connector"}</div></td>
        <td data-label="Owner">{c.owner_directory}{!c.local ? " (inherited)" : ""}</td><td data-label="Status">{c.status}</td>
        <td data-label="Used by">{c.bindings.map((b,i) => <div key={i}>{b.connector_id ?? b.identity_id ?? b.consumer}<div className="muted-text">{b.destination ?? b.directory}</div></div>)}{!c.bindings.length ? "Inherited configuration" : null}
          {c.binding_error ? <div className="error-box">{c.binding_error}</div> : null}
          {c.current_bindings?.length ? <details><summary>Review current consumers</summary><ul>{c.current_bindings.map((b,i)=><li key={i}>{describeBinding(b)}</li>)}</ul></details> : null}
        </td>
        <td data-label="Expiry">{c.expires_at ? new Date(c.expires_at * 1000).toLocaleString() : "Unknown"}</td>
        <td data-label="Actions"><div className="credential-actions inline-actions">{c.local ? <>
          {c.kind === "connector" ? <button disabled={entryLocked} onClick={() => {clear(); setReplacement(c.id);}}>Replace values</button> : <button disabled={entryLocked || c.status !== "active"} onClick={() => {clear(); setTeamsCredential(c.id);}}>Teams sign-in</button>}
          <button disabled={entryLocked} onClick={() => void mutate({action: c.status === "disabled" ? "enable" : "disable", expected_revision: c.revision}, c.id)}>{c.status === "disabled" ? "Enable" : "Disable"}</button>
          <button disabled={entryLocked || !c.current_bindings?.length || !c.bindings_revision || Boolean(c.binding_error)} onClick={() => {
            const preview = (c.current_bindings ?? []).map(describeBinding).join("\n");
            const notice = c.kind === "teams_oauth" ? " This signs out the shared Teams identity; sign in again after approval." : "";
            if (window.confirm(`Approve these consumers?${notice}\n\n${preview}`)) void mutate({action:"approve_bindings", expected_revision:c.revision, expected_bindings_revision:c.bindings_revision}, c.id);
          }}>Approve current consumers</button>
          <button disabled={entryLocked} onClick={() => {if (window.confirm("Delete this credential? Configured consumers will stop authenticating until a new credential is assigned.")) void mutate({action:"delete", expected_revision:c.revision}, c.id);}}>Delete</button>
        </> : "Manage in owning directory"}</div></td>
      </tr>)}
    </tbody></table></div>
    {teamsRecord ? <ManagedTeamsSignIn key={`${directoryPath}:${teamsRecord.id}`}
      directoryPath={directoryPath} credentialRef={teamsRecord.id} label={teamsRecord.label}
      disabled={entryLocked || teamsRecord.status !== "active"} onUpdated={refresh}/> : null}
    {active?.credentials.length === 0 ? <p>No managed credentials in this directory.</p> : null}
    <form onSubmit={event => {
      event.preventDefault(); if (!active || !profile) return;
      const data = {values, expires_at: expiry ? new Date(expiry).getTime()/1000 : null};
      if (record) void mutate({...data, action:"replace", expected_revision:record.revision}, record.id);
      else void mutate({...data, profile_id:profile.id, label:label || profile.id, expected_config_revision:active.connector_revision});
    }}>
      <h4>{record ? `Replace ${record.label}` : "Add connector credential"}</h4>
      {!record ? <><label className="field-stack">Authentication profile<select aria-label="Credential authentication profile" value={profileId} disabled={entryLocked} onChange={e => {setProfileId(e.target.value);setValues({});}}><option value="">Select a configured profile</option>{createProfiles.map(p => <option key={p.id} value={p.id}>{p.id}</option>)}</select></label>
        <label className="field-stack">Label<input value={label} maxLength={128} disabled={entryLocked} onChange={e => setLabel(e.target.value)}/></label></> : null}
      {profile?.fields.map(field => <label className="field-stack" key={field}>{field.replace(/_/g, " ")}<input type="password" autoComplete="new-password" aria-label={`Credential ${field}`} value={values[field] ?? ""} disabled={entryLocked} required onChange={e => setValues({...values,[field]:e.target.value})}/></label>)}
      <label className="field-stack">Expiry (optional)<input type="datetime-local" value={expiry} disabled={entryLocked} onChange={e => setExpiry(e.target.value)}/></label>
      <div className="inline-actions"><button type="submit" className="primary" disabled={entryLocked || !profile || !profile.fields.every(f => values[f])}>{record ? "Save replacement" : "Save and use credential"}</button>
      {dirty ? <button type="button" onClick={clear}>Cancel credential changes</button> : null}
      </div>
    </form>
    {active?.teams_configured && !active.credentials.some(c => c.kind === "teams_oauth" && c.local) ? <div className="inline-actions"><button disabled={entryLocked} onClick={() => void mutate({kind:"teams_oauth", expected_config_revision:active.teams_revision})}>Prepare managed Teams sign-in</button><p>Existing Teams credentials require the operator migration command.</p></div> : null}
    <p>Disabling a credential stops future use through Evander. Tokens already issued by a provider follow that provider’s expiry and revocation rules.</p>
  </article>;
}
