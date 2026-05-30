# Users & Groups Page Modes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split Admin Web Users & Groups into addressable Users and Groups modes so `/#users/groups` opens the group-management view directly.

**Architecture:** Keep the existing global Admin tab as `users`, but teach hash routing to treat `#users/<mode>` as the Users & Groups tab. `UsersPage` owns the in-page mode state, initializes it from the hash, updates the hash when operators switch modes, and renders only the panels for the active mode.

**Tech Stack:** React 18, TypeScript, Vite, Node `--test --experimental-strip-types`, existing Admin Web hash routing helpers.

---

## File Structure

- Modify `frontend/workflow-editor/src/adminRouting.ts`
  - Parse path-like hash values such as `#users/groups`.
  - Export `AdminUsersMode`, `usersModeFromHash()`, and `usersModeHash()`.
- Modify `frontend/workflow-editor/test/adminRouting.test.mjs`
  - Add failing routing tests for Users subpaths and direct Groups links.
- Modify `frontend/workflow-editor/src/features/users/UsersPage.tsx`
  - Add URL-backed mode state and mode tab UI.
  - Render user list/user panels only in Users mode.
  - Render group list/group editor only in Groups mode.
- Modify `frontend/workflow-editor/src/styles.css`
  - Add compact mode-tab styling scoped to `.users-page`.
- Modify `frontend/workflow-editor/test/adminUsersPermissionHelp.test.mjs`
  - Extend the existing source-inspection test to cover mode split structure and hash wiring.
- Modify `docs/admin-ui-user-manual.md`
  - Document the two modes and direct `/#users/groups` link.
- Modify `CHANGELOG.md`
  - Add an entry for the Users & Groups mode split.

## Task 1: Hash Routing For Users Subpaths

**Files:**
- Modify: `frontend/workflow-editor/src/adminRouting.ts`
- Modify: `frontend/workflow-editor/test/adminRouting.test.mjs`

- [ ] **Step 1: Write failing routing tests**

In `frontend/workflow-editor/test/adminRouting.test.mjs`, update the import and add two tests after `"known admin hashes resolve to their matching tabs"`:

```js
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

test("users hash subpaths resolve to the users admin tab", () => {
  assert.equal(tabFromHash("#users"), "users");
  assert.equal(tabFromHash("#users/users"), "users");
  assert.equal(tabFromHash("#users/groups"), "users");
  assert.equal(tabFromHash("#users/groups?ignored=1"), "users");
  assert.equal(tabFromHash("#users/unknown"), "users");
  assert.equal(currentTab("#users/groups"), "users");
  assert.equal(tabFromHash("#workflow/unknown"), null);
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
```

Also extend `"hashes for valid tabs translate into safe Azure post-login next paths"`:

```js
assert.equal(nextPathFromHash("#users/groups"), "/#users/groups");
```

- [ ] **Step 2: Run routing tests and verify they fail**

Run:

```bash
cd frontend/workflow-editor
npm test -- test/adminRouting.test.mjs
```

Expected: FAIL because `usersModeFromHash` and `usersModeHash` are not exported, and `tabFromHash("#users/groups")` does not yet resolve to `users`.

- [ ] **Step 3: Implement routing helpers**

In `frontend/workflow-editor/src/adminRouting.ts`, add this type near `AdminHashParamValue`:

```ts
export type AdminUsersMode = "users" | "groups";
```

Replace `tabFromHash()` with:

```ts
export function tabFromHash(hash: string): AdminTab | null {
  const raw = hash.replace(/^#/, "").trim();
  const path = raw.split("?")[0]?.trim() ?? "";
  const [baseTab = "", subpath = ""] = path.split("/");
  if (!baseTab) {
    return null;
  }
  const resolvedTab = TAB_ALIASES[baseTab] ?? baseTab;
  if (resolvedTab === "users") {
    return "users";
  }
  if (subpath) {
    return null;
  }
  return TABS.some((tab) => tab.key === resolvedTab) ? (resolvedTab as AdminTab) : null;
}
```

