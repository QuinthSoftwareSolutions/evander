import assert from "node:assert/strict";
import test from "node:test";

import {
  workflowJsonFieldDraftFallback,
  workflowJsonFieldFallbackValue,
} from "../src/workflowFieldDefaults.ts";

function jsonField(overrides = {}) {
  return {
    key: "payload",
    label: "Payload",
    kind: "json",
    required: false,
    bindable: false,
    read_only: false,
    multiline: false,
    options: [],
    item_fields: [],
    ...overrides,
  };
}

test("uses field-level JSON default values for literal drafts", () => {
  const field = jsonField({ key: "items", required: true, json_value_type: "array", default_value: [] });

  assert.equal(workflowJsonFieldDraftFallback(field, {}), "[]");
  assert.deepEqual(workflowJsonFieldFallbackValue(field, {}), []);
});

test("uses JSON value type when required fields do not declare an explicit default", () => {
  const field = jsonField({ key: "items", required: true, json_value_type: "array" });

  assert.equal(workflowJsonFieldDraftFallback(field, {}), "[]");
  assert.deepEqual(workflowJsonFieldFallbackValue(field, {}), []);
});

test("clones JSON default values before returning them", () => {
  const field = jsonField({ default_value: [] });
  const first = workflowJsonFieldFallbackValue(field, {});
  const second = workflowJsonFieldFallbackValue(field, {});

  first.push("changed");

  assert.deepEqual(first, ["changed"]);
  assert.deepEqual(second, []);
});

test("keeps object fallback for required JSON fields without a default", () => {
  const field = jsonField({ required: true });

  assert.equal(workflowJsonFieldDraftFallback(field, {}), "{}");
  assert.deepEqual(workflowJsonFieldFallbackValue(field, {}), {});
});

test("keeps optional JSON fields blank without a default", () => {
  const field = jsonField();

  assert.equal(workflowJsonFieldDraftFallback(field, undefined), "");
  assert.equal(workflowJsonFieldFallbackValue(field, undefined), undefined);
});
