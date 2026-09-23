import { type KeyboardEvent, useEffect, useMemo, useState } from "react";

import { usersModeFromHash, usersModeHash, type AdminUsersMode } from "../../adminRouting";
import { fetchJson, formatTimestamp } from "../admin/api";
import { useAdminUi, type DirectoryTreeNode } from "../admin/AdminContext";
import { RecordWorkspace } from "../admin/RecordWorkspace";
import { readAdminHashQuery } from "../admin/hash";
import { Tooltip } from "../admin/Tooltip";
import type {
  AdminContactSummary,
  AdminGroupMutationResponse,
  AdminUserListResponse,
  AdminUserMutationResponse,
  AdminWebGroupRecord,
  AdminWebUserGroupAssignmentRecord,
  AdminWebUserRecord,
  AdminWebWorkflowGrantRecord,
} from "../admin/types";

type GroupAssignmentFormState = {
  groupId: string;
  directoryPath: string;
};

type WorkflowGrantFormState = {
  permission: "workflows.read" | "workflows.write";
  directoryPath: string;
};

type DirectorySelectOption = {
  value: string;
  label: string;
};

type UserFormState = {
  login: string;
  displayName: string;
  password: string;
  contactId: string;
  initialContactId: string;
  groupAssignments: GroupAssignmentFormState[];
  workflowGrants: WorkflowGrantFormState[];
};

type GroupFormState = {
  groupId: string;
  displayName: string;
  description: string;
  permissions: string[];
};

const EMPTY_FORM: UserFormState = {
  login: "",
  displayName: "",
  password: "",
  contactId: "",
  initialContactId: "",
  groupAssignments: [],
  workflowGrants: [],
};

const EMPTY_GROUP_FORM: GroupFormState = {
  groupId: "",
  displayName: "",
  description: "",
  permissions: [],
};

const DIRECT_WORKFLOW_PERMISSIONS = ["workflows.read", "workflows.write"] as const;
const PERMISSION_HELP: Record<string, string> = {
  "admin.owner": "Full Admin Web ownership. Grants every catalog permission in every directory scope.",
  "groups.manage": "Create, edit, and delete reusable permission groups and the permissions assigned to them.",
  "team.human_reviews.decide": "Decide assigned human release reviews in the request's directory scope.",
  "users.manage": "Create and edit user records, directory-scoped group assignments, and direct workflow grants.",
  "workflow_runs.control": "Resume, dismiss, or clear workflow runs. Pair with workflow_runs.read when the user also needs run details.",
  "workflow_runs.read": "Inspect workflow run snapshots, run details, trigger/gate/effect activity, and runtime graph status.",
  "workflow_packs.manage": "Create, edit metadata for, and remove workflow packs from the active list. This is global pack configuration, not a direct workflow grant.",
  "workflows.read": "Open and inspect workflow definitions, graph metadata, layouts, and workflow catalog details.",
  "workflows.write": "Save workflow definition and layout changes. Add workflows.read too when the group should open workflow documents.",
};

function userLabel(user: AdminWebUserRecord): string {
  return user.display_name?.trim() || user.login;
}

function assignmentFormState(
  assignments: AdminWebUserGroupAssignmentRecord[] | undefined,
): GroupAssignmentFormState[] {
  return (assignments ?? []).map((assignment) => ({
    groupId: assignment.group_id,
    directoryPath: assignment.directory_path || "root",
  }));
}

function workflowGrantFormState(
  grants: AdminWebWorkflowGrantRecord[] | undefined,
): WorkflowGrantFormState[] {
  return (grants ?? []).map((grant) => ({
    permission: grant.permission,
    directoryPath: grant.directory_path || "root",
  }));
}

function groupLabel(group: AdminWebGroupRecord | undefined, groupId: string): string {
  return group?.display_name?.trim() || groupId;
}

function directoryOptionLabel(path: string, name: string | undefined, depth: number): string {
  if (path === "root") return "root";
  const prefix = depth > 0 ? `${"  ".repeat(depth)}` : "";
  return name && name !== path ? `${prefix}${name} (${path})` : `${prefix}${path}`;
}

function flattenDirectorySelectOptions(node: DirectoryTreeNode, depth = 0): DirectorySelectOption[] {
  const path = node.directory.path;
  const current = {
    value: path,
    label: directoryOptionLabel(path, node.directory.name, depth),
  };
  return [
    current,
    ...node.children.flatMap((child) => flattenDirectorySelectOptions(child, depth + 1)),
  ];
}