Add these helpers after `adminHash()`:

```ts
export function usersModeFromHash(hash: string): AdminUsersMode {
  const raw = hash.replace(/^#/, "").trim();
  const path = raw.split("?")[0]?.trim() ?? "";
  const [baseTab = "", mode = ""] = path.split("/");
  const resolvedTab = TAB_ALIASES[baseTab] ?? baseTab;
  if (resolvedTab !== "users") {
    return "users";
  }
  return mode === "groups" ? "groups" : "users";
}

export function usersModeHash(mode: AdminUsersMode): string {
  return mode === "groups" ? "#users/groups" : "#users/users";
}
```

- [ ] **Step 4: Run routing tests and verify they pass**

Run:

```bash
cd frontend/workflow-editor
npm test -- test/adminRouting.test.mjs
```

Expected: PASS for all `adminRouting.test.mjs` tests.

- [ ] **Step 5: Commit routing change**

Run:

```bash
git add frontend/workflow-editor/src/adminRouting.ts frontend/workflow-editor/test/adminRouting.test.mjs
git commit -m "Add users groups mode hash routing"
```

## Task 2: UsersPage Mode Split

**Files:**
- Modify: `frontend/workflow-editor/src/features/users/UsersPage.tsx`
- Modify: `frontend/workflow-editor/src/styles.css`
- Modify: `frontend/workflow-editor/test/adminUsersPermissionHelp.test.mjs`

- [ ] **Step 1: Write failing UsersPage structure tests**

In `frontend/workflow-editor/test/adminUsersPermissionHelp.test.mjs`, append this test:

```js
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
```

- [ ] **Step 2: Run UsersPage test and verify it fails**

Run:

```bash
cd frontend/workflow-editor
npm test -- test/adminUsersPermissionHelp.test.mjs
```

Expected: FAIL because `UsersPage.tsx` does not yet import mode helpers, define `activeMode`, or render mode tabs.

- [ ] **Step 3: Import routing helpers and add mode helpers**

In `frontend/workflow-editor/src/features/users/UsersPage.tsx`, add this import below the React import:

```ts
import { usersModeFromHash, usersModeHash, type AdminUsersMode } from "../../adminRouting";
```

Inside `UsersPage`, after the existing `error` state, add:

```ts
  const [activeMode, setActiveMode] = useState<AdminUsersMode>(() =>
    typeof window === "undefined" ? "users" : usersModeFromHash(window.location.hash),
  );
```

After the existing `selectedGroup` memo, add:

```ts
  const isUsersMode = activeMode === "users";
```

After the selected group form `useEffect`, add:

```ts
  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    function handleHashChange() {
      setActiveMode(usersModeFromHash(window.location.hash));
    }
    window.addEventListener("hashchange", handleHashChange);
    return () => window.removeEventListener("hashchange", handleHashChange);
  }, []);

  function selectMode(mode: AdminUsersMode) {
    setActiveMode(mode);
    if (typeof window === "undefined") {
      return;
    }
    const nextHash = usersModeHash(mode);
    if (window.location.hash !== nextHash) {
      window.location.hash = nextHash;
    }
  }
```

Update `beginNewUser()` and `beginNewGroup()`:

```ts
  function beginNewUser() {
    selectMode("users");
    setSelectedLogin(null);
    setForm(EMPTY_FORM);
    setStatus(null);
    setError(null);
  }

  function beginNewGroup() {
    selectMode("groups");
    setSelectedGroupId(null);
    setGroupForm(EMPTY_GROUP_FORM);
    setStatus(null);
    setError(null);
  }
```

- [ ] **Step 4: Split sidebar rendering by mode**

In the `feature-sidebar` header actions, keep Refresh and make the primary action mode-specific:

