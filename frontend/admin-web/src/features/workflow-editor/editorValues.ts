import { type WorkflowEditorCatalog } from "../../types";

export function trimNonEmpty(value: string | null | undefined): string | null {
  const normalized = value?.trim() ?? "";
  return normalized ? normalized : null;
}

export function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function formatJson(value: unknown, fallback = "{}"): string {
  if (value === undefined) return fallback;
  return JSON.stringify(value, null, 2);
}

export function objectListJsonDraftKey(fieldKey: string, index: number, itemFieldKey: string): string {
  return `${fieldKey}.${index}.${itemFieldKey}`;
}

export function getCatalogEntry(catalog: WorkflowEditorCatalog, type: string) {
  return catalog.nodes.find((entry) => entry.type === type);
}

export function removeUndefinedValues(value: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(value).filter(([, entry]) => entry !== undefined));
}
