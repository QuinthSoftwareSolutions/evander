import { useEffect, useMemo, useState } from "react";

import { fetchJson, formatTimestamp } from "../admin/api";
import { useAdminUi } from "../admin/AdminContext";
import type {
  AdminUserListResponse,
  AdminUserMutationResponse,
  AdminWebUserRecord,
  AdminWebUserRole,
} from "../admin/types";

type UserFormState = {
  login: string;
  displayName: string;
  role: AdminWebUserRole;
};

const EMPTY_FORM: UserFormState = {
  login: "",
  displayName: "",
  role: "regular",
};

function userLabel(user: AdminWebUserRecord): string {
  return user.display_name?.trim() || user.login;
}

export function UsersPage() {
  const { canManageUsers, currentUserId } = useAdminUi();
  const [users, setUsers] = useState<AdminWebUserRecord[]>([]);
  const [selectedLogin, setSelectedLogin] = useState<string | null>(null);
  const [form, setForm] = useState<UserFormState>(EMPTY_FORM);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

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

  const selectedUser = useMemo(
    () => sortedUsers.find((user) => user.login === selectedLogin) ?? null,
    [selectedLogin, sortedUsers],
  );

  async function loadUsers() {
    if (!canManageUsers) {
      return;
    }
    setLoading(true);
    try {
      const payload = await fetchJson<AdminUserListResponse>("/api/admin/users");
      setUsers(payload.users);
      setSelectedLogin((current) => {
        if (current && payload.users.some((user) => user.login === current)) return current;
        return payload.users[0]?.login ?? null;
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
  }, [canManageUsers]);

  useEffect(() => {
    if (!selectedUser) {
      setForm(EMPTY_FORM);
      return;
    }
    setForm({
      login: selectedUser.login,
      displayName: selectedUser.display_name ?? "",
      role: selectedUser.role,
    });
  }, [selectedUser]);

  function beginNewUser() {
    setSelectedLogin(null);
    setForm(EMPTY_FORM);
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
      const body = JSON.stringify({
        login,
        role: form.role,
        display_name: form.displayName.trim() || null,
      });
      const payload = selectedUser
        ? await fetchJson<AdminUserMutationResponse>(
            `/api/admin/users/${encodeURIComponent(selectedUser.login)}`,
            { method: "PATCH", body },
          )
        : await fetchJson<AdminUserMutationResponse>("/api/admin/users", {
            method: "POST",
            body,
          });
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
        `/api/admin/users/${encodeURIComponent(login)}`,
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

  const selectedTitle = selectedUser ? userLabel(selectedUser) : "New user";
  const actionDisabled = loading || saving || !canManageUsers;

  return (
    <div className="feature-page users-page">
      <section className="feature-sidebar">
        <div className="feature-sidebar-header">
          <div>
            <div className="eyebrow">Access</div>
            <h1>Users</h1>
          </div>
          <div className="inline-actions compact-actions">
            <button type="button" onClick={() => void loadUsers()} disabled={loading || !canManageUsers}>
              Refresh
            </button>
            <button type="button" className="primary" onClick={beginNewUser} disabled={!canManageUsers}>
              New user
            </button>
          </div>
        </div>
        {!canManageUsers ? (
          <div className="callout warning-callout">Admin role is required to manage users.</div>
        ) : null}
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
            </button>
          ))}
          {canManageUsers && loading && users.length === 0 ? <div className="empty-state">Loading users...</div> : null}
          {canManageUsers && !loading && users.length === 0 ? <div className="empty-state">No users are configured.</div> : null}
        </div>
      </section>

      <section className="feature-content">
        <div className="feature-toolbar">
          <div>
            <h2>{selectedTitle}</h2>
            <p className="muted-text">
              {selectedUser ? selectedUser.login : "Create an Azure SSO user record."}
            </p>
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

        {status ? <div className="callout success-callout">{status}</div> : null}
        {error ? <div className="callout error-callout">{error}</div> : null}

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
                onChange={(event) =>
                  setForm((current) => ({ ...current, displayName: event.target.value }))
                }
                disabled={actionDisabled}
                placeholder="Optional"
              />
            </label>
            <label className="field-stack">
              <span>Role</span>
              <select
                value={form.role}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    role: event.target.value as AdminWebUserRole,
                  }))
                }
                disabled={actionDisabled}
              >
                <option value="admin">Admin</option>
                <option value="regular">Regular</option>
              </select>
            </label>
          </div>
          <div className="inline-actions">
            <button type="button" className="primary" onClick={() => void saveUser()} disabled={actionDisabled}>
              {saving ? "Saving..." : "Save"}
            </button>
          </div>
        </article>

        <article className="panel">
          <div className="panel-header">
            <h3>User records</h3>
            <span className="panel-meta">{users.length} total</span>
          </div>
          <div className="table-wrap">
            <table className="admin-table">
              <thead>
                <tr>
                  <th>Login</th>
                  <th>Role</th>
                  <th>Display name</th>
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
                      <span className={`status-pill state-${user.role === "admin" ? "running" : "idle"}`}>
                        {user.role}
                      </span>
                    </td>
                    <td>{user.display_name || "None"}</td>
                    <td>{formatTimestamp(user.last_login_at)}</td>
                    <td>{formatTimestamp(user.updated_at)}</td>
                  </tr>
                ))}
                {sortedUsers.length === 0 ? (
                  <tr>
                    <td className="empty-state-cell" colSpan={5}>
                      No user records.
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </article>
      </section>
    </div>
  );
}