```tsx
          <div className="inline-actions compact-actions">
            <button type="button" onClick={() => void loadUsers()} disabled={loading || !canManageUsers}>
              Refresh
            </button>
            <button
              type="button"
              className="primary"
              onClick={isUsersMode ? beginNewUser : beginNewGroup}
              disabled={!canManageUsers}
            >
              {isUsersMode ? "New user" : "New group"}
            </button>
          </div>
```

After the permission warning, add the mode tabs:

```tsx
        <div className="users-page-mode-tabs" role="tablist" aria-label="Users and groups mode">
          <button
            type="button"
            role="tab"
            className={`users-page-mode-tab${activeMode === "users" ? " active" : ""}`}
            aria-selected={activeMode === "users"}
            onClick={() => selectMode("users")}
          >
            Users
          </button>
          <button
            type="button"
            role="tab"
            className={`users-page-mode-tab${activeMode === "groups" ? " active" : ""}`}
            aria-selected={activeMode === "groups"}
            onClick={() => selectMode("groups")}
          >
            Groups
          </button>
        </div>
```

Replace the mixed user-list/group-list block with this conditional shape:

```tsx
        {activeMode === "users" ? (
          <div className="feature-list user-list">
            {sortedUsers.map((user) => (
              <button
                key={user.login}
                type="button"
                className={`feature-list-item user-list-item${selectedLogin === user.login ? " selected" : ""}`}
                onClick={() => {
                  setSelectedLogin(user.login);
                  setStatus(null);
                  setError(null);
                }}
                disabled={!canManageUsers}
              >
                <div className="feature-list-title-row user-list-item-row">
                  <strong>{userLabel(user)}</strong>
                  <span className={`status-pill state-${user.role === "admin" ? "running" : "idle"}`}>
                    {user.role}
                  </span>
                </div>
                <div className="feature-list-meta">{user.login}</div>
                <div className="feature-list-meta">
                  {(user.group_assignments ?? [])
                    .map((assignment) =>
                      groupLabel(groupsById.get(assignment.group_id), assignment.group_id),
                    )
                    .join(", ") || "No groups"}
                </div>
              </button>
            ))}
            {canManageUsers && loading && users.length === 0 ? <div className="empty-state">Loading users...</div> : null}
            {canManageUsers && !loading && users.length === 0 ? <div className="empty-state">No users are configured.</div> : null}
          </div>
        ) : (
          <div className="feature-list user-list">
            {sortedGroups.map((group) => (
              <button
                key={group.group_id}
                type="button"
                className={`feature-list-item user-list-item${selectedGroupId === group.group_id ? " selected" : ""}`}
                onClick={() => {
                  setSelectedGroupId(group.group_id);
                  setStatus(null);
                  setError(null);
                }}
                disabled={!canManageUsers}
              >
                <div className="feature-list-title-row user-list-item-row">
                  <strong>{group.display_name}</strong>
                  <span className={`status-pill state-${group.is_required ? "running" : "idle"}`}>
                    {group.is_required ? "required" : "custom"}
                  </span>
                </div>
                <div className="feature-list-meta">{group.group_id}</div>
                <div className="feature-list-meta">{group.permissions.length} permissions</div>
              </button>
            ))}
            {canManageUsers && loading && groups.length === 0 ? <div className="empty-state">Loading groups...</div> : null}
            {canManageUsers && !loading && groups.length === 0 ? <div className="empty-state">No groups are configured.</div> : null}
          </div>
        )}
```

- [ ] **Step 5: Split content rendering by mode**

Inside `<section className="feature-content">`, keep the shared status/error callouts immediately
after the opening section tag:

```tsx
        {status ? <div className="callout success-callout">{status}</div> : null}
        {error ? <div className="callout error-callout">{error}</div> : null}
```

Then render `activeMode === "users"` first. Move the current user toolbar, user identity panel,
access assignments panel, and user records table into that branch without changing their field
handlers:

