import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const app = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
const types = readFileSync(new URL("../src/types.ts", import.meta.url), "utf8");

test("selected workflow nodes expose generic execution-history queries", () => {
  assert.match(app, /<summary>Execution history<\/summary>/);
  assert.match(app, /7 \* 24 \* 60 \* 60 \* 1000/);
  assert.match(app, /\/api\/workflow-editor\/node-executions\/search/);
  assert.match(app, /\/api\/workflow-editor\/node-executions\/fields/);
  assert.match(app, /placeholder="\/user\/name"/);
  assert.match(app, /Inspect run/);
  assert.match(app, /Existing-record backfill is in progress/);
  assert.match(app, /Execution filter paths must be JSON Pointers beginning with '\/'/);
  assert.match(app, /nodeExecutionQueryRef\.current/);
  assert.match(app, /disabled=\{!execution\.full_record_available\}/);
  assert.match(app, /workflowNodeExecutionDefaultWindow/);
  assert.match(app, /nodeExecutionWindowTouchedRef\.current/);
  assert.match(app, /step="0\.001"/);
  assert.match(app, /Execution numeric filter values must not be blank/);
  assert.match(app, /JavaScript's safe range/);
  assert.match(app, /Configuration hash/);
});

test("execution-history types distinguish full and compact records", () => {
  assert.match(types, /record_tier: "full" \| "compact"/);
  assert.match(types, /full_record_available: boolean/);
  assert.match(types, /next_cursor\?: string \| null/);
  assert.match(types, /config_hash\?: string \| null/);
  assert.match(types, /"container"/);
});
