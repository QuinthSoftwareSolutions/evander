import { type Dispatch, type SetStateAction } from "react";
import {
  type WorkflowConfig,
  type WorkflowEditorCatalog,
  type WorkflowEditorDocument,
  type WorkflowEditorNodeCatalogEntry,
  type WorkflowGraphMetadata,
  type WorkflowLayout,
  type WorkflowNodeConfig,
  type WorkflowOutputSchemas,
  type WorkflowValidationDiagnostic,
} from "../../types";
import { rewriteWorkflowConfigBindingReferences, workflowObjectListItemsForEdit } from "../../workflowBindings";
import { workflowCommunicationTargetInspectorState } from "../../workflowCommunicationTargets";
import { workflowJsonFieldDraftFallback, workflowJsonFieldFallbackValue } from "../../workflowFieldDefaults";
import {
  type ToolArgumentPolicyDraftRow,
  toolArgumentPolicyDraftRowsFromValue,
  toolArgumentPolicyDraftRowsToValue,
} from "../../workflowToolArgumentPolicies";
import { type WorkflowTutorialRequiredAction, type WorkflowTutorialSession } from "../../workflowTutorials";
import { AGENT_CREATE_NODE_TYPE, EFFECT_TARGET_KEYS } from "./editorConstants";
import { mapTemplateDraftRowsFromValue, mapTemplateDraftRowsToValue, parseMapTemplateJson, type MapTemplateDraftRow } from "./mapTemplateDraft";
import {
  type InspectorDraftSnapshot,
  type JsonDisplayMode,
  type SessionListMetadataRuleDraftRow,
  type StringMapDraftRow,
} from "./editorTypes";
import {
  formatJson,
  getCatalogEntry,
  isRecord,
  objectListJsonDraftKey,
  removeUndefinedValues,
} from "./editorValues";
import {
  activeEffectTargetKey,
  fieldValueMode,
  isAdoQueryModeNodeType,
  isCommunicationEffectNodeType,
  isExactBindingValue,
  parseJsonDraft,
  parseStringMapDraftRows,
  pruneAdoQueryModeHiddenFieldValues,
  pruneAgentCreateBackendFieldValues,
  sessionListMetadataRuleDraftRowsFromValues,
  stringMapDraftRows,
  supportsStructuredToolArgumentPolicies,
  toolArgumentPolicyValidationErrorSummary,
} from "./fieldModels";
import {
  cloneConfig,
  cloneLayout,
  cloneNodeConfig,
  fallbackPosition,
  kindCollectionKey,
  nodeById,
  nodeKindFromType,
  rewriteWorkflowGraphNodeIds,
  rewriteWorkflowOutputSchemaNodeIds,
} from "./graphModel";

