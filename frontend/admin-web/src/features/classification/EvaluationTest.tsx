import { useState } from "react";
import { fetchJson } from "../admin/api";

export function EvaluationTest({directoryPath, definition, disabled}: {
  directoryPath: string; definition: Record<string, unknown>; disabled?: boolean;
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
      setResult(await fetchJson(`/api/classification/evaluate?directory_path=${encodeURIComponent(directoryPath)}`,
        {method: "POST", body: JSON.stringify({...definition, state})}));
    } catch (e) {setError(e instanceof Error ? e.message : String(e));}
    finally {setBusy(false);}
  }
  return <details className="classification-test"><summary>Test with sample input</summary>
    <p>Tests use the configured provider and incur model usage. Samples are not saved as configuration.</p>
    <label className="field-stack"><span>Sample input</span><textarea aria-label="Sample input" value={sample} onChange={e => setSample(e.target.value)} /></label>
    <label><input type="checkbox" checked={json} onChange={e => setJson(e.target.checked)} /> Structured JSON input</label>
    <button type="button" disabled={disabled || busy || !sample} onClick={() => void run()}>{busy ? "Evaluating…" : "Test evaluation"}</button>
    {error && <p role="alert" className="error-text">{error}</p>}
    {result !== null && <pre aria-label="Evaluation result">{JSON.stringify(result, null, 2)}</pre>}
  </details>;
}
