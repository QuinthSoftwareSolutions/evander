export type StringMapDraftRow = {
  id: string;
  key: string;
  value: string;
};

export type JsonObjectDraftRow = {
  id: string;
  key: string;
  valueText: string;
};

export type RegexPatternDraftRow = {
  id: string;
  pattern: string;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function createDraftRowId(prefix: string): string {
  const randomId = typeof globalThis.crypto?.randomUUID === "function"
    ? globalThis.crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return `${prefix}-${randomId}`;
}

function parseJsonSnippet(valueText: string, fieldLabel: string, rowNumber: number): unknown {
  const trimmed = valueText.trim();
  if (!trimmed) {
    throw new Error(`${fieldLabel} row ${rowNumber} contains a blank JSON value.`);
  }
  try {
    return JSON.parse(trimmed);
  } catch (error) {
    const suffix = error instanceof Error && error.message ? `: ${error.message}` : "";
    throw new Error(`${fieldLabel} row ${rowNumber} contains invalid JSON${suffix}`);
  }
}

export function dedupeStrings(entries: readonly string[]): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const entry of entries) {
    const normalized = entry.trim();
    if (!normalized || seen.has(normalized)) continue;
    seen.add(normalized);
    result.push(normalized);
  }
  return result;
}

export function normalizeLeadingDotExtension(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) return "";
  const collapsed = trimmed.replace(/^\.+/, "");
  return collapsed ? `.${collapsed}` : "";
}

export function normalizeLeadingDotExtensions(entries: readonly string[]): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const entry of entries) {
    const normalized = normalizeLeadingDotExtension(entry);
    if (!normalized || seen.has(normalized)) continue;
    seen.add(normalized);
    result.push(normalized);
  }
  return result;
}

export function parseStringListDraftValue(value: string | boolean | null | undefined): string[] {
  if (typeof value === "boolean" || value == null) return [];
  return dedupeStrings(String(value).split("\n"));
}

export function encodeStringListDraftValue(entries: readonly string[]): string {
  return dedupeStrings(entries).join("\n");
}

export function formatJsonTextDraftValue(value: unknown): string {
  if (value == null) {
    return "";
  }
  return JSON.stringify(value, null, 2) ?? "";
}

export function parseJsonTextDraftValue(
  value: string | boolean | null | undefined,
  fieldLabel: string,
): unknown {
  if (typeof value === "boolean" || value == null) {
    throw new Error(`${fieldLabel} must be valid JSON.`);
  }
  const text = String(value).trim();
  if (!text) {
    throw new Error(`${fieldLabel} must be valid JSON.`);
  }
  try {
    return JSON.parse(text);
  } catch (error) {
    const suffix = error instanceof Error && error.message ? `: ${error.message}` : "";
    throw new Error(`${fieldLabel} must be valid JSON${suffix}`);
  }
}

export function validateJsonTextDraftValue(
  value: string,
  fieldLabel: string,
): string | null {
  try {
    parseJsonTextDraftValue(value, fieldLabel);
    return null;
  } catch (error) {
    return error instanceof Error ? error.message : String(error);
  }
}

export function createStringMapDraftRow(key = "", value = ""): StringMapDraftRow {
  return {
    id: createDraftRowId("map-row"),
    key,
    value,
  };
}

export function stringMapDraftRowsFromValue(value: unknown): StringMapDraftRow[] {
  if (!isRecord(value)) return [];
  return Object.entries(value).map(([key, entry]) => createStringMapDraftRow(key, String(entry ?? "")));
}

export function parseStringMapDraftRows(
  rows: readonly StringMapDraftRow[],
  fieldLabel: string,
  required = false,
): Record<string, string> | undefined {
  const result: Record<string, string> = {};
  const seen = new Set<string>();
  for (const row of rows) {
    const key = row.key.trim();
    const value = row.value;
    if (!key && !value.trim()) continue;
    if (!key) {
      throw new Error(`${fieldLabel} contains a value without a key.`);
    }
    if (seen.has(key)) {
      throw new Error(`${fieldLabel} contains duplicate key '${key}'.`);
    }
    seen.add(key);
    result[key] = value;
  }
  if (Object.keys(result).length === 0 && !required) return undefined;
  return result;
}

function formatJsonSnippet(value: unknown): string {
  return formatJsonTextDraftValue(value);
}

export function createJsonObjectDraftRow(key = "", valueText = ""): JsonObjectDraftRow {
  return {
    id: createDraftRowId("json-row"),
    key,
    valueText,
  };
}

export function jsonObjectDraftRowsFromValue(value: unknown): JsonObjectDraftRow[] {
  if (!isRecord(value)) return [];
  return Object.entries(value).map(([key, entry]) => createJsonObjectDraftRow(key, formatJsonSnippet(entry)));
}

export function parseJsonObjectDraftRows(
  rows: readonly JsonObjectDraftRow[],
  fieldLabel: string,
): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  const seen = new Set<string>();
  for (const [index, row] of rows.entries()) {
    const key = row.key.trim();
    const valueText = row.valueText.trim();
    if (!key && !valueText) continue;
    if (!key) {
      throw new Error(`${fieldLabel} row ${index + 1} contains a value without a key.`);
    }
    if (seen.has(key)) {
      throw new Error(`${fieldLabel} contains duplicate key '${key}'.`);
    }
    const parsed = parseJsonSnippet(valueText, fieldLabel, index + 1);
    seen.add(key);
    result[key] = parsed;
  }
  return result;
}

export function createRegexPatternDraftRow(pattern = ""): RegexPatternDraftRow {
  return {
    id: createDraftRowId("regex-row"),
    pattern,
  };
}

export function regexPatternDraftRowsFromValue(value: unknown): RegexPatternDraftRow[] {
  if (!Array.isArray(value)) return [];
  return value.map((entry) => createRegexPatternDraftRow(String(entry ?? "")));
}

export function validateRegexPattern(pattern: string): string | null {
  const trimmed = pattern.trim();
  if (!trimmed) return null;
  try {
    new RegExp(trimmed);
    return null;
  } catch (error) {
    const suffix = error instanceof Error && error.message ? `: ${error.message}` : "";
    return `Invalid regular expression${suffix}`;
  }
}

export function parseRegexPatternDraftRows(
  rows: readonly RegexPatternDraftRow[],
  fieldLabel: string,
  required = false,
): string[] | undefined {
  const result: string[] = [];
  const seen = new Set<string>();
  for (const [index, row] of rows.entries()) {
    const pattern = row.pattern.trim();
    if (!pattern) continue;
    const validationError = validateRegexPattern(pattern);
    if (validationError) {
      throw new Error(`${fieldLabel} row ${index + 1} ${validationError.toLowerCase()}`);
    }
    if (seen.has(pattern)) continue;
    seen.add(pattern);
    result.push(pattern);
  }
  if (result.length === 0 && !required) return undefined;
  return result;
}
