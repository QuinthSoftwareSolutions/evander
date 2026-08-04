import assert from "node:assert/strict";
import test from "node:test";

import {
  ADMIN_NAVIGATION_GROUPS,
  TABS,
  addonIframeHashQuery,
  addonTabsFromPayload,
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
  assert.equal(tabFromHash("#host-directories"), "host-directories");
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
  assert.equal(nextPathFromHash("#addon:team-addon"), "/#addon:team-addon");
});

test("admin hashes carry non-empty query parameters", () => {
  assert.equal(adminHash("workflow", { trace_id: " trace-1 ", empty: "", skip: null }), "#workflow?trace_id=trace-1");
  assert.equal(adminHash("sessions", { session_id: "session 1" }), "#sessions?session_id=session+1");
  assert.equal(hashQueryParam("#workflow?trace_id=trace-1", "trace_id"), "trace-1");
  assert.equal(hashQueryParam("#workflow", "trace_id"), null);
});

test("add-on iframe hash query forwards only bounded deep-link fields", () => {
  const forwarded = new URLSearchParams(
    addonIframeHashQuery(
      "#addon:team-addon?view=my-human-reviews&human_review_request_id=review%201&conversation_id=conversation-1&directory_path=other&privileged_unlocked=true&unsafe=drop-me",
      "addon:team-addon",
    ),
  );

  assert.deepEqual(Object.fromEntries(forwarded), {
    view: "my-human-reviews",
    human_review_request_id: "review 1",
    conversation_id: "conversation-1",
  });
  assert.equal(
    addonIframeHashQuery(
      "#addon:other?view=my-human-reviews&human_review_request_id=review-1",
      "addon:team-addon",
    ),
    "",
  );
  assert.equal(
    addonIframeHashQuery(
      "#workflow?view=my-human-reviews&human_review_request_id=review-1",
      "workflow",
    ),
    "",
  );
});

test("dashboard is the first visible tab and vms stay backend-gated", () => {
  assert.deepEqual(
    tabsForExecutionBackend("container_worker_vm").map((tab) => tab.key).slice(0, 2),
    ["dashboard", "reports"],
  );
  assert.equal(tabsForExecutionBackend("container_worker_vm").some((tab) => tab.key === "vms"), false);
  assert.equal(tabsForExecutionBackend("libvirt_vm").some((tab) => tab.key === "vms"), true);
});

test("built-in tabs belong to one ordered navigation group", () => {
  assert.deepEqual(
    ADMIN_NAVIGATION_GROUPS.map((group) => group.key),
    ["overview", "operations", "automation", "extensions", "infrastructure", "governance"],
  );
  assert.equal(TABS.length, new Set(TABS.map((tab) => tab.key)).size);
  assert.deepEqual(
    Object.fromEntries(
      ADMIN_NAVIGATION_GROUPS.map((group) => [
        group.key,
        TABS.filter((tab) => tab.group === group.key).map((tab) => tab.key),
      ]),
    ),
    {
      overview: ["dashboard", "reports"],
      operations: ["agents", "sessions", "issues", "approvals", "artifacts"],
      automation: ["workflow", "skills"],
      extensions: ["addons", "connectors", "host-directories"],
      infrastructure: ["vms", "workers"],
      governance: ["tool-visibility", "mcp-tools", "tool-calls", "configuration", "users"],
    },
  );
});

test("add-on navigation payloads append dynamic admin tabs", () => {
  const addonTabs = addonTabsFromPayload([
    {
      addon_id: "team-addon",
      display_name: "Team Add-on",
      tab_key: "addon:team-addon",
      label: "Teams",
      short_label: "Teams",
      description: "Team-oriented add-on surface.",
      api_base_path: "/api/addons/team-addon",
      ui_path: "/addons/team-addon/",
    },
  ]);

  assert.equal(tabFromHash("#addon:team-addon"), "addon:team-addon");
  assert.deepEqual(
    tabsForExecutionBackend("container_worker_vm", addonTabs).map((tab) => tab.key).slice(-1),
    ["addon:team-addon"],
  );
  assert.equal(addonTabs[0].addonId, "team-addon");
  assert.equal(addonTabs[0].uiPath, "/addons/team-addon/");
  assert.equal(addonTabs[0].group, "extensions");
});

test("invalid add-on hashes and duplicate payloads are ignored", () => {
  assert.equal(tabFromHash("#addon:TeamAddon"), null);
  assert.deepEqual(
    addonTabsFromPayload([
      {
        addon_id: "team-addon",
        display_name: "Team Add-on",
        tab_key: "addon:team-addon",
        label: "Teams",
        short_label: "Teams",
        description: "Team add-on.",
        api_base_path: "/api/addons/team-addon",
        ui_path: null,
      },
      {
        addon_id: "team-addon",
        display_name: "Duplicate",
        tab_key: "addon:team-addon",
        label: "Duplicate",
        short_label: "Dup",
        description: "Duplicate.",
        api_base_path: "/api/addons/team-addon",
        ui_path: null,
      },
      {
        addon_id: "bad",
        display_name: "Bad",
        tab_key: "addon:Bad",
        label: "Bad",
        short_label: "Bad",
        description: "Bad.",
        api_base_path: "/api/addons/bad",
        ui_path: null,
      },
    ]).map((tab) => tab.label),
    ["Teams"],
  );
});

test("users tab is labeled users and groups", () => {
  const usersTab = tabsForExecutionBackend("libvirt_vm").find((tab) => tab.key === "users");
  assert.ok(usersTab);
  assert.equal(usersTab?.label, "Users & Groups");
  assert.equal(usersTab?.shortLabel, "Users");
  assert.equal(usersTab?.description, "Admin users, groups, and permissions.");
});
