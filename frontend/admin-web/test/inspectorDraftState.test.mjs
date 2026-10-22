import assert from "node:assert/strict";
import test from "node:test";
import { initialInspectorDraft, inspectorDraftReducer } from "../src/features/workflow-editor/inspectorDraftState.ts";

test("independent inspector edits retain incomplete JSON and unknown fields", () => {
  let state = initialInspectorDraft();
  state = inspectorDraftReducer(state, { type: "set", field: "inspectorJsonDrafts", value: { template: "{" } });
  state = inspectorDraftReducer(state, { type: "set", field: "inspectorUnknownValues", value: { future_option: true } });
  state = inspectorDraftReducer(state, { type: "set", field: "inspectorDisplayName", value: "Edited node" });
  assert.equal(state.inspectorJsonDrafts.template, "{");
  assert.deepEqual(state.inspectorUnknownValues, { future_option: true });
  assert.equal(state.inspectorDisplayName, "Edited node");
});

test("queued functional updates apply to the latest inspector field state", () => {
  let state = initialInspectorDraft();
  for (const name of ["first", "second"]) {
    state = inspectorDraftReducer(state, { type: "set", field: "inspectorKnownValues", value: current => ({ ...current, [name]: true }) });
  }
  assert.deepEqual(state.inspectorKnownValues, { first: true, second: true });
});

test("switching inspector nodes clears stale drafts, modes and errors together", () => {
  const old = { ...initialInspectorDraft(), inspectorNodeId: "previous", inspectorDraftError: "Invalid JSON", inspectorJsonDrafts: { template: "{" }, inspectorFieldModes: { template: "template" }, inspectorSessionListMetadataRawMode: true };
  const next = { ...initialInspectorDraft(), inspectorNodeId: "next", inspectorKnownValues: { prompt: "Run" } };
  const state = inspectorDraftReducer(old, { type: "replace", state: next });
  assert.equal(state.inspectorNodeId, "next");
  assert.deepEqual(state.inspectorKnownValues, { prompt: "Run" });
  assert.deepEqual(state.inspectorJsonDrafts, {});
  assert.deepEqual(state.inspectorFieldModes, {});
  assert.equal(state.inspectorDraftError, null);
  assert.equal(state.inspectorSessionListMetadataRawMode, false);
});
