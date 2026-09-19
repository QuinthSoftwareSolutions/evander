import { useState } from "react";
import { isRecord } from "./classificationDraft";
import { fetchJson } from "../admin/api";

export function EvaluationTest({directoryPath, definition, disabled, mode = "evaluation"}: {
  directoryPath: string; definition: Record<string, unknown>; disabled?: boolean; mode?: "evaluation" | "switch";
}) {
  const [sample, setSample] = useState("");
  const [json, setJson] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<unknown>(null);
  const [error, setError] = useState("");
  async function run() {
    setBusy(true); setError(""); setResult(null);
    try {
      const state = json ? JSON.parse(sample) : sample;
      setResult(await fetchJson(`/api/classification/${mode === "switch" ? "switch/" : ""}evaluate?directory_path=${encodeURIComponent(directoryPath)}`,
        {method: "POST", body: JSON.stringify(mode === "switch" ? {config: {...definition, state}} : {...definition, state})}));
    } catch (e) {setError(e instanceof Error ? e.message : String(e));}
    finally {setBusy(false);}
  }
  return <details className="classification-test"><summary>Test with sample input</summary>
    <p>Tests use the configured provider and incur model usage. Samples are not saved as configuration.</p>
    <label className="field-stack"><span>Sample input</span><textarea aria-label="Sample input" value={sample} onChange={e => setSample(e.target.value)} /></label>
    <label><input type="checkbox" checked={json} onChange={e => setJson(e.target.checked)} /> Structured JSON input</label>
    <button type="button" disabled={disabled || busy || !sample} onClick={() => void run()}>{busy ? "Evaluating…" : "Test evaluation"}</button>
    {error && <p role="alert" className="error-text">{error}</p>}
    {mode === "switch" && isRecord(result) && <p role="status">Selected branch: {String(result.selected_port)} · Classifier choice: {String(result.selected_choice)} · Reason: {String(result.routing_reason)}</p>}
    {result !== null && <pre aria-label="Evaluation result">{JSON.stringify(result, null, 2)}</pre>}
  </details>;
}
