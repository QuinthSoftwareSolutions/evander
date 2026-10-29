import { useRef, useState, type Dispatch, type SetStateAction } from "react";
import { fetchJson } from "../admin/api";
import { parseStringListDraftValue, validateRegexPattern } from "../admin/structuredPolicyInputs";
import {
  emptyModelPricingRuleDraft,
  modelPricingDraftFromCatalog,
  modelPricingPayloadFromDraft,
  type ModelPricingCatalogDraft,
  type ModelPricingCatalogPayload,
  type ModelPricingRuleDraft,
  type ModelPricingRuleSummary,
} from "./modelPricingDraft";

type ModelPricingCatalogStatus = {
  status: "ok" | "error";
  path: string | null;
  path_source: "env" | "default" | string;
  env_var: string;
  version: string | null;
  currency: string | null;
  description: string | null;
  sources: string[];
  rule_count: number;
  rules: ModelPricingRuleSummary[];
  revision: string | null;
  catalog: ModelPricingCatalogPayload;
  editable: boolean;
  write_requires_privileged_unlock: boolean;
  error: string | null;
  error_class: string | null;
};

const MODEL_PRICING_FALLBACK_ERROR = {
  status: "error",
  path: null,
  path_source: "unknown",
  env_var: "EVANDER_MODEL_PRICING_PATH",
  version: null,
  currency: null,
  description: null,
  sources: [],
  rule_count: 0,
  rules: [],
  revision: null,
  catalog: {
    version: "",
    currency: "usd",
    description: null,
    sources: [],
    rules: [],
  },
  editable: false,
  write_requires_privileged_unlock: false,
  error: "Model pricing catalog status is unavailable.",
  error_class: null,
} satisfies ModelPricingCatalogStatus;

