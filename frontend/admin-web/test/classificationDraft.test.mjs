import assert from "node:assert/strict";
import test from "node:test";
import { inlineEvaluation, profileEvaluation, mergeClassification, isEditableConfig, renameKey, providerSettings, profileForProvider } from "../src/features/classification/classificationDraft.ts";
import { classificationAnswerSchema, deriveWorkflowOutputSchemas, flattenOutputPaths } from "../src/workflowNodeOutputs.ts";
import { configurationDestinationsForTab } from "../src/features/configuration/configurationNavigation.ts";

function paths(schema) { return Object.entries(schema).flatMap(([key, value]) => flattenOutputPaths(key, value)); }

test("classification profiles replace inherited questions atomically and revert cleanly", () => {
  const parent = {enabled: true, providers: {jev: {default_model: "pinned", max_retries: 2}}, profiles: {triage: {questions: {old: {type: "boolean"}}}}};
  const draft = {providers: {jev: {max_retries: 0}}, profiles: {triage: {questions: {new: {type: "score"}}}}};
  const result = mergeClassification(parent, draft);
  assert.deepEqual(result.profiles.triage.questions, {new: {type: "score"}});
  assert.deepEqual(result.providers.jev, {default_model: "pinned", max_retries: 0});
  assert.deepEqual(mergeClassification(parent, {}).profiles, parent.profiles);
  assert.deepEqual(mergeClassification(parent, {profiles: {triage: {enabled: false}}}).profiles.triage, {enabled: false});
});

test("switching workflow definition clears incompatible fields", () => {
  const inline = {state: "input", ...inlineEvaluation({q: {type: "boolean"}}, "jev", "pinned")};
  const profile = JSON.parse(JSON.stringify({...inline, ...profileEvaluation("triage")}));
  assert.deepEqual(profile, {state: "input", profile_id: "triage"});
  assert.deepEqual(JSON.parse(JSON.stringify({...profile, ...inlineEvaluation({})})), {state: "input", questions: {}});
  assert.throws(() => renameKey({a: 1, b: 2}, "a", "b"), /already/);
  assert.throws(() => renameKey({a: 1}, "a", ""), /empty/);
});

test("known answers appear in workflow bindings without losing generic output metadata", () => {
  const questions = {route: {type: "choice"}, urgent: {type: "boolean"}, severity: {type: "score"}};
  const config = {triggers: [], gates: [], effects: [], edges: [], queries: [{id: "classify", type: "query:classification:evaluate", config: {questions}}]};
  let schemas = deriveWorkflowOutputSchemas(config, {classify: {provider: {type: "string"}, answers: {type: "object"}}});
  assert.ok(paths(schemas.classify).includes("answers.route.choice"));
  assert.ok(paths(schemas.classify).includes("answers.urgent.probability_true"));
  assert.ok(paths(schemas.classify).includes("answers.urgent.value"));
  assert.deepEqual(schemas.classify.answers.properties.urgent.properties.value, {type: "boolean"});
  assert.ok(paths(schemas.classify).includes("answers.severity.score"));
  assert.ok(paths(schemas.classify).includes("provider"));
  config.queries[0].config = {profile_id: "triage"};
  config.queries[0].outputs = {answers: classificationAnswerSchema(questions)};
  schemas = deriveWorkflowOutputSchemas(config, {classify: {answers: {type: "object"}}});
  assert.ok(paths(schemas.classify).includes("answers.route.choice"));
});

test("malformed advanced configuration cannot enter the structured editor", () => {
  for (const value of [null, [], {profiles: []}, {profiles: {bad: null}}, {profiles: {bad: {label: {nested: true}}}}, {providers: {jev: {timeout_seconds: "bad"}}}]) {
    assert.equal(isEditableConfig(value), false);
  }
  assert.equal(isEditableConfig({profiles: {triage: {questions: {q: {type: "choice", instructions: {structured: "kept"}}}}}}), true);
  assert.equal(isEditableConfig({providers: {openai_decisions: {default_model: null}}, profiles: {pending: {model: null}}}), true);
  assert.ok(configurationDestinationsForTab({key: "models_assistant_knowledge", sections: []}).some(d => d.key === "classification"));
});

test("pending provider profiles use their own defaults and cannot inherit Jev's model or enablement", () => {
  const providers = [{id: "openai_decisions", available: false, defaults: {enabled: false, default_model: null, api_key_env: "OPENAI_API_KEY"}}];
  const settings = providerSettings({providers: {openai_decisions: {max_retries: 0}}}, providers, "openai_decisions");
  assert.deepEqual(settings, {enabled: false, default_model: null, api_key_env: "OPENAI_API_KEY", max_retries: 0});
  const profile = profileForProvider({enabled: true, provider: "jev", model: "jev-1.13.0", questions: {route: {type: "choice"}}}, "openai_decisions", settings, false);
  assert.equal(profile.provider, "openai_decisions");
  assert.equal(profile.model, null);
  assert.equal(profile.enabled, false);
  assert.deepEqual(profile.questions, {route: {type: "choice"}});
});
