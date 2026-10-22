import type { WorkflowEditorNodeCatalogEntry, WorkflowNodeConfig } from "../../types";
import { workflowJsonFieldDraftFallback, workflowJsonFieldFallbackValue } from "../../workflowFieldDefaults";
import { toolArgumentPolicyDraftRowsFromValue, toolArgumentPolicyDraftRowsToValue, type ToolArgumentPolicyDraftRow } from "../../workflowToolArgumentPolicies";
import { mapTemplateDraftRowsFromValue, type MapTemplateDraftRow } from "./mapTemplateDraft";
import type { JsonDisplayMode, StringMapDraftRow } from "./editorTypes";
import { formatJson, isRecord, objectListJsonDraftKey } from "./editorValues";
import { activeEffectTargetKey, fieldValueMode, isExactBindingValue, sessionListMetadataRuleDraftRowsFromValues, stringMapDraftRows, supportsStructuredToolArgumentPolicies } from "./fieldModels";
import { initialInspectorDraft, type InspectorDraftState } from "./inspectorDraftState";

export function inspectorDraftFromNode(
  nextNode: WorkflowNodeConfig | undefined,
  nextCatalogEntry: WorkflowEditorNodeCatalogEntry | undefined,
): InspectorDraftState {
  if (!nextNode) {
    return initialInspectorDraft();
  }

  const extras: Record<string, unknown> = {
    ...nextNode.config,
    ...(nextNode.outputs === undefined ? {} : { outputs: nextNode.outputs }),
  };
  const knownFieldKeys = new Set((nextCatalogEntry?.fields ?? []).map((field) => field.key));
  if (nextNode.type.startsWith("trigger:")) {
    knownFieldKeys.add("enabled");
  }
  const nextKnownValues = Object.fromEntries(Object.entries(extras).filter(([key]) => knownFieldKeys.has(key)));
  const nextUnknownValues = Object.fromEntries(Object.entries(extras).filter(([key]) => !knownFieldKeys.has(key)));
  const nextJsonDrafts: Record<string, string> = {};
  const nextToolArgumentPolicyDrafts: Record<string, ToolArgumentPolicyDraftRow[]> = {};
  const nextMapTemplateDrafts: Record<string, MapTemplateDraftRow[]> = {};
  const nextJsonDisplayModes: Record<string, JsonDisplayMode> = {};
  const nextStringListDrafts: Record<string, string> = {};
  const nextStringMapDrafts: Record<string, StringMapDraftRow[]> = {};
  const nextSessionListMetadataRules = nextNode.type === "query:session:list"
    ? sessionListMetadataRuleDraftRowsFromValues(nextKnownValues).rows
    : [];
  const nextFieldModes: Record<string, "literal" | "bind" | "template"> = {};
  for (const field of nextCatalogEntry?.fields ?? []) {
    nextFieldModes[field.key] = fieldValueMode(field, nextKnownValues[field.key]);
    if (field.kind !== "json") continue;
    if (nextNode.type === "gate:map" && field.key === "template") {
      const value = nextKnownValues.template ?? {};
      const rows = mapTemplateDraftRowsFromValue(value);
      nextJsonDisplayModes.template = rows ? "structured" : "raw";
      nextMapTemplateDrafts.template = rows ?? [];
      nextJsonDrafts.template = JSON.stringify(value, null, 2);
      continue;
    }
    if (isExactBindingValue(nextKnownValues[field.key])) {
      const fallbackValue = field.required || field.key === "outputs" ? {} : undefined;
      const fallbackDraft = workflowJsonFieldDraftFallback(field, fallbackValue);
      nextJsonDrafts[field.key] = formatJson(nextKnownValues[field.key], fallbackDraft);
      if (field.key === "tool_argument_policies" && supportsStructuredToolArgumentPolicies(nextNode.type)) {
        nextJsonDisplayModes[field.key] = "raw";
      }
      continue;
    }
    if (field.key === "tool_argument_policies" && supportsStructuredToolArgumentPolicies(nextNode.type)) {
      const fieldValue = nextKnownValues[field.key] ?? workflowJsonFieldFallbackValue(field, {});
      const structuredRows = toolArgumentPolicyDraftRowsFromValue(fieldValue, field.options.map((option) => option.value));
      if (structuredRows) {
        nextToolArgumentPolicyDrafts[field.key] = structuredRows;
        nextJsonDisplayModes[field.key] = "structured";
        const validation = toolArgumentPolicyDraftRowsToValue(structuredRows);
        nextJsonDrafts[field.key] = JSON.stringify(validation.value ?? {}, null, 2);
      } else {
        nextJsonDisplayModes[field.key] = "raw";
        nextJsonDrafts[field.key] = JSON.stringify(fieldValue, null, 2);
      }
      continue;
    }
    const fallbackValue = field.required || field.key === "outputs" ? {} : undefined;
    const fallbackDraft = workflowJsonFieldDraftFallback(field, fallbackValue);
    nextJsonDrafts[field.key] = nextKnownValues[field.key] === undefined
      ? fallbackDraft
      : formatJson(nextKnownValues[field.key], fallbackDraft);
  }
  for (const field of nextCatalogEntry?.fields ?? []) {
    if (field.kind !== "object_list") continue;
    const items: unknown[] = Array.isArray(nextKnownValues[field.key]) ? (nextKnownValues[field.key] as unknown[]) : [];
    items.forEach((item: unknown, index: number) => {
      const itemValue = isRecord(item) ? item : {};
      field.item_fields.forEach((itemField) => {
        if (itemField.kind !== "json") return;
        const draftKey = objectListJsonDraftKey(field.key, index, itemField.key);
        const nestedValue = itemValue[itemField.key];
        const fallbackValue = itemField.required ? {} : undefined;
        const fallbackDraft = workflowJsonFieldDraftFallback(itemField, fallbackValue);
        nextJsonDrafts[draftKey] = nestedValue === undefined
          ? fallbackDraft
          : formatJson(nestedValue, fallbackDraft);
      });
    });
  }
  for (const field of nextCatalogEntry?.fields ?? []) {
    if (field.kind !== "string_list") continue;
    nextStringListDrafts[field.key] = Array.isArray(nextKnownValues[field.key])
      ? (nextKnownValues[field.key] as unknown[]).map((item) => String(item)).join("\n")
      : "";
  }
  for (const field of nextCatalogEntry?.fields ?? []) {
    if (field.kind !== "string_map") continue;
    nextStringMapDrafts[field.key] = stringMapDraftRows(nextKnownValues[field.key]);
  }
  return {
    ...initialInspectorDraft(),
    inspectorNodeId: nextNode.id,
    inspectorDisplayName: nextNode.display_name,
    inspectorKnownValues: nextKnownValues,
    inspectorUnknownValues: nextUnknownValues,
    inspectorJsonDrafts: nextJsonDrafts,
    inspectorToolArgumentPolicyDrafts: nextToolArgumentPolicyDrafts,
    inspectorMapTemplateDrafts: nextMapTemplateDrafts,
    inspectorJsonDisplayModes: nextJsonDisplayModes,
    inspectorStringListDrafts: nextStringListDrafts,
    inspectorStringMapDrafts: nextStringMapDrafts,
    inspectorSessionListMetadataRules: nextSessionListMetadataRules,
    inspectorSessionListMetadataRawMode: false,
    inspectorFieldModes: nextFieldModes,
    inspectorEffectTargetKey: activeEffectTargetKey(nextKnownValues),
    rawJsonDraft: JSON.stringify(extras, null, 2),
    inspectorDraftError: null,
  };
}
