function dedupeStrings(entries: readonly string[]): string[] {
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

function encodeStringListDraftValue(entries: readonly string[]): string {
  return dedupeStrings(entries).join("\n");
}

function parseStringListDraftValue(value: string): string[] {
  return dedupeStrings(value.split("\n"));
}

export type ModelPricingRuleSummary = {
  id: string;
  provider: string;
  provider_endpoint_identifier?: string | null;
  models: string[];
  model_patterns: string[];
  effective_from: string | null;
  effective_to: string | null;
  currency: string;
  rates_per_1m_tokens: {
    input: string | null;
    cached_input: string | null;
    cache_write_5m?: string | null;
    cache_write_1h?: string | null;
    output: string | null;
  };
  batch_multiplier: string | null;
  service_tier: string | null;
  long_context: {
    input_tokens_over: number;
    input_multiplier: string | null;
    output_multiplier: string | null;
  } | null;
};

export type ModelPricingCatalogPayload = {
  version: string;
  currency: string;
  description: string | null;
  sources: string[];
  rules: ModelPricingRuleSummary[];
};

export type ModelPricingRuleDraft = {
  id: string;
  provider: string;
  providerEndpointIdentifier: string;
  modelsText: string;
  modelPatternsText: string;
  effectiveFrom: string;
  effectiveTo: string;
  inputRate: string;
  cachedInputRate: string;
  cacheWrite5mRate: string;
  cacheWrite1hRate: string;
  outputRate: string;
  batchMultiplier: string;
  serviceTier: string;
  longContextInputTokensOver: string;
  longContextInputMultiplier: string;
  longContextOutputMultiplier: string;
};

export type ModelPricingCatalogDraft = {
  version: string;
  currency: string;
  description: string;
  sources: string[];
  rules: ModelPricingRuleDraft[];
};

function optionalString(value: string): string | null {
  const trimmed = value.trim();
  return trimmed || null;
}

function decimalString(value: string, label: string, required = false): string | null {
  const trimmed = value.trim();
  if (!trimmed) {
    if (required) {
      throw new Error(`${label} is required.`);
    }
    return null;
  }
  const parsed = Number(trimmed);
  if (!Number.isFinite(parsed) || parsed < 0) {
    throw new Error(`${label} must be a non-negative number.`);
  }
  return trimmed;
}

function optionalNonNegativeInteger(value: string, label: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const parsed = Number.parseInt(trimmed, 10);
  if (!Number.isFinite(parsed) || parsed < 0 || String(parsed) !== trimmed) {
    throw new Error(`${label} must be a non-negative integer.`);
  }
  return parsed;
}

export function modelPricingRuleDraftFromPayload(rule: ModelPricingRuleSummary): ModelPricingRuleDraft {
  return {
    id: rule.id,
    provider: rule.provider || "openai",
    providerEndpointIdentifier: rule.provider_endpoint_identifier ?? "",
    modelsText: encodeStringListDraftValue(rule.models),
    modelPatternsText: encodeStringListDraftValue(rule.model_patterns),
    effectiveFrom: rule.effective_from ?? "",
    effectiveTo: rule.effective_to ?? "",
    inputRate: rule.rates_per_1m_tokens.input ?? "",
    cachedInputRate: rule.rates_per_1m_tokens.cached_input ?? "",
    cacheWrite5mRate: rule.rates_per_1m_tokens.cache_write_5m ?? "",
    cacheWrite1hRate: rule.rates_per_1m_tokens.cache_write_1h ?? "",
    outputRate: rule.rates_per_1m_tokens.output ?? "",
    batchMultiplier: rule.batch_multiplier ?? "",
    serviceTier: rule.service_tier ?? "",
    longContextInputTokensOver: rule.long_context?.input_tokens_over == null
      ? ""
      : String(rule.long_context.input_tokens_over),
    longContextInputMultiplier: rule.long_context?.input_multiplier ?? "",
    longContextOutputMultiplier: rule.long_context?.output_multiplier ?? "",
  };
}

export function modelPricingDraftFromCatalog(
  catalog: ModelPricingCatalogPayload | null,
): ModelPricingCatalogDraft {
  const sourceCatalog = catalog ?? {
    version: "",
    currency: "usd",
    description: null,
    sources: [],
    rules: [],
  };
  return {
    version: sourceCatalog.version ?? "",
    currency: sourceCatalog.currency || "usd",
    description: sourceCatalog.description ?? "",
    sources: [...(sourceCatalog.sources ?? [])],
    rules: (sourceCatalog.rules ?? []).map(modelPricingRuleDraftFromPayload),
  };
}

export function emptyModelPricingRuleDraft(index: number): ModelPricingRuleDraft {
  return {
    id: `openai:new-rule-${index}`,
    provider: "openai",
    providerEndpointIdentifier: "",
    modelsText: "",
    modelPatternsText: "",
    effectiveFrom: "",
    effectiveTo: "",
    inputRate: "",
    cachedInputRate: "",
    cacheWrite5mRate: "",
    cacheWrite1hRate: "",
    outputRate: "",
    batchMultiplier: "",
    serviceTier: "",
    longContextInputTokensOver: "",
    longContextInputMultiplier: "",
    longContextOutputMultiplier: "",
  };
}

export function modelPricingPayloadFromDraft(
  draft: ModelPricingCatalogDraft,
): ModelPricingCatalogPayload {
  const version = draft.version.trim();
  if (!version) {
    throw new Error("Pricing catalog version is required.");
  }
  const currency = draft.currency.trim().toLowerCase();
  if (currency !== "usd") {
    throw new Error("Pricing catalog currency must be usd.");
  }
  const rules = draft.rules.map((rule, index) => {
    const id = rule.id.trim();
    if (!id) {
      throw new Error(`Pricing rule ${index + 1} requires an id.`);
    }
    const models = parseStringListDraftValue(rule.modelsText);
    const modelPatterns = parseStringListDraftValue(rule.modelPatternsText);
    if (!models.length && !modelPatterns.length) {
      throw new Error(`Pricing rule ${id} requires at least one exact model or pattern.`);
    }
    for (const [patternIndex, pattern] of modelPatterns.entries()) {
      try {
        new RegExp(pattern);
      } catch (error) {
        const suffix = error instanceof Error && error.message ? `: ${error.message}` : "";
        throw new Error(
          `Pricing rule ${id} model patterns row ${patternIndex + 1} contains an invalid regular expression${suffix}`,
        );
      }
    }
    const longContextInputTokensOver = optionalNonNegativeInteger(
      rule.longContextInputTokensOver,
      `Pricing rule ${id} long-context threshold`,
    );
    const longContext =
      longContextInputTokensOver !== null ||
      rule.longContextInputMultiplier.trim() ||
      rule.longContextOutputMultiplier.trim()
        ? {
            input_tokens_over: longContextInputTokensOver ?? 0,
            input_multiplier: decimalString(
              rule.longContextInputMultiplier,
              `Pricing rule ${id} long-context input multiplier`,
            ),
            output_multiplier: decimalString(
              rule.longContextOutputMultiplier,
              `Pricing rule ${id} long-context output multiplier`,
            ),
          }
        : null;
    return {
      id,
      provider: rule.provider.trim() || "openai",
      ...(optionalString(rule.providerEndpointIdentifier) ? {
        provider_endpoint_identifier: optionalString(rule.providerEndpointIdentifier),
      } : {}),
      models,
      model_patterns: modelPatterns,
      effective_from: optionalString(rule.effectiveFrom),
      effective_to: optionalString(rule.effectiveTo),
      currency,
      rates_per_1m_tokens: {
        input: decimalString(rule.inputRate, `Pricing rule ${id} input rate`, true),
        cached_input: decimalString(
          rule.cachedInputRate,
          `Pricing rule ${id} cached input rate`,
        ),
        ...((rule.cacheWrite5mRate ?? "").trim() ? {
          cache_write_5m: decimalString(rule.cacheWrite5mRate, `Pricing rule ${id} 5-minute cache write rate`),
        } : {}),
        ...((rule.cacheWrite1hRate ?? "").trim() ? {
          cache_write_1h: decimalString(rule.cacheWrite1hRate, `Pricing rule ${id} 1-hour cache write rate`),
        } : {}),
        output: decimalString(rule.outputRate, `Pricing rule ${id} output rate`, true),
      },
      batch_multiplier: decimalString(
        rule.batchMultiplier,
        `Pricing rule ${id} batch multiplier`,
      ),
      service_tier: optionalString(rule.serviceTier),
      long_context: longContext,
    };
  });
  if (!rules.length) {
    throw new Error("Pricing catalog requires at least one rule.");
  }
  return {
    version,
    currency,
    description: optionalString(draft.description),
    sources: draft.sources.map((source) => source.trim()).filter(Boolean),
    rules,
  };
}
