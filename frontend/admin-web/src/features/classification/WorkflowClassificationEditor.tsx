import { classificationAnswerSchema } from "../../workflowNodeOutputs";
import { useEffect, useRef, useState } from "react";
import { fetchJson } from "../admin/api";
import { QuestionsEditor } from "./QuestionsEditor";
import { EvaluationTest } from "./EvaluationTest";
import { inlineEvaluation, isRecord, profileEvaluation, type ClassificationSnapshot } from "./classificationDraft";

export function WorkflowClassificationEditor({value, directoryPath, nodeId, disabled, onChange}: {
  value: Record<string, unknown>; directoryPath: string; nodeId: string; disabled: boolean;
  onChange: (patch: Record<string, unknown>, answers?: Record<string, unknown>) => void;
}) {
  const [snapshot, setSnapshot] = useState<ClassificationSnapshot | null>(null);
  const [error, setError] = useState("");
  const refreshGeneration = useRef(0);
  const changeRef = useRef(onChange);
  changeRef.current = onChange;
  useEffect(() => {
    let active = true;
    setSnapshot(null); setError("");
    fetchJson<ClassificationSnapshot>(`/api/classification?directory_path=${encodeURIComponent(directoryPath)}`)
      .then(data => {if (active) setSnapshot(data);}).catch(e => {if(active) setError(String(e));});
    return () => {active = false;};
  }, [directoryPath]);
  const profiles = snapshot?.effective_config.profiles ?? {};
  const profileId = typeof value.profile_id === "string" ? value.profile_id : "";
  useEffect(() => () => {refreshGeneration.current += 1;}, [directoryPath, nodeId, profileId]);
  const questions = profileId ? profiles[profileId]?.questions : value.questions;
  async function refreshAnswers() {
    const generation = ++refreshGeneration.current;
    try {
      const current = await fetchJson<ClassificationSnapshot>(`/api/classification?directory_path=${encodeURIComponent(directoryPath)}`);
      if (generation !== refreshGeneration.current) return;
      setSnapshot(current); setError("");
      changeRef.current({}, classificationAnswerSchema(current.effective_config.profiles?.[profileId]?.questions));
    } catch (e) {if (generation === refreshGeneration.current) setError(e instanceof Error ? e.message : String(e));}
  }
  return <div className="classification-panel classification-workflow">
    {error && <p role="alert">Profiles could not be loaded: {error}</p>}
    <label className="field-stack"><span>Definition</span><select aria-label="Classification profile" disabled={disabled} value={profileId}
      onChange={e => {const id = e.target.value; onChange(id ? profileEvaluation(id) : inlineEvaluation({}), classificationAnswerSchema(id ? profiles[id]?.questions : {}));}}>
      <option value="">Inline questions</option>
      {profileId && (!profiles[profileId] || profiles[profileId].enabled === false) && <option value={profileId}>{profileId} (unavailable)</option>}
      {Object.entries(profiles).filter(([, p]) => p.enabled !== false).map(([id, p]) => <option key={id} value={id}>{p.label || id}</option>)}
    </select></label>
    <details><summary>Use a profile binding</summary><label className="field-stack"><span>Profile ID or binding</span>
      <input disabled={disabled} value={profileId} placeholder="${{upstream.profile_id}}" onChange={e => onChange(
        e.target.value ? profileEvaluation(e.target.value) : inlineEvaluation({}), classificationAnswerSchema(profiles[e.target.value]?.questions))} />
    </label></details>
    {profileId ? <><p>{profiles[profileId]?.description} Provider and model are fixed by the profile.</p>
      <button type="button" disabled={disabled} onClick={() => void refreshAnswers()}>Refresh answer suggestions</button></>
      : <>
        <label className="field-stack"><span>Provider (optional)</span><input disabled={disabled} value={String(value.provider ?? "")} placeholder="Directory default" onChange={e => onChange({provider: e.target.value || undefined})} /></label>
        <label className="field-stack"><span>Model (optional)</span><input disabled={disabled} value={String(value.model ?? "")} placeholder="Provider default" onChange={e => onChange({model: e.target.value || undefined})} /></label>
        <QuestionsEditor value={value.questions} disabled={disabled} onChange={questions => onChange({questions}, classificationAnswerSchema(questions))} />
      </>}
    {isRecord(questions) && <details><summary>Answer bindings</summary>{Object.entries(questions).filter(([, q]) => isRecord(q)).map(([id, q]) => <div key={id}><code>{"${{" + nodeId + ".answers." + id + "." + ((q as {type: string}).type === "boolean" ? "probability_true" : (q as {type: string}).type === "score" ? "score" : "choice") + "}}"}</code></div>)}</details>}
    <EvaluationTest key={directoryPath + nodeId} directoryPath={directoryPath} disabled={disabled}
      definition={profileId ? {profile_id: profileId} : {questions: value.questions, provider: value.provider, model: value.model}} />
  </div>;
}
