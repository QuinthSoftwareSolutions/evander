import { type Dispatch, type SetStateAction } from "react";
import { type WorkflowEditorFieldDefinition, type WorkflowNodeConfig } from "../../types";
import { workflowJsonFieldFallbackValue } from "../../workflowFieldDefaults";
import {
  type ToolArgumentPolicyDraftRow,
  toolArgumentPolicyDraftRowsFromValue,
  toolArgumentPolicyDraftRowsToValue,
} from "../../workflowToolArgumentPolicies";
import { workflowTriggerEnabledValue } from "../../workflowTriggerState";
import { type WorkflowTutorialRequiredAction } from "../../workflowTutorials";
import {
  ADO_QUERY_MODE_MANUAL_WIQL,
  ADO_QUERY_MODE_STRUCTURED_FILTERS,
  ADO_STRUCTURED_FILTER_FIELD_KEYS,
  AGENT_CREATE_NODE_TYPE,
  EFFECT_TARGET_KEYS,
  SESSION_LIST_METADATA_FIELD_KEYS,
} from "./editorConstants";
import {
  type EffectTargetKey,
  type InspectorDraftSnapshot,
  type JsonDisplayMode,
  type SessionListMetadataRuleDraftRow,
  type StringMapDraftRow,
} from "./editorTypes";
import { isRecord } from "./editorValues";
import {
  createStringMapDraftRow,
  enumLiteralFallbackValue,
  fieldValueMode,
  isExactBindingValue,
  parseJsonDraft,
  pruneAdoQueryModeHiddenFieldValues,
  sessionListMetadataRuleDraftRowsFromValues,
  sessionListMetadataRuleValues,
  supportsStructuredToolArgumentPolicies,
} from "./fieldModels";

type NodeFieldActionsInputs = {
  selectedNode: WorkflowNodeConfig | undefined;
  inspectorKnownValues: Record<string, unknown>;
  setInspectorKnownValues: Dispatch<SetStateAction<Record<string, unknown>>>;
  stageInspectorDraft: (nextDraft?: Partial<InspectorDraftSnapshot>) => void;
  inspectorJsonDrafts: Record<string, string>;
  setInspectorJsonDrafts: Dispatch<SetStateAction<Record<string, string>>>;
  tutorialCurrentRequiredAction: () => WorkflowTutorialRequiredAction | null;
  isTutorialMode: boolean;
  completeTutorialAction: (action: WorkflowTutorialRequiredAction) => boolean;
  inspectorUnknownValues: Record<string, unknown>;
  setInspectorUnknownValues: Dispatch<SetStateAction<Record<string, unknown>>>;
  inspectorStringListDrafts: Record<string, string>;
  setInspectorStringListDrafts: Dispatch<SetStateAction<Record<string, string>>>;
  inspectorToolArgumentPolicyDrafts: Record<string, ToolArgumentPolicyDraftRow[]>;
  setInspectorToolArgumentPolicyDrafts: Dispatch<SetStateAction<Record<string, ToolArgumentPolicyDraftRow[]>>>;
  inspectorJsonDisplayModes: Record<string, JsonDisplayMode>;
  setInspectorJsonDisplayModes: Dispatch<SetStateAction<Record<string, JsonDisplayMode>>>;
  setInspectorDraftError: Dispatch<SetStateAction<string | null>>;
  setError: Dispatch<SetStateAction<string | null>>;
  setInspectorFieldModes: Dispatch<SetStateAction<Record<string, "literal" | "bind" | "template">>>;
  selectedNodeBindingOptions: import("../../workflowNodeOutputs").WorkflowBindingOption[];
  setCopiedBindingExpression: Dispatch<SetStateAction<string | null>>;
  setOutputCopyError: Dispatch<SetStateAction<string | null>>;
  effectTargetKey: "target" | "session_id" | "vm_name";
  inspectorFieldModes: Record<string, "literal" | "bind" | "template">;
  effectTargetField: WorkflowEditorFieldDefinition | undefined;
  setInspectorEffectTargetKey: Dispatch<SetStateAction<"target" | "session_id" | "vm_name">>;
  workflowMutationLocked: boolean;
  inspectorStringMapDrafts: Record<string, StringMapDraftRow[]>;
  setInspectorStringMapDrafts: Dispatch<SetStateAction<Record<string, StringMapDraftRow[]>>>;
  setInspectorSessionListMetadataRules: Dispatch<SetStateAction<SessionListMetadataRuleDraftRow[]>>;
  setInspectorSessionListMetadataRawMode: Dispatch<SetStateAction<boolean>>;
  inspectorSessionListMetadataRules: SessionListMetadataRuleDraftRow[];
};