/** Global pricing drafts outlive tab changes and retain their original save revision. */
export function useModelPricingCatalog({ loading, mutationLocked, setStatusMessage }: {
  loading: boolean;
  mutationLocked: boolean;
  setStatusMessage: Dispatch<SetStateAction<string | null>>;
}) {
  const [modelPricingCatalog, setModelPricingCatalog] = useState<ModelPricingCatalogStatus | null>(null);
  const [modelPricingDraft, setModelPricingDraft] = useState<ModelPricingCatalogDraft | null>(null);
  const [modelPricingDraftBaseRevision, setModelPricingDraftBaseRevision] = useState<string | null>(null);
  const [modelPricingDirty, setModelPricingDirty] = useState(false);
  const [modelPricingSaving, setModelPricingSaving] = useState(false);
  const [modelPricingError, setModelPricingError] = useState<string | null>(null);
  const modelPricingDirtyRef = useRef(false);
  async function loadModelPricingCatalog() {
    try {
      const preserveDirtyDraft = modelPricingDirtyRef.current;
      const payload = await fetchJson<ModelPricingCatalogStatus>("/api/configuration/model-pricing");
      setModelPricingCatalog(payload);
      setModelPricingDraft((current) =>
        preserveDirtyDraft && current ? current : modelPricingDraftFromCatalog(payload.catalog),
      );
      setModelPricingDraftBaseRevision((current) =>
        preserveDirtyDraft ? current : payload.revision,
      );
      if (!preserveDirtyDraft) {
        setModelPricingError(null);
      }
    } catch (reason) {
      const preserveDirtyDraft = modelPricingDirtyRef.current;
      const fallback = {
        ...MODEL_PRICING_FALLBACK_ERROR,
        error: reason instanceof Error ? reason.message : String(reason),
      };
      setModelPricingCatalog(fallback);
      setModelPricingDraft((current) =>
        preserveDirtyDraft && current ? current : modelPricingDraftFromCatalog(fallback.catalog),
      );
      setModelPricingDraftBaseRevision((current) =>
        preserveDirtyDraft ? current : fallback.revision,
      );
    }
  }

  function updateModelPricingDraft(
    updater: (draft: ModelPricingCatalogDraft) => ModelPricingCatalogDraft,
  ) {
    setModelPricingDraft((current) =>
      updater(current ?? modelPricingDraftFromCatalog(modelPricingCatalog?.catalog ?? null)),
    );
    setModelPricingDraftBaseRevision((current) => current ?? modelPricingCatalog?.revision ?? null);
    modelPricingDirtyRef.current = true;
    setModelPricingDirty(true);
    setModelPricingError(null);
  }

  function updateModelPricingSource(index: number, value: string) {
    updateModelPricingDraft((draft) => ({
      ...draft,
      sources: draft.sources.map((source, sourceIndex) =>
        sourceIndex === index ? value : source,
      ),
    }));
  }

  function addModelPricingSource() {
    updateModelPricingDraft((draft) => ({ ...draft, sources: [...draft.sources, ""] }));
  }

  function removeModelPricingSource(index: number) {
    updateModelPricingDraft((draft) => ({
      ...draft,
      sources: draft.sources.filter((_, sourceIndex) => sourceIndex !== index),
    }));
  }

  function updateModelPricingRule(
    index: number,
    patch: Partial<ModelPricingRuleDraft>,
  ) {
    updateModelPricingDraft((draft) => ({
      ...draft,
      rules: draft.rules.map((rule, ruleIndex) =>
        ruleIndex === index ? { ...rule, ...patch } : rule,
      ),
    }));
  }

  function addModelPricingRule() {
    updateModelPricingDraft((draft) => ({
      ...draft,
      rules: [...draft.rules, emptyModelPricingRuleDraft(draft.rules.length + 1)],
    }));
  }

  function duplicateModelPricingRule(index: number) {
    updateModelPricingDraft((draft) => {
      const source = draft.rules[index] ?? emptyModelPricingRuleDraft(draft.rules.length + 1);
      return {
        ...draft,
        rules: [
          ...draft.rules,
          {
            ...source,
            id: source.id.trim() ? `${source.id.trim()}-copy` : `openai:new-rule-${draft.rules.length + 1}`,
          },
        ],
      };
    });
  }

  function deleteModelPricingRule(index: number) {
    updateModelPricingDraft((draft) => ({
      ...draft,
      rules: draft.rules.filter((_, ruleIndex) => ruleIndex !== index),
    }));
  }

  function discardModelPricingChanges() {
    setModelPricingDraft(modelPricingDraftFromCatalog(modelPricingCatalog?.catalog ?? null));
    setModelPricingDraftBaseRevision(modelPricingCatalog?.revision ?? null);
    modelPricingDirtyRef.current = false;
    setModelPricingDirty(false);
    setModelPricingError(null);
  }

  async function saveModelPricingCatalog() {
    if (!modelPricingCatalog || !modelPricingDraft) {
      setModelPricingError("Model pricing catalog is not loaded.");
      return;
    }
    const modelPricingExpectedRevision = modelPricingDraftBaseRevision;
    if (!modelPricingExpectedRevision) {
      setModelPricingError("Model pricing catalog revision is unavailable.");
      return;
    }
    if (mutationLocked) {
      setModelPricingError("Privileged operations are locked.");
      return;
    }
    let catalogPayload: ModelPricingCatalogPayload;
    try {
      catalogPayload = modelPricingPayloadFromDraft(modelPricingDraft);
    } catch (reason) {
      setModelPricingError(reason instanceof Error ? reason.message : String(reason));
      return;
    }
    setModelPricingSaving(true);
    setModelPricingError(null);
    setStatusMessage(null);
    try {
      const payload = await fetchJson<ModelPricingCatalogStatus>(
        "/api/configuration/model-pricing",
        {
          method: "PUT",
          body: JSON.stringify({
            catalog: catalogPayload,
            expected_revision: modelPricingExpectedRevision,
          }),
        },
      );
      setModelPricingCatalog(payload);
      setModelPricingDraft(modelPricingDraftFromCatalog(payload.catalog));
      setModelPricingDraftBaseRevision(payload.revision);
      modelPricingDirtyRef.current = false;
      setModelPricingDirty(false);
      setStatusMessage("Saved model pricing catalog.");
    } catch (reason) {
      setModelPricingError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setModelPricingSaving(false);
    }
  }

  const activeModelPricingDraft = modelPricingDraft ?? modelPricingDraftFromCatalog(modelPricingCatalog?.catalog ?? null);
  const modelPricingPatternError = activeModelPricingDraft.rules.flatMap((rule, ruleIndex) =>
    parseStringListDraftValue(rule.modelPatternsText).flatMap((pattern, patternIndex) => {
      const error = validateRegexPattern(pattern);
      return error ? [`Pricing rule ${rule.id || ruleIndex + 1}, model pattern ${patternIndex + 1}: ${error}`] : [];
    }),
  )[0] ?? null;
  const modelPricingPanelBodyId = "model-pricing-catalog-panel-body";
  const modelPricingSaveDisabled =
    loading ||
    modelPricingSaving ||
    mutationLocked ||
    Boolean(modelPricingPatternError) ||
    !modelPricingDirty ||
    !modelPricingCatalog?.editable ||
    !modelPricingDraftBaseRevision;

  return { modelPricingCatalog, modelPricingDraft, modelPricingDraftBaseRevision, modelPricingDirty, modelPricingSaving, modelPricingError, loadModelPricingCatalog, updateModelPricingDraft, updateModelPricingSource, addModelPricingSource, removeModelPricingSource, updateModelPricingRule, addModelPricingRule, duplicateModelPricingRule, deleteModelPricingRule, discardModelPricingChanges, saveModelPricingCatalog, activeModelPricingDraft, modelPricingPatternError, modelPricingPanelBodyId, modelPricingSaveDisabled };
}