```tsx
        {activeMode === "users" ? (
          <>
            <div className="feature-toolbar">
              <div>
                <h2>{selectedTitle}</h2>
                <p className="muted-text">
                  {selectedUser ? selectedUser.login : "Create an Azure SSO user record."}
                </p>
                <p className="muted-text">Directory: {selectedDirectoryPath}</p>
              </div>
              {selectedUser ? (
                <div className="feature-toolbar-actions">
                  <span className={`status-pill state-${selectedUser.role === "admin" ? "running" : "idle"}`}>
                    {selectedUser.role}
                  </span>
                  <button
                    type="button"
                    className="danger"
                    onClick={() => void deleteUser(selectedUser.login)}
                    disabled={actionDisabled}
                  >
                    Delete
                  </button>
                </div>
              ) : null}
            </div>
```

Close the Users branch after the current `<article className="panel">` that contains
`<h3>User records</h3>`. Start the Groups branch immediately after that close. Use this group
toolbar at the start of the Groups branch:

```tsx
          </>
        ) : (
          <>
            <div className="feature-toolbar">
              <div>
                <h2>{selectedGroupTitle}</h2>
                <p className="muted-text">
                  {selectedGroup ? selectedGroup.group_id : "Create a reusable permission group."}
                </p>
                <p className="muted-text">Permission catalog: {permissionCatalog.length} entries</p>
              </div>
              {selectedGroup && !selectedGroup.is_required ? (
                <div className="feature-toolbar-actions">
                  <span className="status-pill state-idle">custom</span>
                  <button
                    type="button"
                    className="danger"
                    onClick={() => void deleteGroup(selectedGroup.group_id)}
                    disabled={actionDisabled}
                  >
                    Delete group
                  </button>
                </div>
              ) : selectedGroup ? (
                <div className="feature-toolbar-actions">
                  <span className="status-pill state-running">required</span>
                </div>
              ) : null}
            </div>
```

Move the current group editor panel into the Groups branch after that toolbar. In the group editor
panel, remove the old inline `Delete group` button so Groups mode has one delete action:

```tsx
          <div className="inline-actions">
            <button type="button" className="primary" onClick={() => void saveGroup()} disabled={actionDisabled}>
              {saving ? "Saving..." : "Save group"}
            </button>
          </div>
```

Close the Groups branch before `</section>`:

```tsx
          </>
        )}
```

- [ ] **Step 6: Add mode-tab styles**

In `frontend/workflow-editor/src/styles.css`, near the existing `.users-page` styles, add:

```css
.users-page-mode-tabs {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.25rem;
  margin: 0.85rem 0 1rem;
  padding: 0.25rem;
  border: 1px solid rgba(71, 56, 37, 0.12);
  border-radius: 8px;
  background: rgba(242, 236, 226, 0.72);
}

.users-page-mode-tab {
  min-width: 0;
  padding: 0.55rem 0.65rem;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: #5f6f75;
  font-weight: 700;
  cursor: pointer;
}

.users-page-mode-tab.active,
.users-page-mode-tab[aria-selected="true"] {
  border-color: rgba(32, 69, 78, 0.16);
  background: rgba(255, 255, 255, 0.8);
  color: #20454e;
  box-shadow: 0 8px 18px rgba(56, 43, 27, 0.08);
}

.users-page-mode-tab:focus-visible {
  outline: 2px solid rgba(45, 95, 168, 0.55);
  outline-offset: 2px;
}
```

- [ ] **Step 7: Run UsersPage test and verify it passes**

Run:

```bash
cd frontend/workflow-editor
npm test -- test/adminUsersPermissionHelp.test.mjs
```

Expected: PASS for permission help and mode split tests.

- [ ] **Step 8: Run TypeScript build check**

Run:

```bash
cd frontend/workflow-editor
npm run build
```

Expected: PASS. If this updates tracked `frontend/workflow-editor/dist/index.html`, inspect it before staging and include it only if the build output is intentionally part of this repo's frontend workflow for this change.

- [ ] **Step 9: Commit UsersPage mode split**

