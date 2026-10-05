import { useEffect, useState } from "react";
import "./event-payload-form.css";

export type EventSchema = Record<string, unknown>;
export type EventFailure = { code: string; path: string; message: string };
const pointer = (path: string, key: string) => `${path}/${key.replace(/~/g, "~0").replace(/\//g, "~1")}`;
const object = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const owns = (value: Record<string, unknown>, key: string) => Object.prototype.hasOwnProperty.call(value, key);

function resolvedSchema(schema: EventSchema, root: EventSchema, depth: number): EventSchema {
  if (typeof schema.$ref !== "string" || !schema.$ref.startsWith("#/") || depth > 10) return schema;
  let target: unknown = root;
  for (const part of schema.$ref.slice(2).split("/")) target = object(target)[part.replace(/~1/g, "/").replace(/~0/g, "~")];
  if (!target || typeof target !== "object") return schema;
  const { $ref: _, ...siblings } = schema;
  return resolvedSchema({ ...object(target), ...siblings }, root, depth + 1);
}

export function initialEventValue(schema: EventSchema, root = schema, depth = 0): unknown {
  schema = resolvedSchema(schema, root, depth);
  if (owns(schema, "default")) return structuredClone(schema.default);
  if (owns(schema, "const")) return structuredClone(schema.const);
  if (depth > 10) return undefined;
  const type = Array.isArray(schema.type) ? schema.type.find((item) => item !== "null") : schema.type;
  if (type === "object") {
    const fields: [string, unknown][] = [];
    for (const [key, child] of Object.entries(object(schema.properties))) {
      const field = object(child);
      if ((Array.isArray(schema.required) && schema.required.includes(key)) || owns(field, "default") || owns(field, "const")) {
        const value = initialEventValue(field, root, depth + 1);
        if (value !== undefined) fields.push([key, value]);
      }
    }
    return Object.fromEntries(fields);
  }
  if (type === "null") return null;
  if (type === "array") return [];
  if (type === "boolean") return false;
  if (type === "string" && !schema.enum) return "";
  return undefined;
}

function JsonValue({ value, disabled, onChange, onInvalid, label }: {
  value: unknown; disabled: boolean; onChange: (value: unknown) => void; onInvalid: (message: string | null) => void; label: string;
}) {
  const serialized = JSON.stringify(value === undefined ? {} : value, null, 2);
  const [text, setText] = useState(serialized);
  useEffect(() => { setText(serialized); }, [serialized]);
  return <textarea aria-label={label} rows={5} value={text} disabled={disabled} onChange={(event) => {
    setText(event.target.value);
    try { const parsed: unknown = JSON.parse(event.target.value); onInvalid(null); onChange(parsed); }
    catch { onInvalid("Enter valid JSON."); }
  }} />;
}

