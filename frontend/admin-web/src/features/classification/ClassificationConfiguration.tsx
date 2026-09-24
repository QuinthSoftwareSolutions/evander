import { useEffect, useState } from "react";
import { fetchJson } from "../admin/api";
import { QuestionsEditor } from "./QuestionsEditor";
import { EvaluationTest } from "./EvaluationTest";
import { isEditableConfig, mergeClassification, nextId, type ClassificationConfig, type ClassificationSnapshot, type Profile, type ProviderSettings } from "./classificationDraft";
import "./classification.css";

export function ClassificationConfiguration({directoryPath, disabled, onDirtyChange}: {
  directoryPath: string; disabled: boolean; onDirtyChange: (dirty: boolean) => void;
}) {
  const [snapshot, setSnapshot] = useState<ClassificationSnapshot | null>(null);
  const [draft, setDraft] = useState<ClassificationConfig>({});
  const [selected, setSelected] = useState("");
  const [newId, setNewId] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  const [raw, setRaw] = useState("");
  const [invalidJson, setInvalidJson] = useState(false);
  const dirty = Boolean(snapshot && (invalidJson || JSON.stringify(draft) !== JSON.stringify(snapshot.config)));
  const url = `/api/classification?directory_path=${encodeURIComponent(directoryPath)}`;
  useEffect(() => {onDirtyChange(dirty);}, [dirty, onDirtyChange]);
  useEffect(() => {
    let active = true;
    fetchJson<ClassificationSnapshot>(url).then(data => {if (active) {setSnapshot(data); setDraft(data.config);}})
      .catch(e => {if (active) setError(String(e));});
    return () => {active = false;};
  }, [url]);
  const effective = mergeClassification(snapshot?.parent_config ?? {}, draft);
  const profiles = effective.profiles ?? {};
  const profile = profiles[selected];
  const local = selected in (draft.profiles ?? {});
  const locked = disabled || busy;
  function change(next: ClassificationConfig) {setDraft(next); setNotice("");}
  function updateProfile(patch: Partial<Profile>) {
    change({...draft, profiles: {...draft.profiles, [selected]: {...profile, ...patch}}});
  }
  function updateProvider(id: string, patch: Partial<ProviderSettings>) {
    change({...draft, providers: {...draft.providers, [id]: {...draft.providers?.[id], ...patch}}});
  }
  async function save() {
    if (!snapshot) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const data = await fetchJson<ClassificationSnapshot>(url, {method: "PUT", body: JSON.stringify({expected_revision: snapshot.revision, config: draft})});
      setSnapshot(data); setDraft(data.config); setNotice("Classification configuration saved.");
    } catch (e) {setError(e instanceof Error ? e.message : String(e));}
    finally {setBusy(false);}
  }
  async function reload() {
    if (dirty && !window.confirm("Discard unsaved classification changes and reload?")) return;
    setBusy(true); setError("");
    try {
      const data = await fetchJson<ClassificationSnapshot>(url);
      setSnapshot(data); setDraft(data.config); setAdvanced(false); setInvalidJson(false); setNotice("");
    } catch (e) {setError(String(e));}
    finally {setBusy(false);}
  }
  return <article className="panel classification-panel classification-configuration">
    <div className="panel-header"><h3>Classification</h3><span>{directoryPath}</span></div>
    <p>Evaluate shared input against choice, score, or boolean questions. Profiles fix the questions, provider, and model for workflows and agents.</p>
    {error && <p role="alert" className="error-text">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    {!snapshot ? <p>Loading classification configuration…</p> : <>
      <div className="classification-actions">
        <button type="button" disabled={locked || !dirty || invalidJson} onClick={() => void save()}>Save classification</button>
        <button type="button" disabled={busy} onClick={() => void reload()}>Reload</button>
        <button type="button" disabled={locked || invalidJson} onClick={() => {setAdvanced(!advanced); setRaw(JSON.stringify(draft, null, 2));}}> {advanced ? "Structured editor" : "Advanced configuration JSON"}</button>
        <button type="button" disabled={locked} onClick={() => {change({}); setAdvanced(false); setInvalidJson(false);}}>Revert all local overrides</button>
      </div>
      {advanced ? <label className="field-stack"><span>Local classification overrides</span><textarea className="classification-json" disabled={locked} value={raw} onChange={e => {
        setRaw(e.target.value); setNotice("");
        try {const value: unknown = JSON.parse(e.target.value); if (!isEditableConfig(value)) throw new Error(); setDraft(value); setInvalidJson(false);} catch {setInvalidJson(true);}
      }} />{invalidJson && <span role="alert">Enter a configuration object with valid provider and profile fields before saving.</span>}</label> : <>
        <label><input type="checkbox" disabled={locked} checked={effective.enabled === true} onChange={e => change({...draft, enabled: e.target.checked})} /> Enable classification in this directory</label>
        <label className="field-stack"><span>Default provider</span><select aria-label="Default provider" disabled={locked} value={effective.default_provider ?? "jev"} onChange={e => change({...draft, default_provider: e.target.value})}>
          {snapshot.providers.map(p => <option key={p.id} value={p.id}>{p.id}</option>)}
        </select></label>
        {snapshot.providers.map(provider => {
          const settings = effective.providers?.[provider.id] ?? {};
          return <fieldset key={provider.id} disabled={locked}><legend>{provider.id} provider</legend>
            <p>{provider.credential_configured ? "Host credential configured" : "Host credential missing"} · Supports {provider.supported_question_types.join(", ")}</p>
            <label><input type="checkbox" checked={settings.enabled !== false} onChange={e => updateProvider(provider.id, {enabled: e.target.checked})} /> Provider enabled</label>
            <label className="field-stack"><span>Default model</span><input value={settings.default_model ?? "jev-1.13.0"} onChange={e => updateProvider(provider.id, {default_model: e.target.value})} /></label>
            <label className="field-stack"><span>Host credential environment variable</span><input value={settings.api_key_env ?? "TYPESAFE_API_KEY"} onChange={e => updateProvider(provider.id, {api_key_env: e.target.value})} /></label>
            <label className="field-stack"><span>Timeout in seconds</span><input type="number" min="1" max="300" value={settings.timeout_seconds ?? 30} onChange={e => updateProvider(provider.id, {timeout_seconds: Number(e.target.value)})} /></label>
            <label className="field-stack"><span>Retries</span><input type="number" min="0" max="5" value={settings.max_retries ?? 2} onChange={e => updateProvider(provider.id, {max_retries: Number(e.target.value)})} /></label>
            <button type="button" onClick={() => {const providers = {...draft.providers}; delete providers[provider.id]; change({...draft, providers});}}>Revert provider overrides</button>
          </fieldset>;
        })}
        <h4>Profiles</h4>
        <label className="field-stack"><span>Profile</span><select aria-label="Profile" value={selected} onChange={e => setSelected(e.target.value)}>
          <option value="">Select a profile</option>
          {Object.entries(profiles).map(([id, p]) => <option key={id} value={id}>{p.label || id} ({id}){p.enabled === false ? " — disabled" : ""}{id in (draft.profiles ?? {}) ? " — local" : " — inherited"}</option>)}
        </select></label>
        <div className="classification-actions"><label className="field-stack"><span>New profile ID</span><input disabled={locked} value={newId} placeholder={nextId(profiles, "profile")} onChange={e => setNewId(e.target.value)} /></label>
          <button type="button" disabled={locked} onClick={() => {
            const id = newId.trim() || nextId(profiles, "profile");
            if (id in profiles) {setError("This profile ID already exists."); return;}
            const provider = effective.default_provider ?? "jev";
            change({...draft, profiles: {...draft.profiles, [id]: {label: id, enabled: true, provider, model: effective.providers?.[provider]?.default_model ?? "jev-1.13.0", questions: {}}}});
            setSelected(id); setNewId(""); setError("");
          }}>Add profile</button></div>
        {profile && <section className="classification-profile">
          <p>{local ? "Local definition. Saving replaces this entire profile in this directory." : "Inherited definition. Editing creates a complete local override."}</p>
          <fieldset disabled={locked}><legend>{selected}</legend>
            <label><input type="checkbox" checked={profile.enabled !== false} onChange={e => updateProfile({enabled: e.target.checked})} /> Profile enabled</label>
            <label className="field-stack"><span>Label</span><input value={profile.label ?? ""} onChange={e => updateProfile({label: e.target.value})} /></label>
            <label className="field-stack"><span>Description</span><textarea value={profile.description ?? ""} onChange={e => updateProfile({description: e.target.value})} /></label>
            <label className="field-stack"><span>Profile provider</span><select value={profile.provider ?? "jev"} onChange={e => updateProfile({provider: e.target.value})}>{snapshot.providers.map(p => <option key={p.id} value={p.id}>{p.id}</option>)}</select></label>
            <label className="field-stack"><span>Profile model</span><input value={profile.model ?? "jev-1.13.0"} onChange={e => updateProfile({model: e.target.value})} /></label>
          </fieldset>
          <QuestionsEditor key={selected} value={profile.questions} disabled={locked} onChange={questions => updateProfile({questions})} />
          <div className="classification-actions">
            <button type="button" disabled={locked} onClick={() => updateProfile({enabled: false})}>Disable in this directory</button>
            <button type="button" disabled={locked || !local} onClick={() => {const next = {...draft.profiles}; delete next[selected]; change({...draft, profiles: next});}}>Delete local override / restore inherited profile</button>
          </div>
          {!snapshot.effective_config.enabled && <p>Save enabled provider settings before testing.</p>}
          <EvaluationTest key={directoryPath + selected} directoryPath={directoryPath} disabled={locked || profile.enabled === false || !snapshot.effective_config.enabled}
            definition={local ? {questions: profile.questions, provider: profile.provider ?? "jev", model: profile.model ?? "jev-1.13.0"} : {profile_id: selected}} />
        </section>}
      </>}
    </>}
  </article>;
}