Run:

```bash
git add frontend/workflow-editor/src/features/users/UsersPage.tsx frontend/workflow-editor/src/styles.css frontend/workflow-editor/test/adminUsersPermissionHelp.test.mjs
git commit -m "Split users and groups page modes"
```

## Task 3: Operator Docs And Changelog

**Files:**
- Modify: `docs/admin-ui-user-manual.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Update user manual**

In `docs/admin-ui-user-manual.md`, replace the first paragraph and common task list under `## Users & Groups` with:

```md
The Users & Groups tab manages Admin Web user records, reusable permission groups, directory-scoped
group assignments, and direct workflow grants when Azure SSO is enabled. The page has separate
**Users** and **Groups** modes. Open `/#users` for Users mode or `/#users/groups` to link directly
to Groups mode.

![Users](assets/admin-ui/manual-users.png)

Common user tasks:

1. Sign in as a user with the `admin` role.
2. Open **Users & Groups**.
3. Select **Users**.
4. Click **New user**.
5. Enter the user's login/email-style identifier.
6. Optionally set a display name.
7. Choose the legacy compatibility role.
8. Optionally attach a directory-scoped contact.
9. Add directory-scoped group assignments or direct workflow read/write grants.
10. Click **Save**.
11. Select an existing user to edit display name, role, contact, assignments, or grants.
12. Use **Delete** to remove a user record and invalidate their local Admin Web access on the next
    authenticated request.

Common group tasks:

1. Sign in as a user with the `admin` role.
2. Open **Users & Groups**.
3. Select **Groups**, or open `/#users/groups` directly.
4. Click **New group**.
5. Enter the group ID, name, and optional description.
6. Select the permissions the group should grant.
7. Click **Save group**.
8. Select an existing custom group to edit or delete it.
```

- [ ] **Step 2: Update changelog**

At the top of `CHANGELOG.md`, after the introductory paragraph and before the current first dated entry, add:

```md
## 2026-05-30 - Users And Groups Page Modes

- Split Admin Web Users & Groups into separate Users and Groups modes and made
  `/#users/groups` open directly to group management.

```

- [ ] **Step 3: Commit docs**

Run:

```bash
git add docs/admin-ui-user-manual.md CHANGELOG.md
git commit -m "Document users groups page modes"
```

## Task 4: Final Verification

**Files:**
- Verify: `frontend/workflow-editor/src/adminRouting.ts`
- Verify: `frontend/workflow-editor/src/features/users/UsersPage.tsx`
- Verify: `frontend/workflow-editor/src/styles.css`
- Verify: `frontend/workflow-editor/test/adminRouting.test.mjs`
- Verify: `frontend/workflow-editor/test/adminUsersPermissionHelp.test.mjs`
- Verify: `docs/admin-ui-user-manual.md`
- Verify: `CHANGELOG.md`

- [ ] **Step 1: Run focused frontend tests**

Run:

```bash
cd frontend/workflow-editor
npm test -- test/adminRouting.test.mjs test/adminUsersPermissionHelp.test.mjs
```

Expected: PASS for both focused tests.

- [ ] **Step 2: Run frontend build**

Run:

```bash
cd frontend/workflow-editor
npm run build
```

Expected: PASS without TypeScript errors.

- [ ] **Step 3: Inspect final diff**

Run:

```bash
git status --short
git diff --stat HEAD
```

Expected: only unrelated pre-existing worktree changes remain unstaged. The commits from this plan should contain only the files listed in the tasks, plus any intentionally reviewed build output.

- [ ] **Step 4: Manual URL smoke check if a dev server is available**

Run:

```bash
cd frontend/workflow-editor
npm run dev
```

Open `http://localhost:5173/#users/groups`.

Expected: Admin Web renders the Users & Groups page with Groups mode selected. The Users tab remains available in the in-page tab control, and switching modes updates the hash between `#users/users` and `#users/groups`.
