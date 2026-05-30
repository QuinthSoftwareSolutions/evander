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
