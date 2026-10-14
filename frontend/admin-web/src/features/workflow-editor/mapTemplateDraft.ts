export type MapTemplateValueKind = "binding" | "string" | "template" | "number" | "boolean" | "null" | "object" | "array";

export type MapTemplateDraftRow = {
  id: string;
  name: string;
  kind: MapTemplateValueKind;
  text: string;
};

function hasFiniteNumbers(value: unknown): boolean {
  if (typeof value === "number") return Number.isFinite(value);
  if (value && typeof value === "object") return Object.values(value).every(hasFiniteNumbers);
  return true;
}

export function parseMapTemplateJson(text: string): Record<string, unknown> {
  const value: unknown = JSON.parse(text);
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Map template must be a JSON object.");
  if (!hasFiniteNumbers(value)) throw new Error("Map values must contain finite numbers.");
  return value as Record<string, unknown>;
}

export function createMapTemplateDraftRow(name = "", value: unknown = ""): MapTemplateDraftRow {
  let kind: MapTemplateValueKind = "string";
  if (value === null) kind = "null";
  else if (Array.isArray(value)) kind = "array";
  else if (typeof value === "object") kind = "object";
  else if (typeof value === "number") kind = "number";
  else if (typeof value === "boolean") kind = "boolean";
  else if (typeof value === "string" && /^\$\{\{[\s\S]*\}\}$/.test(value)) kind = "binding";
  else if (typeof value === "string" && value.includes("${{")) kind = "template";
  return {
    id: `map-${Date.now()}-${Math.random().toString(36).slice(2)}`,
    name,
    kind,
    text: kind === "object" || kind === "array" ? JSON.stringify(value, null, 2) : String(value),
  };
}

export function mapTemplateDraftRowsFromValue(value: unknown): MapTemplateDraftRow[] | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  // Existing unusual output names remain editable through the JSON view.
  if (Object.keys(value).some(name => !name.trim())) return null;
  return Object.entries(value).map(([name, entry]) => createMapTemplateDraftRow(name, entry));
}

export function mapTemplateDraftRowsToValue(rows: MapTemplateDraftRow[]): {
  value: Record<string, unknown> | null;
  errors: { rowId: string; message: string }[];
} {
  const errors: { rowId: string; message: string }[] = [];
  const names = new Set<string>();
  const entries: [string, unknown][] = [];
  for (const row of rows) {
    if (!row.name.trim()) errors.push({ rowId: row.id, message: "Enter an output name." });
    else if (names.has(row.name)) errors.push({ rowId: row.id, message: `Duplicate output name '${row.name}'.` });
    names.add(row.name);
    let value: unknown = row.text;
    try {
      if (row.kind === "binding" && !/^\$\{\{[\s\S]+\}\}$/.test(row.text)) throw new Error("Choose an upstream output or enter an exact binding reference.");
      if (row.kind === "number") {
        value = JSON.parse(row.text);
        if (typeof value !== "number" || !Number.isFinite(value)) throw new Error("Enter a finite number.");
      }
      if (row.kind === "boolean") {
        if (row.text !== "true" && row.text !== "false") throw new Error("Choose true or false.");
        value = row.text === "true";
      }
      if (row.kind === "null") value = null;
      if (row.kind === "object" || row.kind === "array") {
        value = JSON.parse(row.text);
        if (row.kind === "array" ? !Array.isArray(value) : !value || typeof value !== "object" || Array.isArray(value)) throw new Error(`Enter a JSON ${row.kind}.`);
        if (!hasFiniteNumbers(value)) throw new Error("Use finite numbers in JSON values.");
      }
      entries.push([row.name, value]);
    } catch {
      const message = row.kind === "number" ? "Enter a finite number."
        : row.kind === "binding" ? "Choose an upstream output or enter an exact binding reference."
        : row.kind === "boolean" ? "Choose true or false."
        : `Enter a valid JSON ${row.kind}.`;
      errors.push({ rowId: row.id, message });
    }
  }
  return { value: errors.length ? null : Object.fromEntries(entries), errors };
}
