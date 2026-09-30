import { useEffect, useRef, useState } from "react";
import { fetchJson } from "../admin/api";

type Binding = {response_pointer: string; encoding: "json" | "string"};
type Profile = {
  enabled: boolean; browser_origin: string; login_url: string;
  adapter?: string; adapter_config?: Record<string, unknown>; cookies_response_pointer?: string | null;
  credential_source: "stored" | "environment"; credential_ref?: string | null;
  credential_fields: Record<string, string>; public_credential_fields: string[];
  static_fields: Record<string, unknown>; local_storage: Record<string, Binding>; session_storage: Record<string, Binding>;
  jwt_response_pointer: string | null; expires_in_response_pointer: string | null;
  max_age_seconds: number; renew_before_seconds: number; timeout_seconds: number; max_response_bytes: number;
};
type Entry = {id: string; profile: Profile; local: boolean;
  credentials: {configured_fields: string[]; managed_by?: string; updated_at: number | null; owner_directory: string | null};
  last_test: {status: string; checked_at: number; expires_at: number | null} | null};
type Snapshot = {directory_path: string; revision: string; profiles: Entry[]};
type Credential = {name: string; reference: string; value: string; public: boolean};
type Storage = {name: string; location: "local" | "session"; pointer: string; encoding: "json" | "string"};
type Draft = {id: string; existing: boolean; profile: Profile; credentials: Credential[]; storage: Storage[]; staticJson: string};

function makeDraft(entry?: Entry): Draft {
  const profile: Profile = entry ? structuredClone(entry.profile) : {
    enabled: true, browser_origin: "", login_url: "", credential_source: "stored",
    credential_fields: {username: "username", password: "password"}, public_credential_fields: ["username"],
    static_fields: {}, local_storage: {accessToken: {response_pointer: "/token", encoding: "string"}}, session_storage: {},
    jwt_response_pointer: null, expires_in_response_pointer: null,
    max_age_seconds: 3600, renew_before_seconds: 60, timeout_seconds: 20, max_response_bytes: 262144,
  };
  return {id: entry?.id ?? "", existing: Boolean(entry), profile,
    credentials: Object.entries(profile.credential_fields).map(([name, reference]) => ({name, reference, value: "", public: profile.public_credential_fields.includes(name)})),
    storage: (["local", "session"] as const).flatMap(location => Object.entries(location === "local" ? profile.local_storage : profile.session_storage)
      .map(([name, binding]) => ({name, location, pointer: binding.response_pointer, encoding: binding.encoding}))),
    staticJson: JSON.stringify(profile.static_fields, null, 2)};
}

