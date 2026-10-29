import { useState } from "react";
import { type WorkflowBindingOption } from "../../workflowNodeOutputs";
import { type JsonDisplayMode } from "./editorTypes";
import { createMapTemplateDraftRow, mapTemplateDraftRowsFromValue, mapTemplateDraftRowsToValue, parseMapTemplateJson, type MapTemplateDraftRow, type MapTemplateValueKind } from "./mapTemplateDraft";

type Props = {
  rows: MapTemplateDraftRow[];
  draft: string;
  mode: JsonDisplayMode;
  disabled: boolean;
  bindingOptions: WorkflowBindingOption[];
  onRowsChange: (rows: MapTemplateDraftRow[]) => void;
  onRawChange: (text: string) => void;
  onModeChange: (mode: JsonDisplayMode, rows: MapTemplateDraftRow[], text: string) => void;
};

export function MapTemplateField({ rows, draft, mode, disabled, bindingOptions, onRowsChange, onRawChange, onModeChange }: Props) {
  const [modeError, setModeError] = useState<string | null>(null);
  const validation = mapTemplateDraftRowsToValue(rows);
  function switchMode() {
    setModeError(null);
    if (mode === "structured") {
      if (!validation.value) { setModeError("Resolve the mapping errors before switching to JSON."); return; }
      onModeChange("raw", rows, JSON.stringify(validation.value, null, 2));
    } else {
      try {
        const nextRows = mapTemplateDraftRowsFromValue(parseMapTemplateJson(draft));
        if (!nextRows) throw new Error();
        onModeChange("structured", nextRows, draft);
      } catch { setModeError("The structured editor requires an object with non-empty output names. Your JSON is preserved."); }
    }
  }
  function patch(id: string, change: Partial<MapTemplateDraftRow>) {
    setModeError(null);
    onRowsChange(rows.map(row => row.id === id ? { ...row, ...change } : row));
  }
  return <div className="inspector-field map-template-editor">
    <div className="field-toolbar"><span className="field-header">Output mapping</span><button type="button" className="subtle-button" onClick={switchMode} disabled={disabled} aria-pressed={mode === "raw"}>{mode === "raw" ? "Structured form" : "Raw JSON"}</button></div>
    {modeError ? <p className="error-box" role="alert">{modeError}</p> : null}
    {mode === "raw" ? <label className="inspector-label"><span>Map template JSON</span><textarea rows={10} value={draft} onChange={event => { setModeError(null); onRawChange(event.target.value); }} disabled={disabled} spellCheck={false} /></label> : <>
      {rows.length === 0 ? <p className="field-description">Add an output and choose its value.</p> : null}
      <div className="map-template-rows">{rows.map((row, index) => <div className="map-template-row" key={row.id}>
        <label className="inspector-label map-output-name"><span>Output name</span><input aria-label={`Output name ${index + 1}`} value={row.name} onChange={event => patch(row.id, { name: event.target.value })} disabled={disabled} /></label>
        <label className="inspector-label map-value-kind"><span>Value type</span><select aria-label={`Value type ${index + 1}`} value={row.kind} disabled={disabled} onChange={event => {
          const kind = event.target.value as MapTemplateValueKind;
          const text = kind === "binding" ? bindingOptions[0]?.value ?? "" : kind === "number" ? "0" : kind === "boolean" ? "true" : kind === "null" ? "null" : kind === "object" ? "{}" : kind === "array" ? "[]" : row.text;
          patch(row.id, { kind, text });
        }}>
          <option value="binding">Upstream binding</option><option value="string">Text</option><option value="template">Templated text</option><option value="number">Number</option><option value="boolean">Boolean</option><option value="null">Null</option><option value="object">Object</option><option value="array">Array</option>
        </select></label>
        <div className="map-output-value">
          <label className="inspector-label"><span>{row.kind === "binding" ? "Upstream output" : "Value"}</span>
            {row.kind === "binding" ? <select aria-label={`Output value ${index + 1}`} value={row.text} disabled={disabled} onChange={event => patch(row.id, { text: event.target.value })}>
              <option value="">Select an output</option>
              {row.text && !bindingOptions.some(option => option.value === row.text) ? <option value={row.text}>{row.text}</option> : null}
              {bindingOptions.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select> : row.kind === "boolean" ? <select aria-label={`Output value ${index + 1}`} value={row.text} onChange={event => patch(row.id, { text: event.target.value })} disabled={disabled}><option value="true">true</option><option value="false">false</option></select>
              : row.kind === "null" ? <input aria-label={`Output value ${index + 1}`} value="null" readOnly disabled={disabled} />
              : row.kind === "object" || row.kind === "array" ? <textarea aria-label={`Output value ${index + 1}`} rows={4} value={row.text} onChange={event => patch(row.id, { text: event.target.value })} disabled={disabled} spellCheck={false} />
              : <input aria-label={`Output value ${index + 1}`} inputMode={row.kind === "number" ? "decimal" : undefined} value={row.text} onChange={event => patch(row.id, { text: event.target.value })} disabled={disabled} />}
          </label>
          {row.kind === "binding" ? <details className="map-custom-binding"><summary>Custom reference</summary><input aria-label={`Binding reference ${index + 1}`} value={row.text} onChange={event => patch(row.id, { text: event.target.value })} disabled={disabled} /></details> : null}
          {row.kind === "template" ? <label className="field-inline"><span>Insert output</span><select aria-label={`Insert output ${index + 1}`} value="" onChange={event => { if (event.target.value) patch(row.id, { text: row.text + event.target.value }); }} disabled={disabled || !bindingOptions.length}><option value="">Choose an output</option>{bindingOptions.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label> : null}
        </div>
        <button type="button" className="subtle-button map-remove-output" aria-label={`Remove output ${index + 1}`} onClick={() => onRowsChange(rows.filter(candidate => candidate.id !== row.id))} disabled={disabled}>Remove</button>
        {validation.errors.filter(error => error.rowId === row.id).map((error, errorIndex) => <p key={errorIndex} className="map-output-error" role="alert">{error.message}</p>)}
      </div>)}</div>
      <button type="button" className="subtle-button" onClick={() => onRowsChange([...rows, createMapTemplateDraftRow()])} disabled={disabled}>Add output</button>
    </>}
  </div>;
}
