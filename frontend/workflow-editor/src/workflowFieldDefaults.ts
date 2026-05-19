import type { WorkflowEditorFieldDefinition } from "./types";

function cloneJsonValue(value: unknown): unknown {
  if (value === undefined) return undefined;
  return JSON.parse(JSON.stringify(value)) as unknown;
}

function shapeFallbackValue(field: WorkflowEditorFieldDefinition, fallback: unknown): unknown {
  if (fallback === undefined) return undefined;
  if (field.json_value_type === "array") return [];
  if (field.json_value_type === "object") return {};
  return fallback;
}

export function workflowJsonFieldFallbackValue(
  field: WorkflowEditorFieldDefinition,
  fallback: unknown,
): unknown {
  if (field.default_value !== undefined && field.default_value !== null) {
    return cloneJsonValue(field.default_value);
  }
  return cloneJsonValue(shapeFallbackValue(field, fallback));
}

export function workflowJsonFieldDraftFallback(
  field: WorkflowEditorFieldDefinition,
  fallback: unknown,
): string {
  const fallbackValue = workflowJsonFieldFallbackValue(field, fallback);
  if (fallbackValue === undefined) return "";
  return JSON.stringify(fallbackValue, null, 2);
}
