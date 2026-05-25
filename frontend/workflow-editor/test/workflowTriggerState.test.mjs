import assert from "node:assert/strict";
import test from "node:test";

import {
  workflowTriggerEnabled,
  workflowTriggerEnabledValue,
} from "../src/workflowTriggerState.ts";

test("workflowTriggerEnabled defaults triggers to enabled", () => {
  assert.equal(
    workflowTriggerEnabled({
      id: "trigger-1",
      display_name: "Trigger",
      type: "trigger:cron",
    }),
    true,
  );
});

test("workflowTriggerEnabled honors top-level and nested disabled flags", () => {
  assert.equal(
    workflowTriggerEnabled({
      id: "trigger-1",
      display_name: "Trigger",
      type: "trigger:cron",
      enabled: false,
    }),
    false,
  );
  assert.equal(
    workflowTriggerEnabled({
      id: "trigger-2",
      display_name: "Trigger",
      type: "trigger:cron",
      config: { enabled: false },
    }),
    false,
  );
});

test("workflowTriggerEnabledValue only persists disabled state", () => {
  assert.equal(workflowTriggerEnabledValue(false), false);
  assert.equal(workflowTriggerEnabledValue(true), undefined);
});
