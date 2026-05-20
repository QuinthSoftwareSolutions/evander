import assert from "node:assert/strict";
import test from "node:test";

import { currentTab, nextPathFromHash, tabFromHash, tabsForExecutionBackend } from "../src/adminRouting.ts";

test("empty and unknown hashes default to the dashboard tab", () => {
  assert.equal(tabFromHash(""), null);
  assert.equal(tabFromHash("#"), null);
  assert.equal(tabFromHash("#missing"), null);
  assert.equal(currentTab(""), "dashboard");
  assert.equal(currentTab("#missing"), "dashboard");
});

test("known admin hashes resolve to their matching tabs", () => {
  assert.equal(tabFromHash("#workflow"), "workflow");
  assert.equal(tabFromHash("#sessions"), "sessions");
  assert.equal(tabFromHash("#tool-calls"), "tool-calls");
  assert.equal(tabFromHash("#configuration"), "configuration");
});

test("settings remains a compatibility alias for configuration", () => {
  assert.equal(tabFromHash("#settings"), "configuration");
  assert.equal(currentTab("#settings"), "configuration");
});

test("hashes for valid tabs translate into safe Azure post-login next paths", () => {
  assert.equal(nextPathFromHash(""), null);
  assert.equal(nextPathFromHash("#"), null);
  assert.equal(nextPathFromHash("#missing"), null);
  assert.equal(nextPathFromHash("#workflow"), "/#workflow");
  assert.equal(nextPathFromHash("#settings"), "/#settings");
});

test("dashboard is the first visible tab and vms stay backend-gated", () => {
  assert.deepEqual(
    tabsForExecutionBackend("container_worker_vm").map((tab) => tab.key).slice(0, 2),
    ["dashboard", "agents"],
  );
  assert.equal(tabsForExecutionBackend("container_worker_vm").some((tab) => tab.key === "vms"), false);
  assert.equal(tabsForExecutionBackend("libvirt_vm").some((tab) => tab.key === "vms"), true);
});