/** Binds node field actions to the current render state. */
export function createNodeFieldActions({
  selectedNode,
  inspectorKnownValues,
  setInspectorKnownValues,
  stageInspectorDraft,
  inspectorJsonDrafts,
  setInspectorJsonDrafts,
  tutorialCurrentRequiredAction,
  isTutorialMode,
  completeTutorialAction,
  inspectorUnknownValues,
  setInspectorUnknownValues,
  inspectorStringListDrafts,
  setInspectorStringListDrafts,
  inspectorToolArgumentPolicyDrafts,
  setInspectorToolArgumentPolicyDrafts,
  inspectorJsonDisplayModes,
  setInspectorJsonDisplayModes,
  setInspectorDraftError,
  setError,
  setInspectorFieldModes,
  selectedNodeBindingOptions,
  setCopiedBindingExpression,
  setOutputCopyError,
  effectTargetKey,
  inspectorFieldModes,
  effectTargetField,
  setInspectorEffectTargetKey,
  workflowMutationLocked,
  inspectorStringMapDrafts,
  setInspectorStringMapDrafts,
  setInspectorSessionListMetadataRules,
  setInspectorSessionListMetadataRawMode,
  inspectorSessionListMetadataRules,
}: NodeFieldActionsInputs) {
  function updateSelectedTriggerEnabled(nextEnabled: boolean) {
    if (!selectedNode || !selectedNode.type.startsWith("trigger:")) return;
    const next = { ...inspectorKnownValues, enabled: workflowTriggerEnabledValue(nextEnabled) };
    if (next.enabled === undefined) {
      delete next.enabled;
    }
    setInspectorKnownValues(next);
    stageInspectorDraft({ knownValues: next });
  }

  function updateKnownField(key: string, value: unknown) {
    const next = { ...inspectorKnownValues, [key]: value };
    if (selectedNode?.type === AGENT_CREATE_NODE_TYPE && key === "worker_id") {
      delete next.subscription_profile_id;
      delete next.container_image;
    }
    let nextJsonDrafts = inspectorJsonDrafts;
    if (selectedNode?.type === "gate:filter") {
      if (key === "match" && value !== undefined && value !== null && value !== "") {
        delete next.when;
        nextJsonDrafts = { ...inspectorJsonDrafts, when: "" };
      }
      if (key === "when" && value !== undefined && value !== null && value !== "") {
        delete next.match;
        nextJsonDrafts = { ...inspectorJsonDrafts, match: "" };
      }
    }
    setInspectorKnownValues(next);
    if (nextJsonDrafts !== inspectorJsonDrafts) {
      setInspectorJsonDrafts(nextJsonDrafts);
    }
    stageInspectorDraft({ knownValues: next, jsonDrafts: nextJsonDrafts });
    const expectedAction = tutorialCurrentRequiredAction();
    if (
      isTutorialMode &&
      expectedAction?.kind === "bind-field" &&
      selectedNode &&
      expectedAction.nodeId === selectedNode.id &&
      expectedAction.fieldKey === key &&
      String(value) === expectedAction.bindingExpression
    ) {
      completeTutorialAction({
        kind: "bind-field",
        nodeId: selectedNode.id,
        fieldKey: key,
        bindingExpression: expectedAction.bindingExpression,
      });
    }
  }

  function updateClassificationFields(patch: Record<string, unknown>, answers?: Record<string, unknown>) {
    const knownValues = {...inspectorKnownValues, ...patch};
    const jsonDrafts = {...inspectorJsonDrafts};
    if ("cases" in patch) jsonDrafts.cases = JSON.stringify(patch.cases ?? [], null, 2);
    if ("questions" in patch) {
      jsonDrafts.questions = typeof patch.questions === "string" ? patch.questions : JSON.stringify(patch.questions ?? {}, null, 2);
      if (patch.questions === undefined) jsonDrafts.questions = "";
    }
    const unknownValues = answers ? {...inspectorUnknownValues, outputs: {
      ...(isRecord(inspectorUnknownValues.outputs) ? inspectorUnknownValues.outputs : {}), answers,
    }} : inspectorUnknownValues;
    setInspectorKnownValues(knownValues);
    setInspectorJsonDrafts(jsonDrafts);
    setInspectorUnknownValues(unknownValues);
    stageInspectorDraft({knownValues, jsonDrafts, unknownValues});
  }

  function updateAdoQueryModeField(value: string) {
    const next = { ...inspectorKnownValues };
    if (value) {
      next.query_mode = value;
    } else {
      delete next.query_mode;
    }
    if (value === ADO_QUERY_MODE_STRUCTURED_FILTERS) {
      delete next.wiql;
    }
    if (value === ADO_QUERY_MODE_MANUAL_WIQL) {
      for (const fieldKey of ADO_STRUCTURED_FILTER_FIELD_KEYS) {
        delete next[fieldKey];
      }
    }
    const nextKnownValues = pruneAdoQueryModeHiddenFieldValues(selectedNode?.type, next);
    let nextStringListDrafts = inspectorStringListDrafts;
    if (value === ADO_QUERY_MODE_MANUAL_WIQL) {
      nextStringListDrafts = { ...inspectorStringListDrafts };
      for (const fieldKey of ADO_STRUCTURED_FILTER_FIELD_KEYS) {
        delete nextStringListDrafts[fieldKey];
      }
    }
    setInspectorKnownValues(nextKnownValues);
    if (nextStringListDrafts !== inspectorStringListDrafts) {
      setInspectorStringListDrafts(nextStringListDrafts);
    }
    stageInspectorDraft({
      knownValues: nextKnownValues,
      ...(nextStringListDrafts !== inspectorStringListDrafts ? { stringListDrafts: nextStringListDrafts } : {}),
    });
  }

  function updateToolArgumentPolicyDraftRows(fieldKey: string, nextRows: ToolArgumentPolicyDraftRow[]) {
    const nextToolArgumentPolicyDrafts = { ...inspectorToolArgumentPolicyDrafts, [fieldKey]: nextRows };
    const validation = toolArgumentPolicyDraftRowsToValue(nextRows);
    const nextJsonDrafts = validation.value
      ? { ...inspectorJsonDrafts, [fieldKey]: JSON.stringify(validation.value, null, 2) }
      : inspectorJsonDrafts;
    setInspectorToolArgumentPolicyDrafts(nextToolArgumentPolicyDrafts);
    if (validation.value) {
      setInspectorJsonDrafts(nextJsonDrafts);
    }
    stageInspectorDraft({
      toolArgumentPolicyDrafts: nextToolArgumentPolicyDrafts,
      ...(validation.value ? { jsonDrafts: nextJsonDrafts } : {}),
    });
  }

  function updateToolArgumentPolicyJsonDisplayMode(
    field: WorkflowEditorFieldDefinition,
    nextMode: JsonDisplayMode,
  ) {
    if (!supportsStructuredToolArgumentPolicies(selectedNode?.type) || field.key !== "tool_argument_policies") {
      return;
    }
    const currentMode = inspectorJsonDisplayModes[field.key] ?? "structured";
    if (currentMode === nextMode) return;

    if (nextMode === "structured") {
      try {
        const fallbackValue = workflowJsonFieldFallbackValue(field, {});
        const parsedValue = parseJsonDraft(inspectorJsonDrafts[field.key] ?? "", fallbackValue);
        const structuredRows = toolArgumentPolicyDraftRowsFromValue(parsedValue, field.options.map((option) => option.value));
        if (!structuredRows) return;
        const validation = toolArgumentPolicyDraftRowsToValue(structuredRows);
        if (validation.errors.length > 0 || !validation.value) return;
        const nextToolArgumentPolicyDrafts = { ...inspectorToolArgumentPolicyDrafts, [field.key]: structuredRows };
        const nextJsonDrafts = { ...inspectorJsonDrafts, [field.key]: JSON.stringify(validation.value, null, 2) };
        setInspectorToolArgumentPolicyDrafts(nextToolArgumentPolicyDrafts);
        setInspectorJsonDrafts(nextJsonDrafts);
        setInspectorJsonDisplayModes((current) => ({ ...current, [field.key]: "structured" }));
        setInspectorDraftError(null);
        setError(null);
      } catch {
        return;
      }
      return;
    }

    const currentRows = inspectorToolArgumentPolicyDrafts[field.key] ?? [];
    const validation = toolArgumentPolicyDraftRowsToValue(currentRows);
    if (validation.value) {
      setInspectorJsonDrafts((current) => ({
        ...current,
        [field.key]: JSON.stringify(validation.value, null, 2),
      }));
    }
    setInspectorJsonDisplayModes((current) => ({ ...current, [field.key]: "raw" }));
    setInspectorDraftError(null);
    setError(null);
  }

  function mutateToolArgumentPolicyDraftRows(
    fieldKey: string,
    updater: (rows: ToolArgumentPolicyDraftRow[]) => ToolArgumentPolicyDraftRow[],
  ) {
    const nextRows = updater(inspectorToolArgumentPolicyDrafts[fieldKey] ?? []);
    updateToolArgumentPolicyDraftRows(fieldKey, nextRows);
  }

  function updateFieldMode(key: string, mode: "literal" | "bind" | "template") {
    setInspectorFieldModes((current) => ({ ...current, [key]: mode }));
  }

  function updateBindableEnumFieldMode(
    field: WorkflowEditorFieldDefinition,
    value: unknown,
    nextMode: "literal" | "bind" | "template",
  ) {
    updateFieldMode(field.key, nextMode);
    if (nextMode === "bind") {
      updateKnownField(field.key, selectedNodeBindingOptions[0]?.value ?? "");
    } else {
      updateKnownField(field.key, enumLiteralFallbackValue(field, value));
    }
  }

  function updateMetadataFieldMode(
    field: WorkflowEditorFieldDefinition,
    value: unknown,
    nextMode: "literal" | "bind" | "template",
  ) {
    updateFieldMode(field.key, nextMode);
    if (nextMode === "bind") {
      updateKnownField(field.key, selectedNodeBindingOptions[0]?.value ?? "");
    } else if (isExactBindingValue(value)) {
      updateKnownField(field.key, undefined);
    }
  }

  async function copyBindingExpression(bindingExpression: string) {
    try {
      if (!navigator.clipboard?.writeText) {
        throw new Error("Clipboard API is not available in this browser.");
      }
      await navigator.clipboard.writeText(bindingExpression);
      setCopiedBindingExpression(bindingExpression);
      setOutputCopyError(null);
    } catch (reason) {
      setCopiedBindingExpression(null);
      setOutputCopyError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  function updateEffectTargetKey(nextKey: EffectTargetKey) {
    const currentKey = effectTargetKey;
    const currentValue = inspectorKnownValues[currentKey];
    const currentMode = inspectorFieldModes[currentKey]
      ?? (effectTargetField ? fieldValueMode(effectTargetField, currentValue) : "literal");
    const next = { ...inspectorKnownValues };
    for (const key of EFFECT_TARGET_KEYS) {
      delete next[key];
    }
    if (currentValue !== undefined && currentValue !== "") {
      next[nextKey] = currentValue;
    }
    const nextModes = { ...inspectorFieldModes };
    for (const key of EFFECT_TARGET_KEYS) {
      delete nextModes[key];
    }
    nextModes[nextKey] = currentMode;
    setInspectorEffectTargetKey(nextKey);
    setInspectorKnownValues(next);
    setInspectorFieldModes(nextModes);
    stageInspectorDraft({ knownValues: next, effectTargetKey: nextKey });
  }

  function updateStringListField(key: string, value: string) {
    const next = { ...inspectorStringListDrafts, [key]: value };
    setInspectorStringListDrafts(next);
    stageInspectorDraft({ stringListDrafts: next });
  }

  function addStringMapRow(key: string) {
    if (workflowMutationLocked) return;
    const next = {
      ...inspectorStringMapDrafts,
      [key]: [...(inspectorStringMapDrafts[key] ?? []), createStringMapDraftRow()],
    };
    setInspectorStringMapDrafts(next);
    stageInspectorDraft({ stringMapDrafts: next });
  }

  function updateStringMapRow(
    fieldKey: string,
    rowId: string,
    key: "key" | "value",
    value: string,
  ) {
    const next = {
      ...inspectorStringMapDrafts,
      [fieldKey]: (inspectorStringMapDrafts[fieldKey] ?? []).map((row) =>
        row.id === rowId ? { ...row, [key]: value } : row,
      ),
    };
    setInspectorStringMapDrafts(next);
    stageInspectorDraft({ stringMapDrafts: next });
  }

  function removeStringMapRow(fieldKey: string, rowId: string) {
    if (workflowMutationLocked) return;
    const next = {
      ...inspectorStringMapDrafts,
      [fieldKey]: (inspectorStringMapDrafts[fieldKey] ?? []).filter((row) => row.id !== rowId),
    };
    setInspectorStringMapDrafts(next);
    stageInspectorDraft({ stringMapDrafts: next });
  }

  function updateObjectListItem(fieldKey: string, index: number, key: string, value: unknown) {
    const currentItems = Array.isArray(inspectorKnownValues[fieldKey]) ? [...(inspectorKnownValues[fieldKey] as unknown[])] : [];
    const currentItem = isRecord(currentItems[index]) ? { ...currentItems[index] } : {};
    if (value === undefined || value === "") {
      delete currentItem[key];
    } else {
      currentItem[key] = value;
    }
    currentItems[index] = currentItem;
    const next = { ...inspectorKnownValues, [fieldKey]: currentItems };
    setInspectorKnownValues(next);
    stageInspectorDraft({ knownValues: next });
  }

  function addObjectListItem(key: string, field: WorkflowEditorFieldDefinition) {
    if (workflowMutationLocked) return;
    const currentItems = Array.isArray(inspectorKnownValues[key]) ? [...(inspectorKnownValues[key] as unknown[])] : [];
    const nextItem = Object.fromEntries(
      field.item_fields
        .filter((itemField) => itemField.required)
        .map((itemField) => [itemField.key, itemField.kind === "boolean" ? false : ""]),
    );
    currentItems.push(nextItem);
    const next = { ...inspectorKnownValues, [key]: currentItems };
    setInspectorKnownValues(next);
    stageInspectorDraft({ knownValues: next });
  }

  function removeObjectListItem(key: string, index: number) {
    if (workflowMutationLocked) return;
    const currentItems = Array.isArray(inspectorKnownValues[key]) ? [...(inspectorKnownValues[key] as unknown[])] : [];
    currentItems.splice(index, 1);
    const next = { ...inspectorKnownValues, [key]: currentItems };
    setInspectorKnownValues(next);
    stageInspectorDraft({ knownValues: next });
  }

  function updateSessionListMetadataRuleRows(nextRows: SessionListMetadataRuleDraftRow[]) {
    const metadataValues = sessionListMetadataRuleValues(nextRows);
    const nextKnownValues = { ...inspectorKnownValues };
    const nextJsonDrafts = { ...inspectorJsonDrafts };
    for (const key of SESSION_LIST_METADATA_FIELD_KEYS) {
      delete nextKnownValues[key];
      const value = metadataValues[key];
      nextJsonDrafts[key] = value === undefined ? "" : JSON.stringify(value, null, 2);
      if (value !== undefined) {
        nextKnownValues[key] = value;
      }
    }
    setInspectorSessionListMetadataRules(nextRows);
    setInspectorKnownValues(nextKnownValues);
    setInspectorJsonDrafts(nextJsonDrafts);
    setInspectorFieldModes((current) => ({
      ...current,
      metadata_equals: "literal",
      metadata_exists: "literal",
      metadata_prefix: "literal",
    }));
    setInspectorSessionListMetadataRawMode(false);
    stageInspectorDraft({
      knownValues: nextKnownValues,
      jsonDrafts: nextJsonDrafts,
    });
  }

  function mutateSessionListMetadataRuleRows(
    updater: (rows: SessionListMetadataRuleDraftRow[]) => SessionListMetadataRuleDraftRow[],
  ) {
    const derivedRows = sessionListMetadataRuleDraftRowsFromValues(inspectorKnownValues).rows;
    const currentRows = inspectorSessionListMetadataRules.length > 0
      ? inspectorSessionListMetadataRules
      : derivedRows;
    updateSessionListMetadataRuleRows(updater(currentRows));
  }

  return {
    updateFieldMode,
    updateKnownField,
    updateEffectTargetKey,
    copyBindingExpression,
    updateSelectedTriggerEnabled,
    updateStringListField,
    updateStringMapRow,
    removeStringMapRow,
    addStringMapRow,
    updateToolArgumentPolicyJsonDisplayMode,
    mutateToolArgumentPolicyDraftRows,
    mutateSessionListMetadataRuleRows,
    updateClassificationFields,
    updateBindableEnumFieldMode,
    updateAdoQueryModeField,
    removeObjectListItem,
    updateObjectListItem,
    addObjectListItem,
  };
}
