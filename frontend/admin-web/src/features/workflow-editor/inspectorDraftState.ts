import type { SetStateAction } from "react";
import type { ToolArgumentPolicyDraftRow } from "../../workflowToolArgumentPolicies";
import type { MapTemplateDraftRow } from "./mapTemplateDraft";
import type { EffectTargetKey, JsonDisplayMode, SessionListMetadataRuleDraftRow, StringMapDraftRow } from "./editorTypes";

export type InspectorDraftState = {
  inspectorNodeId: string;
  inspectorDisplayName: string;
  inspectorKnownValues: Record<string, unknown>;
  inspectorUnknownValues: Record<string, unknown>;
  inspectorJsonDrafts: Record<string, string>;
  inspectorMapTemplateDrafts: Record<string, MapTemplateDraftRow[]>;
  inspectorToolArgumentPolicyDrafts: Record<string, ToolArgumentPolicyDraftRow[]>;
  inspectorJsonDisplayModes: Record<string, JsonDisplayMode>;
  inspectorStringListDrafts: Record<string, string>;
  inspectorStringMapDrafts: Record<string, StringMapDraftRow[]>;
  inspectorSessionListMetadataRules: SessionListMetadataRuleDraftRow[];
  inspectorSessionListMetadataRawMode: boolean;
  inspectorFieldModes: Record<string, "literal" | "bind" | "template">;
  inspectorEffectTargetKey: EffectTargetKey;
  rawJsonDraft: string;
  inspectorDraftError: string | null;
};

export function initialInspectorDraft(): InspectorDraftState {
  return {
    inspectorNodeId: "",
    inspectorDisplayName: "",
    inspectorKnownValues: {},
    inspectorUnknownValues: {},
    inspectorJsonDrafts: {},
    inspectorMapTemplateDrafts: {},
    inspectorToolArgumentPolicyDrafts: {},
    inspectorJsonDisplayModes: {},
    inspectorStringListDrafts: {},
    inspectorStringMapDrafts: {},
    inspectorSessionListMetadataRules: [],
    inspectorSessionListMetadataRawMode: false,
    inspectorFieldModes: {},
    inspectorEffectTargetKey: "target",
    rawJsonDraft: "{}",
    inspectorDraftError: null,
  };
}

export type InspectorDraftAction = { type: "replace"; state: InspectorDraftState } | {
  [K in keyof InspectorDraftState]: { type: "set"; field: K; value: SetStateAction<InspectorDraftState[K]> }
}[keyof InspectorDraftState];

export function inspectorDraftReducer(state: InspectorDraftState, action: InspectorDraftAction): InspectorDraftState {
  if (action.type === "replace") return action.state;
  const value = typeof action.value === "function"
    ? (action.value as (previous: InspectorDraftState[keyof InspectorDraftState]) => InspectorDraftState[keyof InspectorDraftState])(state[action.field])
    : action.value;
  return { ...state, [action.field]: value };
}
