import { useState } from "react";
import { isRecord, newQuestion, nextId, renameKey, type Question, type Questions } from "./classificationDraft";
import "./classification.css";

export function QuestionsEditor({value, onChange, disabled = false}: {
  value: unknown; onChange: (value: unknown) => void; disabled?: boolean;
}) {
  const [raw, setRaw] = useState(false);
  const [error, setError] = useState("");
  const questions = (isRecord(value) ? value : {}) as Questions;
  const isBinding = (input: unknown) => typeof input === "string" && input.trim().startsWith("${{") && input.trim().endsWith("}}");
  function change(action: () => unknown) {
    try { onChange(action()); setError(""); } catch (e) { setError(String(e)); }
  }
  function update(id: string, patch: Partial<Question>) {
    onChange({...questions, [id]: {...questions[id], ...patch}});
  }
  function description(value: unknown) {return typeof value === "string" ? value : JSON.stringify(value);}
  return <div className="classification-questions">
    <button type="button" className="subtle-button" onClick={() => setRaw(!raw)}>
      {raw ? "Structured questions" : "Advanced JSON"}
    </button>
    {error && <p role="alert" className="error-text">{error}</p>}
    {raw ? <label className="field-stack"><span>Questions JSON</span><textarea aria-label="Questions JSON" rows={12} disabled={disabled}
      value={typeof value === "string" ? value : JSON.stringify(value ?? {}, null, 2)}
      onChange={e => {try {onChange(isBinding(e.target.value) ? e.target.value : JSON.parse(e.target.value)); setError("");} catch {onChange(e.target.value); setError("Enter valid JSON before saving.");}}} /></label>
      : <>
        {!isRecord(value) && value != null && <p>Use Advanced JSON to {isBinding(value) ? "edit the question binding" : "correct or bind the question definition"}.</p>}
        {Object.entries(questions).map(([id, question], index) => !isRecord(question)
          ? <p role="alert" key={index}>{id}: use Advanced JSON to enter a question object.</p>
          : <fieldset className="classification-question" key={index} disabled={disabled}>
          <legend>Question {index + 1}</legend>
          <label className="field-stack"><span>Question ID</span><input value={id} onChange={e => change(() => renameKey(questions, id, e.target.value))} /></label>
          <label className="field-stack"><span>Type</span><select aria-label="Type" value={question.type} onChange={e => {
            const next = newQuestion(e.target.value as Question["type"]);
            onChange({...questions, [id]: {...next, instructions: question.instructions}});
          }}><option value="choice">Choice</option><option value="score">Score</option><option value="boolean">Boolean probability</option></select></label>
          <label className="field-stack"><span>Instructions</span><textarea aria-label="Instructions" value={description(question.instructions) ?? ""}
            readOnly={typeof question.instructions !== "string"}
            onChange={e => update(id, {instructions: e.target.value})} /></label>
          {typeof question.instructions !== "string" && <p>Structured instructions are preserved. Edit them in Advanced JSON.</p>}
          {question.type === "choice" && isRecord(question.criteria) && <>
            {Object.entries(question.criteria).map(([option, text], optionIndex) => <div className="classification-option" key={optionIndex}>
              <label className="field-stack"><span>Option ID</span><input value={option} onChange={e => change(() => ({...questions, [id]: {...question, criteria: renameKey(question.criteria as Record<string, unknown>, option, e.target.value)}}))} /></label>
              <label className="field-stack"><span>Description</span><textarea aria-label="Option description" value={text === null ? "" : description(text)} readOnly={text !== null && typeof text !== "string"}
                onChange={e => update(id, {criteria: {...question.criteria as object, [option]: e.target.value || null}})} /></label>
              <button type="button" onClick={() => {const next = {...question.criteria as object} as Record<string, unknown>; delete next[option]; update(id, {criteria: next});}}>Remove option</button>
            </div>)}
            <button type="button" onClick={() => update(id, {criteria: {...question.criteria as object, [nextId(question.criteria as object, "option")]: ""}})}>Add option</button>
          </>}
          {question.type === "score" && Array.isArray(question.criteria) && <>
            {question.criteria.map((level, i) => <div key={i}>
              <label className="field-stack"><span>Level {i}</span><textarea value={description(level)} readOnly={typeof level !== "string"}
                onChange={e => update(id, {criteria: (question.criteria as unknown[]).map((x, j) => i === j ? e.target.value : x)})} /></label>
              <button type="button" disabled={i === 0} onClick={() => {const levels = [...question.criteria as unknown[]]; [levels[i - 1], levels[i]] = [levels[i], levels[i - 1]]; update(id, {criteria: levels});}}>Move up</button>
              <button type="button" onClick={() => update(id, {criteria: (question.criteria as unknown[]).filter((_, j) => j !== i)})}>Remove level</button>
            </div>)}
            <button type="button" onClick={() => update(id, {criteria: [...question.criteria as unknown[], ""]})}>Add level</button>
          </>}
          {question.type === "boolean" && ["true", "false"].map(key => <label className="field-stack" key={key}><span>{key} criteria (optional)</span>
            <textarea value={description(isRecord(question.criteria) ? question.criteria[key] : "") ?? ""}
              readOnly={isRecord(question.criteria) && question.criteria[key] != null && typeof question.criteria[key] !== "string"}
              onChange={e => {const criteria = {...(isRecord(question.criteria) ? question.criteria : {})}; if(e.target.value) criteria[key] = e.target.value; else delete criteria[key]; update(id, {criteria: Object.keys(criteria).length ? criteria : undefined});}} /></label>)}
          <button type="button" onClick={() => {const next = {...questions}; delete next[id]; onChange(next);}}>Remove question</button>
        </fieldset>)}
        <button type="button" disabled={disabled} onClick={() => onChange({...questions, [nextId(questions, "question")]: newQuestion("choice")})}>Add question</button>
      </>}
  </div>;
}
