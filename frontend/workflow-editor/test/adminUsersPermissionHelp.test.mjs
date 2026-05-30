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
    'role="tablist"',
    'aria-label="Users and groups mode"',
    'aria-selected={activeMode === "users"}',
    'aria-selected={activeMode === "groups"}',
    'activeMode === "users"',
    'activeMode === "groups"',
  ];

  for (const snippet of requiredSnippets) {
    assert.ok(usersPage.includes(snippet), `missing mode split snippet: ${snippet}`);
  }

  const usersBranch = usersPage.indexOf('activeMode === "users"');
  const groupsBranch = usersPage.indexOf('activeMode === "groups"', usersBranch + 1);
  const userEditor = usersPage.indexOf('selectedUser ? "Edit user" : "Add user"');
  const groupEditor = usersPage.indexOf("<h3>{selectedGroupTitle}</h3>");
  const userRecords = usersPage.indexOf("<h3>User records</h3>");

  assert.ok(usersBranch >= 0, "missing users branch");
  assert.ok(groupsBranch > usersBranch, "missing groups branch after users branch");
  assert.ok(userEditor > usersBranch && userEditor < groupsBranch, "user editor should be inside users mode");
  assert.ok(userRecords > usersBranch && userRecords < groupsBranch, "user records should be inside users mode");
  assert.ok(groupEditor > groupsBranch, "group editor should be inside groups mode");
});