export function WebsiteLoginsConfiguration({directoryPath, disabled, onDirtyChange}: {
  directoryPath: string; disabled: boolean; onDirtyChange?: (dirty: boolean) => void;
}) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const generation = useRef(0);
  const endpoint = `/api/managed-website-sessions?directory_path=${encodeURIComponent(directoryPath)}`;
  useEffect(() => {
    const current = ++generation.current;
    setSnapshot(null); setDraft(null); setDirty(false); setError(null); setMessage(null); setBusy(true);
    fetchJson<Snapshot>(endpoint).then(data => {if (generation.current === current) setSnapshot(data);})
      .catch(() => {if (generation.current === current) setError("Could not load website logins. Directory owner access is required.");})
      .finally(() => {if (generation.current === current) setBusy(false);});
    return () => {generation.current++;};
  }, [endpoint]);
  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);
  useEffect(() => {
    const guard = (event: BeforeUnloadEvent) => {if (dirty) {event.preventDefault(); event.returnValue = "";}};
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [dirty]);

  function choose(entry?: Entry) {
    if (dirty && !window.confirm("Discard unsaved website login changes?")) return;
    setDraft(makeDraft(entry)); setDirty(false); setError(null); setMessage(null);
  }
  function update(patch: Partial<Draft>) {setDraft(current => current ? {...current, ...patch} : current); setDirty(true); setMessage(null);}
  function profile(patch: Partial<Profile>) {if (draft) update({profile: {...draft.profile, ...patch}});}
  async function mutate(id: string, method: string, suffix: string, body: Record<string, unknown>) {
    if (!snapshot) return;
    const current = generation.current;
    setBusy(true); setError(null); setMessage(null);
    try {
      const data = await fetchJson<Snapshot>(`/api/managed-website-sessions/${encodeURIComponent(id)}${suffix}?directory_path=${encodeURIComponent(directoryPath)}`, {
        method, body: JSON.stringify({...body, expected_revision: snapshot.revision}),
      });
      if (current !== generation.current) return;
      setSnapshot(data);
      if (method === "PUT" || method === "DELETE") {
        const entry = data.profiles.find(p => p.id === id);
        setDraft(entry ? makeDraft(entry) : null); setDirty(false);
        setMessage(method === "PUT" ? "Website login saved. Credential values are not returned to the browser." : "Website login updated.");
      } else {
        const result = data.profiles.find(p => p.id === id)?.last_test;
        if (result?.status === "ok") setMessage("Login succeeded. Temporary authentication state was checked on the host.");
        else if (result?.status === "adapter_configured") setMessage("Website login adapter configuration verified.");
        else setError(`Login test failed: ${result?.status ?? "no result"}. Check the saved credentials and login configuration.`);
      }
    } catch (reason) {
      if (current === generation.current) setError(reason instanceof Error ? reason.message : "Website login operation failed.");
    } finally {if (current === generation.current) setBusy(false);}
  }
  function save() {
    if (!draft) return;
    try {
      if (draft.profile.adapter && draft.profile.adapter !== "json") {
        void mutate(draft.id.trim(), "PUT", "", {profile: {...draft.profile, credential_ref: null}, credentials: {}});
        return;
      }
      const names = draft.credentials.map(c => c.name.trim());
      if (!names.length || names.some(n => !n) || new Set(names).size !== names.length) throw new Error("Login fields need unique, nonempty names.");
      const staticFields: unknown = JSON.parse(draft.staticJson);
      if (!staticFields || typeof staticFields !== "object" || Array.isArray(staticFields)) throw new Error("Fixed login fields must be a JSON object.");
      const local: Record<string, Binding> = {}, session: Record<string, Binding> = {};
      for (const binding of draft.storage) {
        const target = binding.location === "local" ? local : session;
        if (!binding.name.trim() || Object.prototype.hasOwnProperty.call(target, binding.name.trim())) throw new Error("Browser storage keys must be nonempty and unique within their storage type.");
        target[binding.name.trim()] = {response_pointer: binding.pointer, encoding: binding.encoding};
      }
      const configured: Profile = {...draft.profile, credential_ref: null,
        credential_fields: Object.fromEntries(draft.credentials.map(c => [c.name.trim(), draft.profile.credential_source === "stored" ? c.name.trim() : c.reference.trim()])),
        public_credential_fields: draft.credentials.filter(c => c.public).map(c => c.name.trim()),
        static_fields: staticFields as Record<string, unknown>, local_storage: local, session_storage: session};
      const credentials = Object.fromEntries(draft.credentials.filter(c => c.value).map(c => [c.name.trim(), c.value]));
      void mutate(draft.id.trim(), "PUT", "", {profile: configured, credentials});
    } catch (reason) {setError(reason instanceof Error ? reason.message : "Check the website login fields.");}
  }
  const locked = disabled || busy || !snapshot;
  const selected = snapshot?.profiles.find(p => p.id === draft?.id);
  const adapterManaged = Boolean(draft?.profile.adapter && draft.profile.adapter !== "json");
  return <article className="panel website-logins-panel">
    <p>Create reusable website logins for this directory and its children. Grant a profile to an assistant in its HTTP targets.</p>
    <p className="muted-text">Credentials stay on the host. The browser receives temporary authentication state.</p>
    {error ? <div role="alert" className="callout error-callout">{error}</div> : null}
    {message ? <div role="status" className="callout success-callout">{message}</div> : null}
    <div className="inline-actions">
      <button type="button" onClick={() => choose()} disabled={locked}>New website login</button>
      <button type="button" disabled={busy} onClick={() => {
        if (dirty && !window.confirm("Discard unsaved changes and reload website logins?")) return;
        setBusy(true); const current = generation.current;
        fetchJson<Snapshot>(endpoint).then(data => {if (current === generation.current) {setSnapshot(data); setDraft(null); setDirty(false); setError(null);}})
          .catch(() => {if (current === generation.current) setError("Could not reload website logins.");})
          .finally(() => {if (current === generation.current) setBusy(false);});
      }}>Reload website logins</button>
    </div>
    <div className="website-login-list">
      {snapshot?.profiles.length === 0 ? <div className="empty-state">No website logins configured.</div> : null}
      {snapshot?.profiles.map(entry => <div key={entry.id} className="website-login-summary">
        <button type="button" onClick={() => choose(entry)} disabled={busy}>{entry.id}</button>
        <span>{entry.profile.browser_origin}</span>
        <span>{entry.profile.enabled ? "Enabled" : "Disabled"} · {entry.local ? "Local profile" : "Inherited profile"}</span>
        <span>{entry.profile.adapter && entry.profile.adapter !== "json" ? `Managed by ${entry.credentials.managed_by ?? entry.profile.adapter}` :
          entry.credentials.configured_fields.length === Object.keys(entry.profile.credential_fields).length ? "Credentials configured" : "Credentials missing"}</span>
        {entry.last_test ? <span>Last test: {entry.last_test.status} · {new Date(entry.last_test.checked_at * 1000).toLocaleString()}</span> : <span>Not tested for current settings</span>}
        {entry.last_test?.expires_at ? <span>Tested session valid until {new Date(entry.last_test.expires_at * 1000).toLocaleString()}</span> : null}
      </div>)}
    </div>
    {draft ? <form onSubmit={event => {event.preventDefault(); save();}} autoComplete="off">
      {selected && !selected.local ? <div className="callout">This profile is inherited. Saving creates an override in {directoryPath}. Removing credentials or deleting it must be done in its owning directory.</div> : null}
      <fieldset disabled={locked} className="website-login-fields">
        <legend>{draft.existing ? `Edit ${draft.id}` : "New website login"}</legend>
        <div className="website-login-grid">
          <label className="field-stack"><span>Profile ID</span><input required pattern="[A-Za-z0-9][A-Za-z0-9_.-]*" value={draft.id} disabled={draft.existing} onChange={e => update({id: e.target.value})} /></label>
          <label className="field-stack"><span>Website origin</span><input required type="url" placeholder="https://training.example.org" value={draft.profile.browser_origin} onChange={e => profile({browser_origin: e.target.value})} /></label>
          <label className="field-stack"><span>Login endpoint</span><input required type="url" placeholder="https://training.example.org/api/login" value={draft.profile.login_url} onChange={e => profile({login_url: e.target.value})} /></label>
          <label className="checkbox-row"><input type="checkbox" checked={draft.profile.enabled} onChange={e => profile({enabled: e.target.checked})} /><span>Enabled</span></label>
          {!adapterManaged ? <label className="field-stack"><span>Credential source</span><select value={draft.profile.credential_source} onChange={e => {
            update({profile: {...draft.profile, credential_source: e.target.value as Profile["credential_source"]}, credentials: draft.credentials.map(c => ({...c, value: ""}))});
          }}><option value="stored">Encrypted host storage</option><option value="environment">Existing host environment variables</option></select></label> : null}
        </div>
        {adapterManaged ? <p className="field-description">Credentials and browser authentication state are managed by {draft.profile.adapter}.</p> : <>
        <h3>Login fields and credentials</h3>
        <p className="field-description">Leave a saved value blank to keep it. Changing the website, login endpoint, login fields or public identifiers requires entering all credentials again. Never mark a password as a public identifier.</p>
        {draft.credentials.map((credential, index) => <div className="website-login-grid website-login-row" key={index}>
          <label className="field-stack"><span>Login field {index + 1}</span><input required value={credential.name} onChange={e => update({credentials: draft.credentials.map((c, i) => i === index ? {...c, name: e.target.value} : c)})} /></label>
          {draft.profile.credential_source === "stored" ? <label className="field-stack"><span>Value for {credential.name || "login field"}</span><input type="password" autoComplete="new-password" value={credential.value}
            placeholder={selected?.credentials.configured_fields.includes(credential.name) ? "Configured — leave blank to keep" : "Enter credential"}
            onChange={e => update({credentials: draft.credentials.map((c, i) => i === index ? {...c, value: e.target.value} : c)})} /></label>
            : <label className="field-stack"><span>Environment variable for {credential.name}</span><input value={credential.reference} onChange={e => update({credentials: draft.credentials.map((c, i) => i === index ? {...c, reference: e.target.value} : c)})} /></label>}
          <label className="checkbox-row"><input type="checkbox" checked={credential.public} onChange={e => update({credentials: draft.credentials.map((c, i) => i === index ? {...c, public: e.target.checked} : c)})} /><span>Public identifier (may appear in response)</span></label>
          <button type="button" onClick={() => update({credentials: draft.credentials.filter((_, i) => i !== index)})}>Remove login field {index + 1}</button>
        </div>)}
        <button type="button" onClick={() => update({credentials: [...draft.credentials, {name: "", reference: "", value: "", public: false}]})}>Add login field</button>
        <h3>Browser authentication state</h3>
        <p className="field-description">Map selected login response values into browser storage. A JSON pointer such as /token selects a response field; an empty pointer selects the whole response.</p>
        {draft.storage.map((binding, index) => <div key={index} className="website-login-grid website-login-row">
          <label className="field-stack"><span>Storage key {index + 1}</span><input required value={binding.name} onChange={e => update({storage: draft.storage.map((b, i) => i === index ? {...b, name: e.target.value} : b)})} /></label>
          <label className="field-stack"><span>Storage location {index + 1}</span><select value={binding.location} onChange={e => update({storage: draft.storage.map((b, i) => i === index ? {...b, location: e.target.value as Storage["location"]} : b)})}><option value="local">Local storage</option><option value="session">Session storage</option></select></label>
          <label className="field-stack"><span>Response pointer {index + 1}</span><input value={binding.pointer} onChange={e => update({storage: draft.storage.map((b, i) => i === index ? {...b, pointer: e.target.value} : b)})} /></label>
          <label className="field-stack"><span>Storage encoding {index + 1}</span><select value={binding.encoding} onChange={e => update({storage: draft.storage.map((b, i) => i === index ? {...b, encoding: e.target.value as Binding["encoding"]} : b)})}><option value="string">String value</option><option value="json">JSON</option></select></label>
          <button type="button" onClick={() => update({storage: draft.storage.filter((_, i) => i !== index)})}>Remove storage mapping {index + 1}</button>
        </div>)}
        <button type="button" onClick={() => update({storage: [...draft.storage, {name: "", location: "local", pointer: "", encoding: "string"}]})}>Add storage mapping</button>
        </>}
        <h3>Expiry and renewal</h3>
        <div className="website-login-grid">
          {!adapterManaged ? <><label className="field-stack"><span>Expiry source</span><select aria-label="Expiry source" value={draft.profile.jwt_response_pointer !== null ? "jwt" : draft.profile.expires_in_response_pointer !== null ? "lifetime" : "age"}
            onChange={e => profile({jwt_response_pointer: e.target.value === "jwt" ? "/token" : null, expires_in_response_pointer: e.target.value === "lifetime" ? "/expires_in" : null})}>
            <option value="age">Maximum age only</option><option value="jwt">JWT expiry in login response</option><option value="lifetime">Lifetime in seconds in login response</option></select></label>
          {draft.profile.jwt_response_pointer !== null ? <label className="field-stack"><span>JWT response pointer</span><input value={draft.profile.jwt_response_pointer} onChange={e => profile({jwt_response_pointer: e.target.value})} /></label> : null}
          {draft.profile.expires_in_response_pointer !== null ? <label className="field-stack"><span>Lifetime response pointer</span><input value={draft.profile.expires_in_response_pointer} onChange={e => profile({expires_in_response_pointer: e.target.value})} /></label> : null}
          </> : null}
          {([ ["max_age_seconds", "Maximum age (seconds)", 60, 86400], ["renew_before_seconds", "Renew before expiry (seconds)", 5, 3600], ["timeout_seconds", "Login timeout (seconds)", 1, 60], ["max_response_bytes", "Maximum response (bytes)", 1024, 1048576] ] as const).map(([key, label, min, max]) =>
            <label className="field-stack" key={key}><span>{label}</span><input type="number" min={min} max={max} required value={draft.profile[key]} onChange={e => profile({[key]: Number(e.target.value)})} /></label>)}
        </div>
        {!adapterManaged ? <details><summary>Fixed login fields</summary><p>Additional nonsecret JSON values sent with the login request, such as a tenant name.</p><textarea aria-label="Fixed login fields JSON" rows={5} value={draft.staticJson} onChange={e => update({staticJson: e.target.value})} /></details> : null}
        <div className="inline-actions website-login-actions">
          <button type="submit" className="primary" disabled={!dirty || !draft.id.trim()}>Save website login</button>
          <button type="button" disabled={!selected || dirty || !draft.profile.enabled} onClick={() => void mutate(draft.id, "POST", "/test", {})}>Test saved login</button>
          <button type="button" disabled={adapterManaged || !selected?.local || dirty || selected.profile.credential_source !== "stored"} onClick={() => {
            if (window.confirm("Remove the saved credentials? New browser sessions will fail until credentials are configured again.")) void mutate(draft.id, "DELETE", "/credentials", {});
          }}>Remove saved credentials</button>
          <button type="button" className="danger" disabled={!selected?.local || dirty} onClick={() => {
            if (window.confirm("Remove this directory's website login profile? Any inherited parent profile will become effective again.")) void mutate(draft.id, "DELETE", "", {});
          }}>Remove local profile</button>
        </div>
      </fieldset>
    </form> : null}
  </article>;
}
