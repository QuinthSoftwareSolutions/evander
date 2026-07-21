import assert from "node:assert/strict";
import test from "node:test";

import {
  workflowTargetInventoryPath,
  workflowTargetOptions,
} from "../src/workflowTargetInventory.ts";

test("builds a workflow-authorized directory-scoped target catalog path", () => {
  const url = new URL(workflowTargetInventoryPath("root/operations"), "http://localhost");

  assert.equal(url.pathname, "/api/workflow-editor/target-catalog");
  assert.equal(url.searchParams.get("directory_path"), "root/operations");
});

test("offers mode-specific targets and marks expired sessions without changing their values", () => {
  const inventory = {
    sessions: [
      { session_id: "session-z", is_expired: true },
      { session_id: "session-a", is_expired: false },
      { session_id: "  " },
    ],
    vms: [
      { vm_name: "vm-b" },
      { vm_name: "session-z" },
      { vm_name: "vm-a" },
    ],
  };

  assert.deepEqual(workflowTargetOptions(inventory, "session_id"), [
    { value: "session-a", label: "session-a" },
    { value: "session-z", label: "session-z (expired)" },
  ]);
  assert.deepEqual(workflowTargetOptions(inventory, "vm_name"), [
    { value: "session-z", label: "session-z" },
    { value: "vm-a", label: "vm-a" },
    { value: "vm-b", label: "vm-b" },
  ]);
  assert.deepEqual(workflowTargetOptions(inventory, "target"), [
    { value: "session-a", label: "session-a" },
    { value: "session-z", label: "session-z (expired)" },
    { value: "vm-a", label: "vm-a" },
    { value: "vm-b", label: "vm-b" },
  ]);
});
