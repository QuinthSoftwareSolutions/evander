import assert from "node:assert/strict";
import { test } from "node:test";

import {
  modelPricingDraftFromCatalog,
  modelPricingPayloadFromDraft,
} from "../src/features/configuration/modelPricingDraft.ts";

test("model pricing drafts hydrate and validate structured model rows", () => {
  const draft = modelPricingDraftFromCatalog({
    version: "2026.06",
    currency: "usd",
    description: null,
    sources: ["https://example.invalid/pricing.json"],
    rules: [
      {
        id: "rule-1",
        provider: "openai",
        provider_endpoint_identifier: "decisions",
        models: [" gpt-4o ", "gpt-4o-mini", "gpt-4o-mini"],
        model_patterns: ["^gpt-4o.*$"],
        effective_from: null,
        effective_to: null,
        currency: "usd",
        rates_per_1m_tokens: {
          input: "1.25",
          cached_input: null,
          cache_write_5m: "0",
          cache_write_1h: "0",
          output: "2.5",
        },
        batch_multiplier: null,
        service_tier: null,
        long_context: null,
      },
    ],
  });

  assert.equal(draft.rules[0].modelsText, "gpt-4o\ngpt-4o-mini");
  assert.equal(draft.rules[0].modelPatternsText, "^gpt-4o.*$");

  draft.rules[0].inputRate = "0.20";
  const payload = modelPricingPayloadFromDraft(draft);
  assert.equal(payload.rules[0].provider_endpoint_identifier, "decisions");
  assert.equal(payload.rules[0].rates_per_1m_tokens.input, "0.20");
  assert.equal(payload.rules[0].rates_per_1m_tokens.cache_write_5m, "0");
  assert.equal(payload.rules[0].rates_per_1m_tokens.cache_write_1h, "0");
  assert.deepEqual(payload.rules[0].models, ["gpt-4o", "gpt-4o-mini"]);
  assert.deepEqual(payload.rules[0].model_patterns, ["^gpt-4o.*$"]);
});

test("model pricing payload validation names the rule and regex row", () => {
  const draft = {
    version: "2026.06",
    currency: "usd",
    description: "",
    sources: [],
    rules: [
      {
        id: "rule-1",
        provider: "openai",
        providerEndpointIdentifier: "",
        modelsText: "",
        modelPatternsText: "(abc",
        effectiveFrom: "",
        effectiveTo: "",
        inputRate: "1",
        cachedInputRate: "",
        cacheWrite5mRate: "",
        cacheWrite1hRate: "",
        outputRate: "2",
        batchMultiplier: "",
        serviceTier: "",
        longContextInputTokensOver: "",
        longContextInputMultiplier: "",
        longContextOutputMultiplier: "",
      },
    ],
  };

  assert.throws(
    () => modelPricingPayloadFromDraft(draft),
    /Pricing rule rule-1 model patterns row 1 contains an invalid regular expression:/,
  );
});
