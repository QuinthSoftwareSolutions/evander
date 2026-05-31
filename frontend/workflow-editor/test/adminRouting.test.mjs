import assert from "node:assert/strict";
import test from "node:test";

import {
  adminHash,
  currentTab,
  hashQueryParam,
  nextPathFromHash,
  tabFromHash,
  tabsForExecutionBackend,
  usersModeFromHash,
  usersModeHash,
} from "../src/adminRouting.ts";

test("empty and unknown hashes default to the dashboard tab", () => {
  assert.equal(tabFromHash(""), null);
  assert.equal(tabFromHash("#"), null);
  assert.equal(tabFromHash("#missing"), null);
  assert.equal(currentTab(""), "dashboard");
  assert.equal(currentTab("#missing"), "dashboard");
});

test("known admin hashes resolve to their matching tabs", () => {
  assert.equal(tabFromHash("#workflow"), "workflow");
  assert.equal(tabFromHash("#workflow?trace_id=trace-1"), "workflow");
  assert.equal(tabFromHash("#sessions"), "sessions");
  assert.equal(tabFromHash("#sessions?session_id=session-1"), "sessions");
  assert.equal(tabFromHash("#tool-calls"), "tool-calls");
  assert.equal(tabFromHash("#configuration"), "configuration");
});

test("users hash subpaths resolve to the users admin tab", () => {
  assert.equal(tabFromHash("#users"), "users");
  assert.equal(tabFromHash("#users/users"), "users");
  assert.equal(tabFromHash("#users/groups"), "users");
  assert.equal(tabFromHash("#users/groups?ignored=1"), "users");
  assert.equal(tabFromHash("#users/unknown"), "users");
  assert.equal(currentTab("#users/groups"), "users");
  assert.equal(tabFromHash("#workflow/unknown"), null);
});

test("non-users hash subpaths are rejected", () => {
  assert.equal(tabFromHash("#workflow/"), null);
  assert.equal(tabFromHash("#workflow//unknown"), null);
  assert.equal(tabFromHash("#sessions/"), null);
  assert.equal(nextPathFromHash("#workflow//unknown"), null);
});

test("users mode helpers normalize hash subpaths", () => {
  assert.equal(usersModeFromHash(""), "users");
  assert.equal(usersModeFromHash("#users"), "users");
  assert.equal(usersModeFromHash("#users/users"), "users");
  assert.equal(usersModeFromHash("#users/groups"), "groups");
  assert.equal(usersModeFromHash("#users/groups?ignored=1"), "groups");
  assert.equal(usersModeFromHash("#users/unknown"), "users");
  assert.equal(usersModeFromHash("#workflow"), "users");
  assert.equal(usersModeHash("users"), "#users/users");
  assert.equal(usersModeHash("groups"), "#users/groups");
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
  assert.equal(nextPathFromHash("#workflow?trace_id=trace-1"), "/#workflow?trace_id=trace-1");
  assert.equal(nextPathFromHash("#users/groups"), "/#users/groups");
  assert.equal(nextPathFromHash("#settings"), "/#settings");
});

test("admin hashes carry non-empty query parameters", () => {
  assert.equal(adminHash("workflow", { trace_id: " trace-1 ", empty: "", skip: null }), "#workflow?trace_id=trace-1");
  assert.equal(adminHash("sessions", { session_id: "session 1" }), "#sessions?session_id=session+1");
  assert.equal(hashQueryParam("#workflow?trace_id=trace-1", "trace_id"), "trace-1");
  assert.equal(hashQueryParam("#workflow", "trace_id"), null);
});

test("dashboard is the first visible tab and vms stay backend-gated", () => {
  assert.deepEqual(
    tabsForExecutionBackend("container_worker_vm").map((tab) => tab.key).slice(0, 2),
    ["dashboard", "agents"],
  );
  assert.equal(tabsForExecutionBackend("container_worker_vm").some((tab) => tab.key === "vms"), false);
  assert.equal(tabsForExecutionBackend("libvirt_vm").some((tab) => tab.key === "vms"), true);
});

test("users tab is labeled users and groups", () => {
  const usersTab = tabsForExecutionBackend("libvirt_vm").find((tab) => tab.key === "users");
  assert.ok(usersTab);
  assert.equal(usersTab?.label, "Users & Groups");
  assert.equal(usersTab?.shortLabel, "Users");
  assert.equal(usersTab?.description, "Azure SSO users, groups, and permissions.");
});