type NodeDraftActionsInputs = {
  setInspectorDraftError: Dispatch<SetStateAction<string | null>>;
  setInspectorNodeId: Dispatch<SetStateAction<string>>;
  setInspectorDisplayName: Dispatch<SetStateAction<string>>;
  setInspectorKnownValues: Dispatch<SetStateAction<Record<string, unknown>>>;
  setInspectorUnknownValues: Dispatch<SetStateAction<Record<string, unknown>>>;
  setInspectorJsonDrafts: Dispatch<SetStateAction<Record<string, string>>>;
  setInspectorToolArgumentPolicyDrafts: Dispatch<SetStateAction<Record<string, ToolArgumentPolicyDraftRow[]>>>;
  setInspectorMapTemplateDrafts: Dispatch<SetStateAction<Record<string, MapTemplateDraftRow[]>>>;
  setInspectorJsonDisplayModes: Dispatch<SetStateAction<Record<string, JsonDisplayMode>>>;
  setInspectorStringListDrafts: Dispatch<SetStateAction<Record<string, string>>>;
  setInspectorStringMapDrafts: Dispatch<SetStateAction<Record<string, StringMapDraftRow[]>>>;
  setInspectorSessionListMetadataRules: Dispatch<SetStateAction<SessionListMetadataRuleDraftRow[]>>;
  setInspectorSessionListMetadataRawMode: Dispatch<SetStateAction<boolean>>;
  setInspectorFieldModes: Dispatch<SetStateAction<Record<string, "literal" | "bind" | "template">>>;
  setInspectorEffectTargetKey: Dispatch<SetStateAction<"target" | "session_id" | "vm_name">>;
  setRawJsonDraft: Dispatch<SetStateAction<string>>;
  selectedNode: WorkflowNodeConfig | undefined;
  hasCollapsedTargetUi: boolean;
  selectedCatalogEntry: WorkflowEditorNodeCatalogEntry | undefined;
  document: WorkflowEditorDocument | null;
  workflowMutationLocked: boolean;
  isTutorialMode: boolean;
  tutorialCurrentRequiredAction: () => WorkflowTutorialRequiredAction | null;
  activeWorkflowTutorial: WorkflowTutorialSession | null;
  activeWorkflowDocument: WorkflowEditorDocument | null;
  patchWorkflowDocument: (nextConfig: WorkflowConfig, nextLayout: WorkflowLayout, nextGraphs?: WorkflowGraphMetadata[], selectedNodeIdsAfterPatch?: string[], diagnosticsOverride?: WorkflowValidationDiagnostic[], outputSchemasOverride?: WorkflowOutputSchemas, revisionOverride?: string) => void;
  setProgrammaticSelection: (nextNodeIds: string[], preferredNodeId?: string | null) => void;
  setSelectedEdgeId: Dispatch<SetStateAction<string | null>>;
  setError: Dispatch<SetStateAction<string | null>>;
  setNodeEditOrigins: Dispatch<SetStateAction<Record<string, string>>>;
  savedDocument: WorkflowEditorDocument | null;
  nodeEditOrigins: Record<string, string>;
  deleteNodes: (nodeIds: string[]) => void;
  catalog: WorkflowEditorCatalog;
  inspectorNodeId: string;
  inspectorDisplayName: string;
  inspectorKnownValues: Record<string, unknown>;
  inspectorUnknownValues: Record<string, unknown>;
  inspectorEffectTargetKey: "target" | "session_id" | "vm_name";
  inspectorJsonDrafts: Record<string, string>;
  inspectorToolArgumentPolicyDrafts: Record<string, ToolArgumentPolicyDraftRow[]>;
  inspectorMapTemplateDrafts: Record<string, MapTemplateDraftRow[]>;
  inspectorJsonDisplayModes: Record<string, JsonDisplayMode>;
  inspectorStringListDrafts: Record<string, string>;
  inspectorStringMapDrafts: Record<string, StringMapDraftRow[]>;
};