function ensureDirectorySelectOption(
  options: DirectorySelectOption[],
  value: string,
  label: string,
): DirectorySelectOption[] {
  if (!value || options.some((option) => option.value === value)) return options;
  return [...options, { value, label }];
}

function permissionHelp(permission: string): string {
  return PERMISSION_HELP[permission] ?? `Permission: ${permission}`;
}

function PermissionTooltip({ permission, focusable = false }: { permission: string; focusable?: boolean }) {
  return (
    <Tooltip
      content={permissionHelp(permission)}
      focusable={focusable}
      ariaLabel={`Permission help for ${permission}`}
    >
      <span className="permission-label">{permission}</span>
    </Tooltip>
  );
}

export function UsersPage() {
  const {
    authMode,
    canManageUsers,
    currentUserId,
    directoryScopedConfigEnabled,
    directoryTree,
    selectedDirectoryPath,
  } = useAdminUi();
  const [users, setUsers] = useState<AdminWebUserRecord[]>([]);
  const [groups, setGroups] = useState<AdminWebGroupRecord[]>([]);
  const [permissionCatalog, setPermissionCatalog] = useState<string[]>([]);
  const [contacts, setContacts] = useState<AdminContactSummary[]>([]);
  const [selectedLogin, setSelectedLogin] = useState<string | null>(null);
  const [selectedGroupId, setSelectedGroupId] = useState<string | null>(null);
  const [form, setForm] = useState<UserFormState>(EMPTY_FORM);
  const [groupForm, setGroupForm] = useState<GroupFormState>(EMPTY_GROUP_FORM);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activeMode, setActiveMode] = useState<AdminUsersMode>(() =>
    typeof window === "undefined" ? "users" : usersModeFromHash(window.location.hash),
  );

  const sortedUsers = useMemo(
    () =>
      [...users].sort((left, right) =>
        left.login.localeCompare(right.login, undefined, {
          numeric: true,
          sensitivity: "base",
        }),
      ),
    [users],
  );

  const sortedGroups = useMemo(
    () =>
      [...groups].sort((left, right) =>
        left.display_name.localeCompare(right.display_name, undefined, {
          numeric: true,
          sensitivity: "base",
        }),
      ),
    [groups],
  );

  const groupsById = useMemo(
    () => new Map(sortedGroups.map((group) => [group.group_id, group])),
    [sortedGroups],
  );

  const directoryPathOptions = useMemo(() => {
    const treeOptions = directoryTree?.ok && directoryTree.data
      ? flattenDirectorySelectOptions(directoryTree.data)
      : [];
    const withRoot = ensureDirectorySelectOption(treeOptions, "root", "root");
    return ensureDirectorySelectOption(withRoot, selectedDirectoryPath, selectedDirectoryPath);
  }, [directoryTree, selectedDirectoryPath]);

  const selectedUser = useMemo(
    () => sortedUsers.find((user) => user.login === selectedLogin) ?? null,
    [selectedLogin, sortedUsers],
  );

  const selectedGroup = useMemo(
    () => sortedGroups.find((group) => group.group_id === selectedGroupId) ?? null,
    [selectedGroupId, sortedGroups],
  );
  const isUsersMode = activeMode === "users";

  async function loadUsers() {
    if (!canManageUsers) {
      return;
    }
    setLoading(true);
    try {
      const payload = await fetchJson<AdminUserListResponse>(
        `/api/admin/users?directory_path=${encodeURIComponent(selectedDirectoryPath)}`,
      );
      setUsers(payload.users);
      setContacts(payload.contacts);
      setGroups(payload.groups);
      setPermissionCatalog(payload.permission_catalog);
      setSelectedLogin((current) => {
        if (current && payload.users.some((user) => user.login === current)) return current;
        return payload.users[0]?.login ?? null;
      });
      setSelectedGroupId((current) => {
        if (current && payload.groups.some((group) => group.group_id === current)) return current;
        return payload.groups[0]?.group_id ?? null;
      });
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadUsers();
  }, [canManageUsers, selectedDirectoryPath]);

  useEffect(() => {
    if (!selectedUser) {
      setForm(EMPTY_FORM);
      return;
    }
    setForm({
      login: selectedUser.login,
      displayName: selectedUser.display_name ?? "",
      password: "",
      contactId: selectedUser.contact_id ?? "",
      initialContactId: selectedUser.contact_id ?? "",
      groupAssignments: assignmentFormState(selectedUser.group_assignments),
      workflowGrants: workflowGrantFormState(selectedUser.workflow_grants),
    });
  }, [selectedUser]);

  useEffect(() => {
    if (!selectedGroup) {
      setGroupForm(EMPTY_GROUP_FORM);
      return;
    }
    setGroupForm({
      groupId: selectedGroup.group_id,
      displayName: selectedGroup.display_name,
      description: selectedGroup.description ?? "",
      permissions: selectedGroup.permissions ?? [],
    });
  }, [selectedGroup]);

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

  function handleModeTabKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    let nextMode: AdminUsersMode | null = null;
    switch (event.key) {
      case "ArrowLeft":
      case "Left":
        nextMode = activeMode === "users" ? "groups" : "users";
        break;
      case "ArrowRight":
      case "Right":
        nextMode = activeMode === "users" ? "groups" : "users";
        break;
      default:
        return;
    }
    event.preventDefault();
    selectMode(nextMode);
    if (typeof window === "undefined") {
      return;
    }
    window.requestAnimationFrame(() => {
      document.getElementById(nextMode === "users" ? "users-page-users-tab" : "users-page-groups-tab")?.focus();
    });
  }

  function beginNewUser() {
    selectMode("users");
    setSelectedLogin(null);
    setForm({
      ...EMPTY_FORM,
      groupAssignments:
        users.length === 0 && groupsById.has("owner")
          ? [{ groupId: "owner", directoryPath: "root" }]
          : groupsById.has("operator")
            ? [{ groupId: "operator", directoryPath: "root" }]
            : [],
    });
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

  async function saveUser() {
    const login = form.login.trim();
    if (!login) {
      setError("Login is required.");
      return;
    }
    setSaving(true);
    setStatus(null);
    setError(null);
    try {
      const requestPayload: {
        login: string;
        display_name: string | null;
        password?: string;
        contact_id?: string | null;
        group_assignments?: Array<{ group_id: string; directory_path: string }>;
        workflow_grants?: Array<{ permission: "workflows.read" | "workflows.write"; directory_path: string }>;
      } = {
        login,
        display_name: form.displayName.trim() || null,
      };
      if (authMode === "local_password") {
        if (!selectedUser && form.password === "") {
          setError("Password is required.");
          setSaving(false);
          return;
        }
        if (form.password !== "") {
          requestPayload.password = form.password;
        }
      }
      if (selectedUser || form.groupAssignments.length > 0 || form.workflowGrants.length > 0) {
        requestPayload.group_assignments = form.groupAssignments
          .map((assignment) => ({
            group_id: assignment.groupId.trim(),
            directory_path: assignment.directoryPath.trim() || "root",
          }))
          .filter((assignment) => assignment.group_id);
        requestPayload.workflow_grants = form.workflowGrants
          .map((grant) => ({
            permission: grant.permission,
            directory_path: grant.directoryPath.trim() || "root",
          }))
          .filter((grant) => DIRECT_WORKFLOW_PERMISSIONS.includes(grant.permission));
      }
      const contactChanged = form.contactId !== form.initialContactId;
      if (
        directoryScopedConfigEnabled &&
        (selectedUser ? contactChanged : Boolean(form.contactId))
      ) {
        requestPayload.contact_id = form.contactId || null;
      }
      const body = JSON.stringify(requestPayload);
      const payload = selectedUser
        ? await fetchJson<AdminUserMutationResponse>(
            `/api/admin/users/${encodeURIComponent(selectedUser.login)}?directory_path=${encodeURIComponent(selectedDirectoryPath)}`,
            { method: "PATCH", body },
          )
        : await fetchJson<AdminUserMutationResponse>(
            `/api/admin/users?directory_path=${encodeURIComponent(selectedDirectoryPath)}`,
            {
              method: "POST",
              body,
            },
          );
      await loadUsers();
      setSelectedLogin(payload.user.login);
      setStatus(`Saved ${payload.user.login}.`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSaving(false);
    }
  }

  async function deleteUser(login: string) {
    if (!window.confirm(`Remove ${login}?`)) {
      return;
    }
    setSaving(true);
    setStatus(null);
    setError(null);
    try {
      await fetchJson<AdminUserMutationResponse>(
        `/api/admin/users/${encodeURIComponent(login)}?directory_path=${encodeURIComponent(selectedDirectoryPath)}`,
        { method: "DELETE" },
      );
      await loadUsers();
      setSelectedLogin(null);
      setStatus(`Removed ${login}.`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSaving(false);
    }
  }

  async function saveGroup() {
    const groupId = groupForm.groupId.trim();
    const displayName = groupForm.displayName.trim();
    if (!groupId) {
      setError("Group ID is required.");
      return;
    }
    if (!displayName) {
      setError("Group name is required.");
      return;
    }
    setSaving(true);
    setStatus(null);
    setError(null);
    try {
      const body = JSON.stringify({
        group_id: groupId,
        display_name: displayName,
        description: groupForm.description.trim() || null,
        permissions: groupForm.permissions,
      });
      const payload = selectedGroup
        ? await fetchJson<AdminGroupMutationResponse>(
            `/api/admin/groups/${encodeURIComponent(selectedGroup.group_id)}`,
            { method: "PATCH", body },
          )
        : await fetchJson<AdminGroupMutationResponse>("/api/admin/groups", {
            method: "POST",
            body,
          });
      await loadUsers();
      setSelectedGroupId(payload.group.group_id);
      setStatus(`Saved ${payload.group.display_name}.`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSaving(false);
    }
  }

  async function deleteGroup(groupId: string) {
    if (!window.confirm(`Remove group ${groupId}?`)) {
      return;
    }
    setSaving(true);
    setStatus(null);
    setError(null);
    try {
      const payload = await fetchJson<AdminGroupMutationResponse>(
        `/api/admin/groups/${encodeURIComponent(groupId)}`,
        { method: "DELETE" },
      );
      await loadUsers();
      setSelectedGroupId(null);
      setStatus(`Removed ${payload.group.display_name}.`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSaving(false);
    }
  }

  const selectedTitle = selectedUser ? userLabel(selectedUser) : "New user";
  const selectedGroupTitle = selectedGroup ? selectedGroup.display_name : "New group";
  const actionDisabled = loading || saving || !canManageUsers;

  function renderDirectorySelect(
    value: string,
    onChange: (nextValue: string) => void,
    ariaLabel: string,
  ) {
    const directoryPath = value.trim() || "root";
    const options = ensureDirectorySelectOption(
      directoryPathOptions,
      directoryPath,
      `${directoryPath} (not in directory tree)`,
    );
    return (
      <select
        value={directoryPath}
        onChange={(event) => onChange(event.target.value)}
        disabled={actionDisabled}
        aria-label={ariaLabel}
      >
        {options.map((option) => (
          <option key={`${ariaLabel}-${option.value}`} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    );
  }

  return (
    <RecordWorkspace
      title="Users & Groups"
      description="Inspect identities, group membership, and directory scope. Manage permissions in the full record."
      columns={isUsersMode ? ["User", "Groups", "Last login"] : ["Group", "Type", "Permissions"]}
      records={
        isUsersMode
          ? sortedUsers.map((user) => ({
              id: user.login,
              title: userLabel(user),
              description: user.login,
              href: `${usersModeHash("users")}?${new URLSearchParams({ login: user.login })}`,
              cells: [
                (user.group_assignments ?? [])
                  .map((assignment) => groupLabel(groupsById.get(assignment.group_id), assignment.group_id))
                  .join(", ") || "No groups",
                formatTimestamp(user.last_login_at),
              ],
              facts: [
                ["Login", user.login],
                ["Name", userLabel(user)],
                ["Local password", user.local_password_enabled ? "Enabled" : "Disabled"],
                ["Contact", user.contact?.name ?? "None"],
                ["Last login", formatTimestamp(user.last_login_at)],
              ],
            }))
          : sortedGroups.map((group) => ({
              id: group.group_id,
              title: group.display_name,
              description: group.group_id,
              href: `${usersModeHash("groups")}?${new URLSearchParams({ group_id: group.group_id })}`,
              cells: [group.is_required ? "Required" : "Custom", group.permissions.length],
              facts: [
                ["Group", group.group_id],
                ["Description", group.description],
                ["Type", group.is_required ? "Required" : "Custom"],
                ["Permissions", group.permissions.join(", ")],
              ],
            }))
      }
      selectedId={isUsersMode ? selectedLogin : selectedGroupId}
      loading={loading}
      error={error}
      initialRecordOpen={Boolean(readAdminHashQuery().get("login") || readAdminHashQuery().get("group_id"))}
      recordRouteKeys="login,group_id"
      onSelect={(id) => {
        if (!canManageUsers) return false;
        if (isUsersMode) setSelectedLogin(id);
        else setSelectedGroupId(id);
        setStatus(null);
        setError(null);
      }}
      filters={
        <label className="field-stack">
          <span>View</span>
          <select value={activeMode} onChange={(event) => selectMode(event.target.value as AdminUsersMode)}>
            <option value="users">Users</option>
            <option value="groups">Groups</option>
          </select>
        </label>
      }
      actions={(openRecord) => (
        <>
          <button type="button" disabled={loading || !canManageUsers} onClick={() => void loadUsers()}>
            Refresh
          </button>
          <button
            type="button"
            className="primary"
            disabled={!canManageUsers}
            onClick={() => {
              if (isUsersMode) beginNewUser();
              else beginNewGroup();
              openRecord();
            }}
          >
            {isUsersMode ? "New user" : "New group"}
          </button>
        </>
      )}
    >
      <div className="feature-page users-page">
        <section className="feature-sidebar">
          <div className="feature-sidebar-header">
            <div>
              <div className="eyebrow">Access</div>
              <h1>Users &amp; Groups</h1>
            </div>
            <div className="inline-actions compact-actions">
              <button
                className="admin-action-button compact"
                type="button"
                onClick={() => void loadUsers()}
                disabled={loading || !canManageUsers}
              >
                Refresh
              </button>
              <button
                type="button"
                className="admin-action-button primary compact"
                onClick={isUsersMode ? beginNewUser : beginNewGroup}
                disabled={!canManageUsers}
              >
                {isUsersMode ? "New user" : "New group"}
              </button>
            </div>
          </div>
          {!canManageUsers ? (
            <div className="callout warning-callout">
              User-management permission is required to manage users and groups.
            </div>
          ) : null}
          <div className="users-page-mode-tabs" role="tablist" aria-label="Users and groups mode">
            <button
              id="users-page-users-tab"
              type="button"
              role="tab"
              className={`users-page-mode-tab${activeMode === "users" ? " active" : ""}`}
              aria-controls="users-page-users-panel"
              aria-selected={activeMode === "users"}
              onClick={() => selectMode("users")}
              onKeyDown={handleModeTabKeyDown}
            >
              Users
            </button>
            <button
              id="users-page-groups-tab"
              type="button"
              role="tab"
              className={`users-page-mode-tab${activeMode === "groups" ? " active" : ""}`}
              aria-controls="users-page-groups-panel"
              aria-selected={activeMode === "groups"}
              onClick={() => selectMode("groups")}
              onKeyDown={handleModeTabKeyDown}
            >
              Groups
            </button>
          </div>
          {isUsersMode ? (
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
                  </div>
                  <div className="feature-list-meta">{user.login}</div>
                  <div className="feature-list-meta">
                    {(user.group_assignments ?? [])
                      .map((assignment) => groupLabel(groupsById.get(assignment.group_id), assignment.group_id))
                      .join(", ") || "No groups"}
                  </div>
                </button>
              ))}
              {canManageUsers && loading && users.length === 0 ? (
                <div className="empty-state">Loading users...</div>
              ) : null}
              {canManageUsers && !loading && users.length === 0 ? (
                <div className="empty-state">No users are configured.</div>
              ) : null}
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
              {canManageUsers && loading && groups.length === 0 ? (
                <div className="empty-state">Loading groups...</div>
              ) : null}
              {canManageUsers && !loading && groups.length === 0 ? (
                <div className="empty-state">No groups are configured.</div>
              ) : null}
            </div>
          )}
        </section>

        <section className="feature-content">
          {status ? <div className="callout success-callout">{status}</div> : null}
          {error ? <div className="callout error-callout">{error}</div> : null}

          <div
            id="users-page-users-panel"
            role="tabpanel"
            className="users-page-mode-panel"
            aria-labelledby="users-page-users-tab"
            hidden={activeMode !== "users"}
          >
            {activeMode === "users" ? (
              <>
                <div className="feature-toolbar">
                  <div>
                    <h2>{selectedTitle}</h2>
                    <p className="muted-text">
                      {selectedUser
                        ? selectedUser.login
                        : authMode === "local_password"
                          ? "Create a local user record."
                          : "Create an Azure SSO user record."}
                    </p>
                    <p className="muted-text">Directory: {selectedDirectoryPath}</p>
                  </div>
                  {selectedUser ? (
                    <div className="feature-toolbar-actions">
                      <button
                        type="button"
                        className="admin-action-button danger compact"
                        onClick={() => void deleteUser(selectedUser.login)}
                        disabled={actionDisabled}
                      >
                        Delete
                      </button>
                    </div>
                  ) : null}
                </div>

                <article className="panel">
                  <div className="panel-header">
                    <h3>{selectedUser ? "Edit user" : "Add user"}</h3>
                    <span className="panel-meta">{currentUserId}</span>
                  </div>
                  <div className="config-field-grid">
                    <label className="field-stack">
                      <span>Login</span>
                      <input
                        value={form.login}
                        onChange={(event) => setForm((current) => ({ ...current, login: event.target.value }))}
                        disabled={actionDisabled || Boolean(selectedUser)}
                        placeholder="operator@example.com"
                      />
                    </label>
                    <label className="field-stack">
                      <span>Display name</span>
                      <input
                        value={form.displayName}
                        onChange={(event) => setForm((current) => ({ ...current, displayName: event.target.value }))}
                        disabled={actionDisabled}
                        placeholder="Optional"
                      />
                    </label>
                    {authMode === "local_password" ? (
                      <label className="field-stack">
                        <span>{selectedUser ? "New password" : "Password"}</span>
                        <input
                          value={form.password}
                          onChange={(event) => setForm((current) => ({ ...current, password: event.target.value }))}
                          disabled={actionDisabled}
                          type="password"
                          autoComplete="new-password"
                          placeholder={selectedUser ? "Unchanged" : ""}
                        />
                      </label>
                    ) : null}
                    <label className="field-stack">
                      <span>Contact</span>
                      <select
                        value={form.contactId}
                        onChange={(event) => setForm((current) => ({ ...current, contactId: event.target.value }))}
                        disabled={actionDisabled || !directoryScopedConfigEnabled}
                      >
                        <option value="">None</option>
                        {contacts.map((contact) => (
                          <option key={contact.id} value={contact.id}>
                            {contact.name} ({contact.id})
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>
                  <div className="inline-actions">
                    <button
                      type="button"
                      className="admin-action-button primary compact"
                      onClick={() => void saveUser()}
                      disabled={actionDisabled}
                    >
                      {saving ? "Saving..." : "Save"}
                    </button>
                  </div>
                </article>

                <article className="panel">
                  <div className="panel-header">
                    <h3>Access assignments</h3>
                    <span className="panel-meta">{selectedDirectoryPath}</span>
                  </div>
                  <div className="config-field-grid">
                    <div className="field-stack">
                      <span>Group assignments</span>
                      <div className="stacked-list">
                        {form.groupAssignments.map((assignment, index) => (
                          <div
                            className="inline-actions"
                            key={`${assignment.groupId}-${assignment.directoryPath}-${index}`}
                          >
                            <select
                              value={assignment.groupId}
                              onChange={(event) =>
                                setForm((current) => ({
                                  ...current,
                                  groupAssignments: current.groupAssignments.map((item, itemIndex) =>
                                    itemIndex === index ? { ...item, groupId: event.target.value } : item,
                                  ),
                                }))
                              }
                              disabled={actionDisabled}
                            >
                              <option value="">Select group</option>
                              {sortedGroups.map((group) => (
                                <option key={group.group_id} value={group.group_id}>
                                  {group.display_name} ({group.group_id})
                                </option>
                              ))}
                            </select>
                            {renderDirectorySelect(
                              assignment.directoryPath,
                              (directoryPath) =>
                                setForm((current) => ({
                                  ...current,
                                  groupAssignments: current.groupAssignments.map((item, itemIndex) =>
                                    itemIndex === index ? { ...item, directoryPath } : item,
                                  ),
                                })),
                              "Group assignment directory",
                            )}
                            <button
                              className="admin-action-button compact"
                              type="button"
                              onClick={() =>
                                setForm((current) => ({
                                  ...current,
                                  groupAssignments: current.groupAssignments.filter(
                                    (_, itemIndex) => itemIndex !== index,
                                  ),
                                }))
                              }
                              disabled={actionDisabled}
                            >
                              Remove
                            </button>
                          </div>
                        ))}
                        <button
                          className="admin-action-button compact"
                          type="button"
                          onClick={() =>
                            setForm((current) => ({
                              ...current,
                              groupAssignments: [
                                ...current.groupAssignments,
                                { groupId: sortedGroups[0]?.group_id ?? "", directoryPath: selectedDirectoryPath },
                              ],
                            }))
                          }
                          disabled={actionDisabled || sortedGroups.length === 0}
                        >
                          Add group
                        </button>
                      </div>
                    </div>
                    <div className="field-stack">
                      <span>Direct workflow grants</span>
                      <div className="stacked-list">
                        {form.workflowGrants.map((grant, index) => (
                          <div className="inline-actions" key={`${grant.permission}-${grant.directoryPath}-${index}`}>
                            <select
                              value={grant.permission}
                              onChange={(event) =>
                                setForm((current) => ({
                                  ...current,
                                  workflowGrants: current.workflowGrants.map((item, itemIndex) =>
                                    itemIndex === index
                                      ? {
                                          ...item,
                                          permission: event.target.value as WorkflowGrantFormState["permission"],
                                        }
                                      : item,
                                  ),
                                }))
                              }
                              disabled={actionDisabled}
                            >
                              <option value="workflows.read">Workflow read</option>
                              <option value="workflows.write">Workflow write</option>
                            </select>
                            {renderDirectorySelect(
                              grant.directoryPath,
                              (directoryPath) =>
                                setForm((current) => ({
                                  ...current,
                                  workflowGrants: current.workflowGrants.map((item, itemIndex) =>
                                    itemIndex === index ? { ...item, directoryPath } : item,
                                  ),
                                })),
                              "Workflow grant directory",
                            )}
                            <button
                              className="admin-action-button compact"
                              type="button"
                              onClick={() =>
                                setForm((current) => ({
                                  ...current,
                                  workflowGrants: current.workflowGrants.filter((_, itemIndex) => itemIndex !== index),
                                }))
                              }
                              disabled={actionDisabled}
                            >
                              Remove
                            </button>
                          </div>
                        ))}
                        <button
                          className="admin-action-button compact"
                          type="button"
                          onClick={() =>
                            setForm((current) => ({
                              ...current,
                              workflowGrants: [
                                ...current.workflowGrants,
                                { permission: "workflows.read", directoryPath: selectedDirectoryPath },
                              ],
                            }))
                          }
                          disabled={actionDisabled}
                        >
                          Add grant
                        </button>
                      </div>
                    </div>
                  </div>
                  {selectedUser ? (
                    <div className="users-effective-permissions">
                      <span className="muted-text">Effective here:</span>
                      <div className="permission-chip-list" aria-label="Effective permissions in this directory">
                        {(selectedUser.effective_permissions ?? []).length > 0 ? (
                          [...selectedUser.effective_permissions].sort().map((permission) => (
                            <span className="permission-chip" key={permission}>
                              <PermissionTooltip permission={permission} focusable />
                            </span>
                          ))
                        ) : (
                          <span className="muted-text">none</span>
                        )}
                      </div>
                    </div>
                  ) : null}
                </article>

                <article className="panel">
                  <div className="panel-header">
                    <h3>User records</h3>
                    <span className="panel-meta">{users.length} total</span>
                  </div>
                  <div className="table-wrap mobile-paired-table">
                    <table className="admin-table">
                      <thead>
                        <tr>
                          <th>Login</th>
                          <th>Groups</th>
                          <th>Display name</th>
                          <th>Contact</th>
                          <th>Password</th>
                          <th>Last login</th>
                          <th>Updated</th>
                        </tr>
                      </thead>
                      <tbody>
                        {sortedUsers.map((user) => (
                          <tr
                            key={user.login}
                            className={selectedLogin === user.login ? "selected" : ""}
                            onClick={() => setSelectedLogin(user.login)}
                          >
                            <td>{user.login}</td>
                            <td>
                              {(user.group_assignments ?? []).map((assignment) => assignment.group_id).join(", ") ||
                                "None"}
                            </td>
                            <td>{user.display_name || "None"}</td>
                            <td>{user.contact ? user.contact.name : "None"}</td>
                            <td>{user.local_password_enabled ? "Set" : "None"}</td>
                            <td>{formatTimestamp(user.last_login_at)}</td>
                            <td>{formatTimestamp(user.updated_at)}</td>
                          </tr>
                        ))}
                        {sortedUsers.length === 0 ? (
                          <tr>
                            <td className="empty-state-cell" colSpan={7}>
                              No user records.
                            </td>
                          </tr>
                        ) : null}
                      </tbody>
                    </table>
                  </div>
                  <div className="mobile-record-list users-mobile-record-list" aria-label="User records">
                    {sortedUsers.map((user) => (
                      <button
                        key={user.login}
                        type="button"
                        className={`mobile-record-card${selectedLogin === user.login ? " selected" : ""}`}
                        aria-pressed={selectedLogin === user.login}
                        onClick={() => setSelectedLogin(user.login)}
                      >
                        <div className="mobile-record-title-row">
                          <strong>{userLabel(user)}</strong>
                        </div>
                        <div className="mobile-record-row">
                          <span>Login</span>
                          <span>{user.login}</span>
                        </div>
                        <div className="mobile-record-row">
                          <span>Display</span>
                          <span>{user.display_name || "None"}</span>
                        </div>
                        <div className="mobile-record-row">
                          <span>Groups</span>
                          <span>
                            {(user.group_assignments ?? []).map((assignment) => assignment.group_id).join(", ") ||
                              "None"}
                          </span>
                        </div>
                        <div className="mobile-record-row">
                          <span>Contact</span>
                          <span>{user.contact ? user.contact.name : "None"}</span>
                        </div>
                        <div className="mobile-record-row">
                          <span>Password</span>
                          <span>{user.local_password_enabled ? "Set" : "None"}</span>
                        </div>
                        <div className="mobile-record-row">
                          <span>Last login</span>
                          <span>{formatTimestamp(user.last_login_at)}</span>
                        </div>
                        <div className="mobile-record-row">
                          <span>Updated</span>
                          <span>{formatTimestamp(user.updated_at)}</span>
                        </div>
                      </button>
                    ))}
                    {sortedUsers.length === 0 ? (
                      <div className="empty-state mobile-record-empty">No user records.</div>
                    ) : null}
                  </div>
                </article>
              </>
            ) : null}
          </div>

          <div
            id="users-page-groups-panel"
            role="tabpanel"
            className="users-page-mode-panel"
            aria-labelledby="users-page-groups-tab"
            hidden={activeMode !== "groups"}
          >
            {activeMode === "groups" ? (
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
                        className="admin-action-button danger compact"
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

                <article className="panel">
                  <div className="panel-header">
                    <h3>{selectedGroupTitle}</h3>
                    <span className="panel-meta">{selectedGroup?.group_id ?? "new"}</span>
                  </div>
                  <div className="config-field-grid">
                    <label className="field-stack">
                      <span>Group ID</span>
                      <input
                        value={groupForm.groupId}
                        onChange={(event) => setGroupForm((current) => ({ ...current, groupId: event.target.value }))}
                        disabled={actionDisabled || Boolean(selectedGroup)}
                        placeholder="ops-reviewers"
                      />
                    </label>
                    <label className="field-stack">
                      <span>Name</span>
                      <input
                        value={groupForm.displayName}
                        onChange={(event) =>
                          setGroupForm((current) => ({ ...current, displayName: event.target.value }))
                        }
                        disabled={actionDisabled}
                        placeholder="Ops Reviewers"
                      />
                    </label>
                    <label className="field-stack">
                      <span>Description</span>
                      <input
                        value={groupForm.description}
                        onChange={(event) =>
                          setGroupForm((current) => ({ ...current, description: event.target.value }))
                        }
                        disabled={actionDisabled}
                        placeholder="Optional"
                      />
                    </label>
                  </div>
                  <div className="config-field-grid">
                    {permissionCatalog.map((permission) => (
                      <label className="checkbox-row" key={permission}>
                        <input
                          type="checkbox"
                          checked={groupForm.permissions.includes(permission)}
                          onChange={(event) =>
                            setGroupForm((current) => ({
                              ...current,
                              permissions: event.target.checked
                                ? [...new Set([...current.permissions, permission])].sort()
                                : current.permissions.filter((candidate) => candidate !== permission),
                            }))
                          }
                          disabled={actionDisabled}
                        />
                        <PermissionTooltip permission={permission} />
                      </label>
                    ))}
                  </div>
                  <div className="inline-actions">
                    <button
                      type="button"
                      className="admin-action-button primary compact"
                      onClick={() => void saveGroup()}
                      disabled={actionDisabled}
                    >
                      {saving ? "Saving..." : "Save group"}
                    </button>
                  </div>
                </article>
              </>
            ) : null}
          </div>
        </section>
      </div>
    </RecordWorkspace>
  );
}