function Field({ schema: inputSchema, root, value, onChange, path, label, required, disabled, failures, onInvalid, depth = 0 }: {
  schema: EventSchema; root: EventSchema; value: unknown; onChange: (value: unknown) => void;
  path: string; label: string; required: boolean; disabled: boolean; failures: EventFailure[];
  onInvalid: (path: string, message: string | null, clearChildren?: boolean) => void; depth?: number;
}) {
  const [itemsRevision, setItemsRevision] = useState(0);
  const schema = resolvedSchema(inputSchema, root, 0);
  const nullable = schema.type === "null" || (Array.isArray(schema.type) && schema.type.includes("null"));
  const type = Array.isArray(schema.type) ? schema.type.find((item) => item !== "null") : schema.type;
  const title = typeof schema.title === "string" ? schema.title : label;
  const properties = object(schema.properties);
  const current = object(value);
  const complex = ["$ref", "allOf", "anyOf", "oneOf", "if", "dependentSchemas", "patternProperties", "prefixItems"].some((key) => key in schema)
    || (Array.isArray(schema.type) && schema.type.filter((item) => item !== "null").length > 1) || depth > 10;
  const errors = failures.filter((failure) => failure.path === path);
  const set = (next: unknown) => { onInvalid(path, null); onChange(next); };
  let control;
  if (nullable && value === null) control = <span>Null</span>;
  else if (complex) control = <><p className="field-description">This field uses a complex schema. Enter its value as JSON.</p><JsonValue label={`${title} JSON`} value={value} disabled={disabled} onChange={set} onInvalid={(message) => onInvalid(path, message)} /></>;
  else if (Array.isArray(schema.enum)) control = <select aria-label={title} value={schema.enum.findIndex((option) => JSON.stringify(option) === JSON.stringify(value))} disabled={disabled} onChange={(event) => set((schema.enum as unknown[])[Number(event.target.value)])}>
    <option value={-1} disabled>Select a value</option>{schema.enum.map((option, index) => <option key={index} value={index}>{typeof option === "string" ? option : JSON.stringify(option)}</option>)}
  </select>;
  else if (type === "object") control = <>
    {Object.entries(properties).map(([key, child]) => {
      const childRequired = Array.isArray(schema.required) && schema.required.includes(key);
      const included = owns(current, key);
      const childPath = pointer(path, key);
      const update = (next: unknown) => {
        const nextObject = { ...current };
        if (next === undefined) delete nextObject[key];
        set(next === undefined ? nextObject : { ...nextObject, [key]: next });
      };
      return <div key={key}>
        {!childRequired ? <label><input type="checkbox" disabled={disabled} checked={included} onChange={(event) => {
          onInvalid(childPath, null, true);
          const initial = initialEventValue(object(child), root);
          update(event.target.checked ? initial === undefined ? "" : initial : undefined);
        }} /> Include {typeof object(child).title === "string" ? String(object(child).title) : key}</label> : null}
        {included || childRequired ? <Field schema={object(child)} root={root} value={current[key]} onChange={update} path={childPath} label={key} required={childRequired} disabled={disabled} failures={failures} onInvalid={onInvalid} depth={depth + 1} /> : null}
      </div>;
    })}
    {schema.additionalProperties !== false || Object.keys(current).some((key) => !owns(properties, key)) ? <details><summary>Additional fields</summary><JsonValue label={`${title} additional fields JSON`} value={Object.fromEntries(Object.entries(current).filter(([key]) => !owns(properties, key)))} disabled={disabled} onInvalid={(message) => onInvalid(`${path}/@additional`, message)} onChange={(extra) => {
      if (!extra || typeof extra !== "object" || Array.isArray(extra) || Object.keys(extra).some((key) => owns(properties, key))) {
        onInvalid(`${path}/@additional`, "Use an object containing only additional field names."); return;
      }
      set({ ...object(extra), ...Object.fromEntries(Object.entries(current).filter(([key]) => owns(properties, key))) });
    }} /></details> : null}
  </>;
  else if (type === "array") {
    const items = Array.isArray(value) ? value : [];
    control = <>{items.map((item, index) => <div key={`${itemsRevision}:${index}`}>
      <Field schema={object(schema.items)} root={root} value={item} onChange={(next) => set(items.map((entry, i) => i === index ? next : entry))} path={pointer(path, String(index))} label={`${title} ${index + 1}`} required disabled={disabled} failures={failures} onInvalid={onInvalid} depth={depth + 1} />
      <button type="button" disabled={disabled} onClick={() => { onInvalid(path, null, true); setItemsRevision((version) => version + 1); set(items.filter((_, i) => i !== index)); }}>Remove item</button>
    </div>)}<button type="button" disabled={disabled} onClick={() => set([...items, initialEventValue(object(schema.items), root)])}>Add {title} item</button></>;
  } else if (type === "boolean") control = <input type="checkbox" aria-label={title} checked={value === true} disabled={disabled} onChange={(event) => set(event.target.checked)} />;
  else if (type === "number" || type === "integer") control = <input aria-label={title} type="number" step={type === "integer" ? 1 : "any"} min={typeof schema.minimum === "number" ? schema.minimum : undefined} max={typeof schema.maximum === "number" ? schema.maximum : undefined} value={typeof value === "number" ? value : ""} disabled={disabled} required={required} onChange={(event) => set(event.target.value === "" ? undefined : Number(event.target.value))} />;
  else if (type === "string") control = <input aria-label={title} value={typeof value === "string" ? value : ""} disabled={disabled} required={required} minLength={typeof schema.minLength === "number" ? schema.minLength : undefined} maxLength={typeof schema.maxLength === "number" ? schema.maxLength : undefined} onChange={(event) => set(event.target.value)} />;
  else control = <JsonValue label={`${title} JSON`} value={value} disabled={disabled} onChange={set} onInvalid={(message) => onInvalid(path, message)} />;
  return <fieldset className="workflow-event-field" disabled={disabled}>
    <legend>{title}{required ? " *" : ""}</legend>
    {typeof schema.description === "string" ? <p className="field-description">{schema.description}</p> : null}
    {nullable ? <label><input type="checkbox" checked={value === null} disabled={disabled || type === "null" || schema.const === null} onChange={(event) => {
      onInvalid(path, null, true);
      const nonNullSchema: EventSchema = { ...schema, type };
      if (nonNullSchema.default === null) delete nonNullSchema.default;
      set(event.target.checked ? null : initialEventValue(nonNullSchema, root));
    }} /> Use null</label> : null}
    {control}
    {errors.map((failure, index) => <p key={index} role="alert" className="runtime-run-error">{failure.message}</p>)}
  </fieldset>;
}

export function EventPayloadForm({ schema, value, onChange, disabled, failures, onInvalid }: {
  schema: EventSchema; value: Record<string, unknown>; onChange: (value: Record<string, unknown>) => void;
  disabled: boolean; failures: EventFailure[]; onInvalid: (path: string, message: string | null, clearChildren?: boolean) => void;
}) {
  return <Field schema={schema} root={schema} value={value} onChange={(next) => {
    if (next && typeof next === "object" && !Array.isArray(next)) { onInvalid("", null); onChange(next as Record<string, unknown>); }
    else onInvalid("", "Event payload must be a JSON object.");
  }} path="" label="Event payload" required disabled={disabled} failures={failures} onInvalid={onInvalid} />;
}