/** Binds node draft actions to the current render state. */
export function createNodeDraftActions({
  setInspectorDraftError,
  setInspectorNodeId,
  setInspectorDisplayName,
  setInspectorKnownValues,
  setInspectorUnknownValues,
  setInspectorJsonDrafts,
  setInspectorToolArgumentPolicyDrafts,
  setInspectorMapTemplateDrafts,
  setInspectorJsonDisplayModes,
  setInspectorStringListDrafts,
  setInspectorStringMapDrafts,
  setInspectorSessionListMetadataRules,
  setInspectorSessionListMetadataRawMode,
  setInspectorFieldModes,
  setInspectorEffectTargetKey,
  setRawJsonDraft,
  selectedNode,
  hasCollapsedTargetUi,
  selectedCatalogEntry,
  document,
  workflowMutationLocked,
  isTutorialMode,
  tutorialCurrentRequiredAction,
  activeWorkflowTutorial,
  activeWorkflowDocument,
  patchWorkflowDocument,
  setProgrammaticSelection,
  setSelectedEdgeId,
  setError,
  setNodeEditOrigins,
  savedDocument,
  nodeEditOrigins,
  deleteNodes,
  catalog,
  inspectorNodeId,
  inspectorDisplayName,
  inspectorKnownValues,
  inspectorUnknownValues,
  inspectorEffectTargetKey,
  inspectorJsonDrafts,
  inspectorToolArgumentPolicyDrafts,
  inspectorMapTemplateDrafts,
  inspectorJsonDisplayModes,
  inspectorStringListDrafts,
  inspectorStringMapDrafts,
}: NodeDraftActionsInputs) {
  function hydrateInspectorDraftFromNode(
    nextNode: WorkflowNodeConfig | undefined,
    nextCatalogEntry: WorkflowEditorNodeCatalogEntry | undefined,
  ) {
    if (!nextNode) {
      setInspectorDraftError(null);
      setInspectorNodeId("");
      setInspectorDisplayName("");
      setInspectorKnownValues({});
      setInspectorUnknownValues({});
      setInspectorJsonDrafts({});
      setInspectorToolArgumentPolicyDrafts({});
      setInspectorMapTemplateDrafts({});
      setInspectorJsonDisplayModes({});
      setInspectorStringListDrafts({});
      setInspectorStringMapDrafts({});
      setInspectorSessionListMetadataRules([]);
      setInspectorSessionListMetadataRawMode(false);
      setInspectorFieldModes({});
      setInspectorEffectTargetKey("target");
      setRawJsonDraft("{}");
      return;
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
    setInspectorNodeId(nextNode.id);
    setInspectorDisplayName(nextNode.display_name);
    setInspectorKnownValues(nextKnownValues);
    setInspectorUnknownValues(nextUnknownValues);
    setInspectorJsonDrafts(nextJsonDrafts);
    setInspectorToolArgumentPolicyDrafts(nextToolArgumentPolicyDrafts);
    setInspectorMapTemplateDrafts(nextMapTemplateDrafts);
    setInspectorJsonDisplayModes(nextJsonDisplayModes);
    setInspectorStringListDrafts(nextStringListDrafts);
    setInspectorStringMapDrafts(nextStringMapDrafts);
    setInspectorSessionListMetadataRules(nextSessionListMetadataRules);
    setInspectorSessionListMetadataRawMode(false);
    setInspectorFieldModes(nextFieldModes);
    setInspectorEffectTargetKey(activeEffectTargetKey(nextKnownValues));
    setRawJsonDraft(JSON.stringify(extras, null, 2));
    setInspectorDraftError(null);
  }

  function materializeInspectorNodeFromDraft(draft: InspectorDraftSnapshot): WorkflowNodeConfig {
    if (!selectedNode) throw new Error("No node is selected.");
    const rawId = selectedNode.id;
    const rawName = draft.displayName.trim();
    if (!rawName) throw new Error("Display name is required.");

    let nextKnownValues = { ...draft.knownValues };
    if (selectedNode.type.startsWith("trigger:") && nextKnownValues.enabled !== false) {
      delete nextKnownValues.enabled;
    }
    if (hasCollapsedTargetUi) {
      const activeTargetKey = draft.effectTargetKey ?? activeEffectTargetKey(nextKnownValues);
      for (const key of EFFECT_TARGET_KEYS) {
        if (key !== activeTargetKey) {
          delete nextKnownValues[key];
        }
      }
    }
    for (const field of selectedCatalogEntry?.fields ?? []) {
      if (field.kind !== "json") continue;
      if (field.read_only) continue;
      if (selectedNode.type === "gate:map" && field.key === "template") {
        if (draft.jsonDisplayModes.template === "structured") {
          const validation = mapTemplateDraftRowsToValue(draft.mapTemplateDrafts.template ?? []);
          if (!validation.value) throw new Error(validation.errors.map(error => error.message).join(" "));
          nextKnownValues.template = validation.value;
        } else {
          nextKnownValues.template = parseMapTemplateJson(draft.jsonDrafts.template ?? "{}");
        }
        continue;
      }
      if (isExactBindingValue(nextKnownValues[field.key])) continue;
      if (field.key === "tool_argument_policies" && supportsStructuredToolArgumentPolicies(selectedNode.type)) {
        if (draft.jsonDisplayModes[field.key] === "structured") {
          const validation = toolArgumentPolicyDraftRowsToValue(draft.toolArgumentPolicyDrafts[field.key] ?? []);
          if (validation.errors.length > 0 || !validation.value) {
            throw new Error(toolArgumentPolicyValidationErrorSummary(validation.errors));
          }
          nextKnownValues[field.key] = validation.value;
          continue;
        }
        const fallbackValue = workflowJsonFieldFallbackValue(field, {});
        nextKnownValues[field.key] = parseJsonDraft(draft.jsonDrafts[field.key] ?? "", fallbackValue);
        continue;
      }
      const fallbackValue = workflowJsonFieldFallbackValue(field, field.required ? {} : undefined);
      nextKnownValues[field.key] = parseJsonDraft(draft.jsonDrafts[field.key] ?? "", fallbackValue);
    }
    for (const field of selectedCatalogEntry?.fields ?? []) {
      if (field.kind !== "string_list") continue;
      nextKnownValues[field.key] = (draft.stringListDrafts[field.key] ?? "")
        .split("\n")
        .map((item) => item.trim())
        .filter(Boolean);
    }
    for (const field of selectedCatalogEntry?.fields ?? []) {
      if (field.kind !== "string_map") continue;
      if (isExactBindingValue(nextKnownValues[field.key])) continue;
      if (field.read_only) continue;
      nextKnownValues[field.key] = parseStringMapDraftRows(
        draft.stringMapDrafts[field.key] ?? [],
        field.label,
        field.required,
      );
    }
    for (const field of selectedCatalogEntry?.fields ?? []) {
      if (field.kind !== "object_list") continue;
      const currentItems = workflowObjectListItemsForEdit(
        nextKnownValues[field.key],
        field.bindable,
      );
      if (currentItems === null) continue;
      field.item_fields.forEach((itemField) => {
        if (itemField.kind !== "json") return;
        currentItems.forEach((item, index) => {
          if (!isRecord(item)) return;
          const draftKey = objectListJsonDraftKey(field.key, index, itemField.key);
          const currentValue = item[itemField.key];
          if (isExactBindingValue(currentValue)) return;
          const fallbackValue = workflowJsonFieldFallbackValue(itemField, itemField.required ? {} : undefined);
          const parsedValue = parseJsonDraft(draft.jsonDrafts[draftKey] ?? "", fallbackValue);
          if (parsedValue === undefined) {
            delete item[itemField.key];
          } else {
            item[itemField.key] = parsedValue;
          }
        });
      });
      nextKnownValues[field.key] = currentItems;
    }

    if (isCommunicationEffectNodeType(selectedNode.type)) {
      const communicationTargetField = selectedCatalogEntry?.fields.find((field) => field.key === "communication_target_id");
      const selectedTargetValue = typeof nextKnownValues.communication_target_id === "string"
        ? nextKnownValues.communication_target_id
        : "";
      const selectedTargetOption = communicationTargetField?.options.find((option) => option.value === selectedTargetValue);
      const threadField = selectedCatalogEntry?.fields.find((field) => field.key === "thread_id");
      const threadMode = threadField
        ? fieldValueMode(threadField, nextKnownValues.thread_id)
        : "literal";
      const communicationState = workflowCommunicationTargetInspectorState(
        selectedNode.type,
        selectedTargetOption,
        nextKnownValues.thread_id,
        threadMode,
      );
      if (communicationState?.validation?.blocked) {
        throw new Error(communicationState.validation.message);
      }
    }
    if (selectedNode.type === AGENT_CREATE_NODE_TYPE && selectedCatalogEntry) {
      nextKnownValues = pruneAgentCreateBackendFieldValues(nextKnownValues, selectedCatalogEntry.fields);
    }
    if (isAdoQueryModeNodeType(selectedNode.type)) {
      nextKnownValues = pruneAdoQueryModeHiddenFieldValues(selectedNode.type, nextKnownValues);
    }

    const values = removeUndefinedValues({
      ...draft.unknownValues,
      ...nextKnownValues,
    });
    const outputs = values.outputs;
    delete values.outputs;
    return {
      id: rawId,
      display_name: rawName,
      type: selectedNode.type,
      config: values,
      ...(isRecord(outputs) ? { outputs } : {}),
    };
  }

  function replaceSelectedNode(nextNode: WorkflowNodeConfig, originalNodeId: string) {
    if (!document || !selectedNode) return;
    if (workflowMutationLocked && !(isTutorialMode && tutorialCurrentRequiredAction()?.kind === "bind-field")) return;
    if (isTutorialMode && activeWorkflowTutorial && activeWorkflowDocument) {
      const nextConfig = cloneConfig(activeWorkflowDocument.config);
      const collectionName = kindCollectionKey(nodeKindFromType(selectedNode.type));
      nextConfig[collectionName] = nextConfig[collectionName].map((node) =>
        node.id === originalNodeId ? nextNode : node,
      );
      nextConfig.edges = nextConfig.edges.map((edge) => ({
        ...edge,
        source_id: edge.source_id === originalNodeId ? nextNode.id : edge.source_id,
        target_id: edge.target_id === originalNodeId ? nextNode.id : edge.target_id,
      }));
      const nextLayout = cloneLayout(activeWorkflowDocument.layout);
      if (nextNode.id !== originalNodeId) {
        nextLayout.nodes[nextNode.id] = nextLayout.nodes[originalNodeId] ?? fallbackPosition(0);
        delete nextLayout.nodes[originalNodeId];
      }
      patchWorkflowDocument(
        nextConfig,
        nextLayout,
        activeWorkflowDocument.graphs,
        [nextNode.id],
        undefined,
        activeWorkflowDocument.output_schemas,
        activeWorkflowDocument.revision,
      );
      setProgrammaticSelection([nextNode.id], nextNode.id);
      setSelectedEdgeId(null);
      setInspectorDraftError(null);
      setError(null);
      return;
    }
    const currentDocument = document;
    const nextConfig = cloneConfig(document.config);
    const collectionName = kindCollectionKey(nodeKindFromType(selectedNode.type));
    nextConfig[collectionName] = nextConfig[collectionName].map((node) =>
      node.id === originalNodeId ? nextNode : node,
    );
    nextConfig.edges = nextConfig.edges.map((edge) => ({
      ...edge,
      source_id: edge.source_id === originalNodeId ? nextNode.id : edge.source_id,
      target_id: edge.target_id === originalNodeId ? nextNode.id : edge.target_id,
    }));
    const nextLayout = cloneLayout(document.layout);
    let nextGraphs: WorkflowGraphMetadata[] | undefined;
    let nextOutputSchemas: WorkflowOutputSchemas | undefined;
    if (nextNode.id !== originalNodeId) {
      const nodeIdMap = new Map([[originalNodeId, nextNode.id]]);
      const rewrittenConfig = rewriteWorkflowConfigBindingReferences(nextConfig, nodeIdMap);
      nextConfig.triggers = rewrittenConfig.triggers;
      nextConfig.gates = rewrittenConfig.gates;
      nextConfig.effects = rewrittenConfig.effects;
      nextConfig.queries = rewrittenConfig.queries;
      nextGraphs = rewriteWorkflowGraphNodeIds(currentDocument.graphs ?? [], nodeIdMap);
      nextOutputSchemas = rewriteWorkflowOutputSchemaNodeIds(
        currentDocument.output_schemas ?? {},
        nodeIdMap,
      );
      nextLayout.nodes[nextNode.id] = nextLayout.nodes[originalNodeId] ?? fallbackPosition(0);
      delete nextLayout.nodes[originalNodeId];
    }
    patchWorkflowDocument(
      nextConfig,
      nextLayout,
      nextGraphs,
      [nextNode.id],
      undefined,
      nextOutputSchemas,
    );
    setProgrammaticSelection([nextNode.id], nextNode.id);
    setSelectedEdgeId(null);
    setNodeEditOrigins((current) => {
      const next = { ...current };
      const originId = current[originalNodeId] ?? (savedDocument && nodeById(savedDocument.config, originalNodeId) ? originalNodeId : null);
      delete next[originalNodeId];
      if (originId && originId !== nextNode.id) {
        next[nextNode.id] = originId;
      }
      return next;
    });
    setInspectorDraftError(null);
    setError(null);
  }

  function resetSelectedNodeChanges() {
    if (!document || !selectedNode) return;
    if (isTutorialMode) return;
    if (workflowMutationLocked && !(isTutorialMode && tutorialCurrentRequiredAction()?.kind === "bind-field")) return;
    const originalNodeId = nodeEditOrigins[selectedNode.id] ?? selectedNode.id;
    const savedWorkflowDocument = savedDocument;
    if (!savedWorkflowDocument) {
      deleteNodes([selectedNode.id]);
      return;
    }
    const savedNode = nodeById(savedWorkflowDocument.config, originalNodeId);
    if (!savedNode) {
      deleteNodes([selectedNode.id]);
      return;
    }

    const nextConfig = cloneConfig(document.config);
    const currentCollectionName = kindCollectionKey(nodeKindFromType(selectedNode.type));
    const savedCollectionName = kindCollectionKey(nodeKindFromType(savedNode.type));
    if (currentCollectionName === savedCollectionName) {
      nextConfig[currentCollectionName] = nextConfig[currentCollectionName].map((node) =>
        node.id === selectedNode.id ? cloneNodeConfig(savedNode) : node,
      );
    } else {
      nextConfig.triggers = (nextConfig.triggers ?? []).filter((node) => node.id !== selectedNode.id);
      nextConfig.gates = (nextConfig.gates ?? []).filter((node) => node.id !== selectedNode.id);
      nextConfig.effects = (nextConfig.effects ?? []).filter((node) => node.id !== selectedNode.id);
      nextConfig.queries = (nextConfig.queries ?? []).filter((node) => node.id !== selectedNode.id);
      nextConfig[savedCollectionName] = [
        ...(nextConfig[savedCollectionName] ?? []).filter((node) => node.id !== originalNodeId),
        cloneNodeConfig(savedNode),
      ];
    }
    if (selectedNode.id !== originalNodeId) {
      nextConfig.edges = nextConfig.edges.map((edge) => ({
        ...edge,
        source_id: edge.source_id === selectedNode.id ? originalNodeId : edge.source_id,
        target_id: edge.target_id === selectedNode.id ? originalNodeId : edge.target_id,
      }));
      const nodeIdMap = new Map([[selectedNode.id, originalNodeId]]);
      const rewrittenConfig = rewriteWorkflowConfigBindingReferences(nextConfig, nodeIdMap);
      nextConfig.triggers = rewrittenConfig.triggers;
      nextConfig.gates = rewrittenConfig.gates;
      nextConfig.effects = rewrittenConfig.effects;
      nextConfig.queries = rewrittenConfig.queries;
    }
    const nextLayout = cloneLayout(document.layout);
    const savedPosition = savedWorkflowDocument.layout.nodes[originalNodeId];
    nextLayout.nodes[originalNodeId] = savedPosition ? { ...savedPosition } : fallbackPosition(0);
    let nextGraphs: WorkflowGraphMetadata[] | undefined;
    let nextOutputSchemas: WorkflowOutputSchemas | undefined;
    if (selectedNode.id !== originalNodeId) {
      const nodeIdMap = new Map([[selectedNode.id, originalNodeId]]);
      nextGraphs = rewriteWorkflowGraphNodeIds(document.graphs ?? [], nodeIdMap);
      nextOutputSchemas = rewriteWorkflowOutputSchemaNodeIds(
        document.output_schemas ?? {},
        nodeIdMap,
        savedWorkflowDocument.output_schemas ?? {},
      );
      delete nextLayout.nodes[selectedNode.id];
    }
    patchWorkflowDocument(
      nextConfig,
      nextLayout,
      nextGraphs,
      [originalNodeId],
      undefined,
      nextOutputSchemas,
    );
    setProgrammaticSelection([originalNodeId], originalNodeId);
    setSelectedEdgeId(null);
    hydrateInspectorDraftFromNode(savedNode, getCatalogEntry(catalog, savedNode.type));
    setNodeEditOrigins((current) => {
      const next = { ...current };
      for (const [nodeId, originId] of Object.entries(current)) {
        if (nodeId === selectedNode.id || originId === originalNodeId) {
          delete next[nodeId];
        }
      }
      return next;
    });
    setInspectorDraftError(null);
    setError(null);
  }

  function stageInspectorDraft(nextDraft: Partial<InspectorDraftSnapshot> = {}) {
    if (!document || !selectedNode) return;
    if (workflowMutationLocked && !(isTutorialMode && tutorialCurrentRequiredAction()?.kind === "bind-field")) return;
    try {
      const nextNode = materializeInspectorNodeFromDraft({
        nodeId: nextDraft.nodeId ?? inspectorNodeId,
        displayName: nextDraft.displayName ?? inspectorDisplayName,
        knownValues: nextDraft.knownValues ?? inspectorKnownValues,
        unknownValues: nextDraft.unknownValues ?? inspectorUnknownValues,
        effectTargetKey: nextDraft.effectTargetKey ?? inspectorEffectTargetKey,
        jsonDrafts: nextDraft.jsonDrafts ?? inspectorJsonDrafts,
        toolArgumentPolicyDrafts: nextDraft.toolArgumentPolicyDrafts ?? inspectorToolArgumentPolicyDrafts,
        mapTemplateDrafts: nextDraft.mapTemplateDrafts ?? inspectorMapTemplateDrafts,
        jsonDisplayModes: nextDraft.jsonDisplayModes ?? inspectorJsonDisplayModes,
        stringListDrafts: nextDraft.stringListDrafts ?? inspectorStringListDrafts,
        stringMapDrafts: nextDraft.stringMapDrafts ?? inspectorStringMapDrafts,
      });
      replaceSelectedNode(nextNode, selectedNode.id);
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : String(reason);
      setInspectorDraftError(message);
      setError(message);
    }
  }

  return {
    hydrateInspectorDraftFromNode,
    stageInspectorDraft,
    resetSelectedNodeChanges,
  };
}
