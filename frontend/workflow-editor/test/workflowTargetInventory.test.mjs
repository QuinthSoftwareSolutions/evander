import assert from "node:assert/strict";
import test from "node:test";

import {
  workflowTargetInventoryFromSettledResults,
  workflowTargetInventoryPaths,
  workflowTargetOptions,
} from "../src/workflowTargetInventory.ts";

test("builds directory-scoped session and VM inventory paths", () => {
  const paths = workflowTargetInventoryPaths("root/operations");
  const sessionsUrl = new URL(paths.sessions, "http://localhost");
  const vmsUrl = new URL(paths.vms, "http://localhost");

  assert.equal(sessionsUrl.pathname, "/api/admin/sessions");
  assert.equal(sessionsUrl.searchParams.get("directory_path"), "root/operations");
  assert.equal(sessionsUrl.searchParams.get("direct_only"), "false");
  assert.equal(sessionsUrl.searchParams.get("include_expired"), "true");
  assert.equal(vmsUrl.pathname, "/api/admin/vms");
  assert.equal(vmsUrl.searchParams.get("directory_path"), "root/operations");
  assert.equal(vmsUrl.searchParams.get("direct_only"), "false");
  assert.equal(vmsUrl.searchParams.get("include_inactive"), "true");
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

test("keeps partial inventory when either request fails", () => {
  const sessionsResult = {
    status: "fulfilled",
    value: { items: [{ session_id: "session-1", is_expired: false }] },
  };
  const failedResult = { status: "rejected", reason: new Error("inventory unavailable") };

  assert.deepEqual(workflowTargetInventoryFromSettledResults(sessionsResult, failedResult), {
    sessions: [{ session_id: "session-1", is_expired: false }],
    vms: [],
  });
  assert.deepEqual(workflowTargetInventoryFromSettledResults(failedResult, {
    status: "fulfilled",
    value: { items: [{ vm_name: "vm-1" }] },
  }), {
    sessions: [],
    vms: [{ vm_name: "vm-1" }],
  });
  assert.deepEqual(workflowTargetInventoryFromSettledResults(failedResult, failedResult), {
    sessions: [],
    vms: [],
  });
});
