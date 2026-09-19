import {useEffect, useState} from "react";
import {fetchJson} from "../admin/api";
import {EvaluationTest} from "./EvaluationTest";
import {isRecord, nextId, type ClassificationSnapshot} from "./classificationDraft";
import {classifierSwitchConfig, classifierSwitchProblems} from "./classifierSwitchDraft";
import "./classification.css";

export function WorkflowClassifierSwitchEditor({value, directoryPath, disabled, onChange}: {
  value: Record<string, unknown>; directoryPath: string; disabled: boolean;
  onChange: (patch: Record<string, unknown>) => void;
}) {
  const [snapshot, setSnapshot] = useState<ClassificationSnapshot | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    setSnapshot(null); setError("");
    fetchJson<ClassificationSnapshot>("/api/classification?directory_path=" + encodeURIComponent(directoryPath))
      .then(data => {if (active) setSnapshot(data);}).catch(e => {if (active) setError(String(e));});
    return () => {active = false;};
  }, [directoryPath]);
  const cases = Array.isArray(value.cases) ? value.cases.filter(isRecord) : [];
  const config = classifierSwitchConfig(value);
  const problems = classifierSwitchProblems(config);
  const selectedProvider = typeof value.provider === "string" && value.provider ? value.provider : snapshot?.effective_config.default_provider ?? "jev";
  const settings = snapshot?.effective_config.providers?.[selectedProvider];
  const enabled = snapshot?.effective_config.enabled && settings?.enabled !== false;
  function text(key: string) {return typeof value[key] === "string" ? value[key] : "";}
  function changeCase(index: number, patch: Record<string, unknown>) {
    onChange({cases: cases.map((c, i) => i === index ? {...c, ...patch} : c)});
  }
  return <div className="classification-panel">
    {error && <p role="alert">{error}</p>}
    <p>Describe when each branch should be selected. The original input continues on the selected branch.</p>
    <fieldset disabled={disabled}><legend>Classification routing</legend>
      <label className="field-stack"><span>Routing instructions</span><textarea aria-label="Routing instructions" value={text("instructions")} onChange={e => onChange({instructions: e.target.value})} /></label>
      {cases.map((branch, index) => <fieldset key={index}><legend>Branch {index + 1}</legend>
        <label className="field-stack"><span>Branch port</span><input value={String(branch.port ?? "")} onChange={e => changeCase(index, {port: e.target.value})} /></label>
        <label className="field-stack"><span>Branch label</span><input value={String(branch.label ?? "")} onChange={e => changeCase(index, {label: e.target.value})} /></label>
        <label className="field-stack"><span>Branch description</span><textarea aria-label="Branch description" value={String(branch.description ?? "")} onChange={e => changeCase(index, {description: e.target.value})} /></label>
        <button type="button" onClick={() => onChange({cases: cases.filter((_, i) => i !== index)})}>Remove branch</button>
      </fieldset>)}
      <button type="button" onClick={() => {
        const ports = Object.fromEntries([...cases.map(c => [String(c.port), true]), [text("default_port"), true]]);
        onChange({cases: [...cases, {port: nextId(ports, "branch"), label: "", description: ""}]});
      }}>Add branch</button>
      <label className="field-stack"><span>Default port</span><input value={text("default_port")} onChange={e => onChange({default_port: e.target.value})} /></label>
      <label className="field-stack"><span>Default description</span><textarea aria-label="Default description" value={text("default_description") || (value.default_description === undefined ? "None of the described routes clearly applies." : "")} onChange={e => onChange({default_description: e.target.value})} /></label>
      <label className="field-stack"><span>Classification provider</span><select value={text("provider")} onChange={e => onChange({provider: e.target.value || undefined})}>
        <option value="">Directory default ({snapshot?.effective_config.default_provider ?? "jev"})</option>
        {(snapshot?.providers ?? [{id: "jev"}]).map(p => <option key={p.id} value={p.id}>{p.id}</option>)}
      </select></label>
      <label className="field-stack"><span>Classification model</span><input value={text("model")} placeholder={settings?.default_model ?? "Provider default"} onChange={e => onChange({model: e.target.value || undefined})} /></label>
      <label><input type="checkbox" checked={value.min_probability != null} onChange={e => onChange({min_probability: e.target.checked ? 0.8 : undefined})} /> Use probability cutoff</label>
      {value.min_probability != null && <label className="field-stack"><span>Minimum option probability</span><input type="number" min="0" max="1" step="0.01" value={Number(value.min_probability)} onChange={e => onChange({min_probability: e.target.value === "" ? undefined : Number(e.target.value)})} /></label>}
      <p>Results below the cutoff take the default branch. The cutoff uses option probability.</p>
    </fieldset>
    {problems.map(problem => <p role="alert" key={problem}>{problem}</p>)}
    {snapshot && !enabled && <p>Enable classification and the selected provider in this directory before testing.</p>}
    <EvaluationTest key={directoryPath} directoryPath={directoryPath} mode="switch" definition={config}
      disabled={disabled || !enabled || problems.length > 0} />
  </div>;
}
