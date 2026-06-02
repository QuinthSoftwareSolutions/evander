import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const usersPage = readFileSync(new URL("../src/features/users/UsersPage.tsx", import.meta.url), "utf8");

test("users and groups permission catalog has help copy", () => {
  const expectedPermissions = [
    ["admin.owner", "Full Admin Web ownership"],
    ["groups.manage", "reusable permission groups"],
    ["users.manage", "user records"],
    ["workflow_runs.control", "Resume, dismiss, or clear workflow runs"],
    ["workflow_runs.read", "workflow run snapshots"],
    ["workflows.read", "workflow definitions"],
    ["workflows.write", "Save workflow definition"],
  ];

  for (const [permission, helpSnippet] of expectedPermissions) {
    assert.ok(usersPage.includes(permission), `missing permission ${permission}`);
    assert.ok(usersPage.includes(helpSnippet), `missing help text for ${permission}`);
  }
  assert.match(usersPage, /<PermissionTooltip permission=\{permission\} focusable \/>/);
  assert.match(usersPage, /<PermissionTooltip permission=\{permission\} \/>/);
});

test("users page separates users and groups into URL-backed modes", () => {
  const requiredSnippets = [
    'import { usersModeFromHash, usersModeHash, type AdminUsersMode } from "../../adminRouting";',
    "const [activeMode, setActiveMode] = useState<AdminUsersMode>",
    "usersModeFromHash(window.location.hash)",
    'window.addEventListener("hashchange", handleHashChange)',
    "usersModeHash(mode)",
    "function handleModeTabKeyDown",
    'case "ArrowLeft":',
    'case "ArrowRight":',
    'role="tablist"',
    'aria-label="Users and groups mode"',
    'id="users-page-users-tab"',
    'id="users-page-groups-tab"',
    'aria-controls="users-page-users-panel"',
    'aria-controls="users-page-groups-panel"',
    'aria-selected={activeMode === "users"}',
    'aria-selected={activeMode === "groups"}',
    'onKeyDown={handleModeTabKeyDown}',
    'id="users-page-users-panel"',
    'id="users-page-groups-panel"',
    'role="tabpanel"',
    'aria-labelledby="users-page-users-tab"',
    'aria-labelledby="users-page-groups-tab"',
    'activeMode === "users"',
    'activeMode === "groups"',
  ];

  for (const snippet of requiredSnippets) {
    assert.ok(usersPage.includes(snippet), `missing mode split snippet: ${snippet}`);
  }

  const sidebar = usersPage.indexOf('<section className="feature-sidebar">');
  const content = usersPage.indexOf('<section className="feature-content">');
  const usersBranch = usersPage.indexOf('id="users-page-users-panel"');
  const groupsBranch = usersPage.indexOf('id="users-page-groups-panel"');
  const userEditor = usersPage.indexOf('selectedUser ? "Edit user" : "Add user"');
  const groupEditor = usersPage.indexOf("<h3>{selectedGroupTitle}</h3>");
  const userRecords = usersPage.indexOf("<h3>User records</h3>");

  assert.ok(sidebar >= 0, "missing sidebar section");
  assert.ok(content >= 0, "missing content section");
  assert.ok(sidebar < content, "mode sidebar should appear before content in DOM order");
  assert.ok(usersBranch >= 0, "missing users tab panel");
  assert.ok(groupsBranch > usersBranch, "missing groups branch after users branch");
  assert.ok(userEditor > usersBranch && userEditor < groupsBranch, "user editor should be inside users mode");
  assert.ok(userRecords > usersBranch && userRecords < groupsBranch, "user records should be inside users mode");
  assert.ok(groupEditor > groupsBranch, "group editor should be inside groups mode");
});

test("users access assignments use directory dropdowns", () => {
  const accessStart = usersPage.indexOf("<h3>Access assignments</h3>");
  const userRecordsStart = usersPage.indexOf("<h3>User records</h3>", accessStart);
  assert.ok(accessStart >= 0, "missing access assignments panel");
  assert.ok(userRecordsStart > accessStart, "missing user records panel after access assignments");

  const accessPanel = usersPage.slice(accessStart, userRecordsStart);

  assert.ok(usersPage.includes("directoryTree,"), "users page should read the admin directory tree");
  assert.ok(usersPage.includes("flattenDirectorySelectOptions"), "users page should flatten directory options");
  assert.ok(usersPage.includes("ensureDirectorySelectOption"), "users page should preserve root/current/stale directory options");
  assert.equal(
    (accessPanel.match(/renderDirectorySelect\(/g) ?? []).length,
    2,
    "group assignments and workflow grants should both render directory selects",
  );
  assert.ok(accessPanel.includes('"Group assignment directory"'), "missing group assignment directory select label");
  assert.ok(accessPanel.includes('"Workflow grant directory"'), "missing workflow grant directory select label");
  assert.doesNotMatch(accessPanel, /placeholder="root\/ops"/, "directory paths should not require typed placeholders");
  assert.doesNotMatch(accessPanel, /<input\b/, "access assignment directory controls should not be free-form inputs");
});
