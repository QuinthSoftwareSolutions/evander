import { FormEvent, useEffect, useState } from "react";

import WorkflowEditorApp from "./App";
import { fetchJson } from "./features/admin/api";
import { AdminUiContext } from "./features/admin/AdminContext";
import type { AdminResult, DirectoryTreeNode } from "./features/admin/AdminContext";
import { ApprovalsScreen } from "./features/approvals/ApprovalsScreen";
import { ConfigurationScreen } from "./features/configuration/ConfigurationScreen";
import { IssuesPage } from "./features/issues/IssuesPage";
import { SessionsScreen } from "./features/sessions/SessionsScreen";
import { VmsPage } from "./features/vms/VmsPage";
import { WorkersScreen } from "./features/workers/WorkersScreen";

type AdminSession = {
  auth_required: boolean;
  authenticated: boolean;
  username: string | null;
  session_id: string | null;
  privileged_operations: {
    unlocked: boolean;
  };
};

type AdminState = {
  selected_directory_path: string;
  directory_scoped_config_enabled: boolean;
  privileged_operations: {
    unlocked: boolean;
  };
};

type DirectoryStateResponse = {
  directory_scoped_config_enabled: boolean;
  selected_directory_path: string;
  selected_directory: {
    path: string;
    parent_path: string | null;
    can_create: boolean;
    can_delete: boolean;
  };
  tree: AdminResult<DirectoryTreeNode>;
};

type AdminTab =
  | "vms"
  | "issues"
  | "approvals"
  | "sessions"
  | "configuration"
  | "workers"
  | "workflow";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";
const WORKFLOW_SIDEBAR_STORAGE_KEY = "admin-web:workflow:sections-collapsed";
const DIRECTORY_STORAGE_KEY = "admin-web:selected-directory";
const THEME_STORAGE_KEY = "admin-web:theme";

const TABS: ReadonlyArray<{ key: AdminTab; label: string; shortLabel: string; description: string }> = [
  { key: "vms", label: "VMs", shortLabel: "VMs", description: "Lifecycle, logs, and agent control." },
  { key: "issues", label: "Issues", shortLabel: "Issues", description: "Local issue intake and VM creation." },
  { key: "approvals", label: "Approvals", shortLabel: "Review", description: "Queued review requests and decisions." },
  { key: "sessions", label: "Sessions", shortLabel: "Sessions", description: "Session state, tools, and wake schedules." },
  { key: "configuration", label: "Configuration", shortLabel: "Config", description: "Directory-scoped config and policy." },
  { key: "workers", label: "Workers", shortLabel: "Workers", description: "Background worker state, logs, and settings." },
  { key: "workflow", label: "Workflow", shortLabel: "Flow", description: "Workflow editor and runtime activity." },
];

function currentTab(): AdminTab {
  const raw = window.location.hash.replace(/^#/, "").trim();
  if (TABS.some((tab) => tab.key === raw)) {
    return raw as AdminTab;
  }
  return "workflow";
}

type AdminTheme = "light" | "dark";

function getInitialTheme(): AdminTheme {
  if (typeof window === "undefined") return "light";
  const storedTheme = window.localStorage.getItem(THEME_STORAGE_KEY);
  if (storedTheme === "light" || storedTheme === "dark") {
    return storedTheme;
  }
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function PlaceholderView({ title, description }: { title: string; description: string }) {
  return (
    <div className="admin-placeholder">
      <div className="admin-placeholder-card">
        <div className="eyebrow">In Progress</div>
        <h2>{title}</h2>
        <p>{description}</p>
        <p>This area is wired into the new admin shell and waiting for its dedicated feature module.</p>
      </div>
    </div>
  );
}

function DirectoryTreeBranch({
  node,
  selectedPath,
  onSelect,
}: {
  node: DirectoryTreeNode;
  selectedPath: string;
  onSelect: (path: string) => void;
}) {
  return (
    <li>
      <button
        type="button"
        className={`directory-tree-button${node.directory.path === selectedPath ? " selected" : ""}`}
        onClick={() => onSelect(node.directory.path)}
      >
        <span>{node.directory.path}</span>
      </button>
      {node.children.length > 0 ? (
        <ul className="directory-tree-list">
          {node.children.map((child) => (
            <DirectoryTreeBranch key={child.directory.path} node={child} selectedPath={selectedPath} onSelect={onSelect} />
          ))}
        </ul>
      ) : null}
    </li>
  );
}

function DirectoryChooserModal({
  open,
  loading,
  error,
  selectedPath,
  tree,
  onClose,
  onRefresh,
  onSelect,
}: {
  open: boolean;
  loading: boolean;
  error: string | null;
  selectedPath: string;
  tree: AdminResult<DirectoryTreeNode> | null;
  onClose: () => void;
  onRefresh: () => Promise<void>;
  onSelect: (path: string) => void;
}) {
  if (!open) return null;
  const root = tree?.ok ? tree.data : null;
  return (
    <div className="modal-backdrop" onClick={onClose} role="presentation">
      <div
        className="modal-card directory-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="directory-modal-title"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="modal-header">
          <div>
            <div className="eyebrow">Directory chooser</div>
            <h2 id="directory-modal-title">Choose directory</h2>
            <p>Select the shared directory context for directory-scoped admin views.</p>
          </div>
          <div className="inline-actions">
            <button type="button" onClick={() => void onRefresh()} disabled={loading}>
              {loading ? "Refreshing..." : "Refresh"}
            </button>
            <button type="button" className="admin-shell-button admin-shell-button-muted" onClick={onClose}>
              Close
            </button>
          </div>
        </div>
        <div className="modal-body">
          <div className="directory-modal-summary">
            <span className="section-label">Current directory</span>
            <strong>{selectedPath}</strong>
          </div>
          {error ? <div className="callout error-callout">{error}</div> : null}
          {loading && !root ? <div className="empty-state">Loading directories…</div> : null}
          {!loading && tree && !tree.ok ? <div className="empty-state">The directory tree could not be loaded.</div> : null}
          {root ? (
            <ul className="directory-tree-list root">
              <DirectoryTreeBranch node={root} selectedPath={selectedPath} onSelect={onSelect} />
            </ul>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function LoginScreen({
  theme,
  onToggleTheme,
  error,
  loading,
  onSubmit,
}: {
  theme: AdminTheme;
  onToggleTheme: () => void;
  error: string | null;
  loading: boolean;
  onSubmit: (username: string, password: string) => Promise<void>;
}) {
  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formData = new FormData(event.currentTarget);
    await onSubmit(String(formData.get("username") ?? ""), String(formData.get("password") ?? ""));
  }

  return (
    <div className="admin-login-shell">
      <div className="admin-login-toolbar">
        <button type="button" className="admin-shell-button admin-shell-button-muted" onClick={onToggleTheme}>
          {theme === "dark" ? "Light mode" : "Dark mode"}
        </button>
      </div>
      <form className="admin-login-card" onSubmit={handleSubmit}>
        <div className="eyebrow">Agent Sandbox</div>
        <h1>Admin Sign In</h1>
        <p>Authenticate to access the multi-user admin surface.</p>
        <label className="inspector-label">
          <span>Username</span>
          <input name="username" defaultValue="admin" autoComplete="username" />
        </label>
        <label className="inspector-label">
          <span>Password</span>
          <input name="password" type="password" autoComplete="current-password" />
        </label>
        {error ? <div className="admin-login-error">{error}</div> : null}
        <button className="primary" type="submit" disabled={loading}>
          {loading ? "Signing in..." : "Sign in"}
        </button>
      </form>
    </div>
  );
}

export default function AdminRoot() {
  const [theme, setTheme] = useState<AdminTheme>(() => getInitialTheme());
  const [session, setSession] = useState<AdminSession | null>(null);
  const [sessionError, setSessionError] = useState<string | null>(null);
  const [authBusy, setAuthBusy] = useState(false);
  const [selectedTab, setSelectedTab] = useState<AdminTab>(() => currentTab());
  const [adminState, setAdminState] = useState<AdminState | null>(null);
  const [selectedDirectoryPath, setSelectedDirectoryPath] = useState<string>(() => {
    if (typeof window === "undefined") return "root";
    return window.localStorage.getItem(DIRECTORY_STORAGE_KEY) || "root";
  });
  const [selectedDirectoryParentPath, setSelectedDirectoryParentPath] = useState<string | null>(null);
  const [directoryTree, setDirectoryTree] = useState<AdminResult<DirectoryTreeNode> | null>(null);
  const [directoryStateLoading, setDirectoryStateLoading] = useState(false);
  const [directoryStateError, setDirectoryStateError] = useState<string | null>(null);
  const [directoryPickerOpen, setDirectoryPickerOpen] = useState(false);
  const [workflowSidebarCollapsed, setWorkflowSidebarCollapsed] = useState<boolean>(() => {
    if (typeof window === "undefined") return true;
    return window.localStorage.getItem(WORKFLOW_SIDEBAR_STORAGE_KEY) !== "false";
  });

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);
  }, [theme]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    const storedTheme = window.localStorage.getItem(THEME_STORAGE_KEY);
    if (storedTheme === "light" || storedTheme === "dark") {
      return;
    }
    const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
    const handleChange = (event: MediaQueryListEvent) => {
      setTheme(event.matches ? "dark" : "light");
    };
    mediaQuery.addEventListener("change", handleChange);
    return () => mediaQuery.removeEventListener("change", handleChange);
  }, []);

  useEffect(() => {
    function handleHashChange() {
      setSelectedTab(currentTab());
    }
    window.addEventListener("hashchange", handleHashChange);
    return () => window.removeEventListener("hashchange", handleHashChange);
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function loadSession() {
      try {
        const response = await fetch(`${API_BASE}/api/auth/session`, { credentials: "same-origin" });
        const payload = (await response.json()) as AdminSession;
        if (!cancelled) {
          setSession(payload);
          setSessionError(null);
        }
      } catch (error) {
        if (!cancelled) {
          setSessionError(error instanceof Error ? error.message : "Unable to load session.");
        }
      }
    }
    void loadSession();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (session === null || (session.auth_required && !session.authenticated)) {
      return;
    }
    let cancelled = false;
    async function loadAdminState() {
      try {
        const response = await fetch(`${API_BASE}/api/admin/state`, { credentials: "same-origin" });
        const payload = (await response.json()) as AdminState & { error?: string };
        if (!response.ok) {
          throw new Error(payload.error ?? "Unable to load admin state.");
        }
        if (!cancelled) {
          setAdminState(payload);
          if (!window.localStorage.getItem(DIRECTORY_STORAGE_KEY)) {
            setSelectedDirectoryPath(payload.selected_directory_path || "root");
          }
        }
      } catch (error) {
        if (!cancelled) {
          setSessionError(error instanceof Error ? error.message : "Unable to load admin state.");
        }
      }
    }
    void loadAdminState();
    return () => {
      cancelled = true;
    };
  }, [session]);

  useEffect(() => {
    window.localStorage.setItem(WORKFLOW_SIDEBAR_STORAGE_KEY, String(workflowSidebarCollapsed));
  }, [workflowSidebarCollapsed]);

  useEffect(() => {
    window.localStorage.setItem(DIRECTORY_STORAGE_KEY, selectedDirectoryPath);
  }, [selectedDirectoryPath]);

  useEffect(() => {
    if (!directoryPickerOpen) {
      return;
    }
    function handleKeydown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setDirectoryPickerOpen(false);
      }
    }
    window.addEventListener("keydown", handleKeydown);
    return () => window.removeEventListener("keydown", handleKeydown);
  }, [directoryPickerOpen]);

  async function loadDirectoryState(targetPath = selectedDirectoryPath) {
    setDirectoryStateLoading(true);
    try {
      const payload = await fetchJson<DirectoryStateResponse>(`/api/configuration/state?directory_path=${encodeURIComponent(targetPath)}`);
      setSelectedDirectoryPath(payload.selected_directory_path);
      setSelectedDirectoryParentPath(payload.selected_directory.parent_path);
      setDirectoryTree(payload.tree);
      setAdminState((current) =>
        current
          ? {
              ...current,
              selected_directory_path: payload.selected_directory_path,
              directory_scoped_config_enabled: payload.directory_scoped_config_enabled,
            }
          : current,
      );
      setDirectoryStateError(null);
    } catch (error) {
      setDirectoryStateError(error instanceof Error ? error.message : "Unable to load directory state.");
    } finally {
      setDirectoryStateLoading(false);
    }
  }

  useEffect(() => {
    if (session === null || (session.auth_required && !session.authenticated)) {
      return;
    }
    void loadDirectoryState(selectedDirectoryPath);
  }, [session, selectedDirectoryPath]);

  async function signIn(username: string, password: string) {
    setAuthBusy(true);
    setSessionError(null);
    try {
      const response = await fetch(`${API_BASE}/api/auth/login`, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      const payload = (await response.json()) as AdminSession & { error?: string };
      if (!response.ok) {
        throw new Error(payload.error ?? "Sign in failed.");
      }
      setSession(payload);
      setAdminState(null);
    } catch (error) {
      setSessionError(error instanceof Error ? error.message : "Sign in failed.");
    } finally {
      setAuthBusy(false);
    }
  }

  async function signOut() {
    setAuthBusy(true);
    try {
      const response = await fetch(`${API_BASE}/api/auth/logout`, {
        method: "POST",
        credentials: "same-origin",
      });
      const payload = (await response.json()) as AdminSession;
      setSession(payload);
      setAdminState(null);
    } finally {
      setAuthBusy(false);
    }
  }

  async function setPrivilegeLock(unlock: boolean) {
    setAuthBusy(true);
    try {
      const response = await fetch(`${API_BASE}/api/admin/privileged/${unlock ? "unlock" : "lock"}`, {
        method: "POST",
        credentials: "same-origin",
      });
      const payload = (await response.json()) as
        | ({ message: string; privileged_operations: { unlocked: boolean } } & { error?: string })
        | { error: string };
      if (!response.ok) {
        throw new Error("error" in payload ? payload.error : "Privilege update failed.");
      }
      const privilegeState = "privileged_operations" in payload ? payload.privileged_operations : { unlocked: unlock };
      setAdminState((current) =>
        current
          ? {
              ...current,
              privileged_operations: privilegeState,
            }
          : current,
      );
      setSession((current) =>
        current
          ? {
              ...current,
              privileged_operations: privilegeState,
            }
          : current,
      );
      setSessionError(null);
    } catch (error) {
      setSessionError(error instanceof Error ? error.message : "Privilege update failed.");
    } finally {
      setAuthBusy(false);
    }
  }

  if (session === null) {
    return (
      <div className="admin-login-shell">
        <div className="admin-login-toolbar">
          <button
            type="button"
            className="admin-shell-button admin-shell-button-muted"
            onClick={() => setTheme((current) => (current === "dark" ? "light" : "dark"))}
          >
            {theme === "dark" ? "Light mode" : "Dark mode"}
          </button>
        </div>
        <div className="admin-login-card">
          <div className="eyebrow">Agent Sandbox</div>
          <h1>Loading admin shell</h1>
          <p>{sessionError ?? "Checking authentication and bootstrapping the UI."}</p>
        </div>
      </div>
    );
  }

  if (session.auth_required && !session.authenticated) {
    return (
      <LoginScreen
        theme={theme}
        onToggleTheme={() => setTheme((current) => (current === "dark" ? "light" : "dark"))}
        error={sessionError}
        loading={authBusy}
        onSubmit={signIn}
      />
    );
  }

  const activeTab = TABS.find((tab) => tab.key === selectedTab) ?? TABS[TABS.length - 1];
  const privilegeUnlocked = adminState?.privileged_operations.unlocked ?? session.privileged_operations.unlocked;
  const workflowFocus = selectedTab === "workflow";
  const adminUiState = {
    privilegedUnlocked: privilegeUnlocked,
    authBusy,
    directoryScopedConfigEnabled: adminState?.directory_scoped_config_enabled ?? false,
    selectedDirectoryPath,
    selectedDirectoryParentPath,
    directoryTree,
    directoryStateLoading,
    directoryStateError,
    directoryPickerOpen,
    openDirectoryPicker: () => setDirectoryPickerOpen(true),
    closeDirectoryPicker: () => setDirectoryPickerOpen(false),
    setSelectedDirectoryPath: (path: string) => setSelectedDirectoryPath(path || "root"),
    refreshDirectoryState: async () => loadDirectoryState(selectedDirectoryPath),
  };

  return (
    <AdminUiContext.Provider value={adminUiState}>
      <div className={`admin-shell${workflowFocus ? " workflow-focus" : ""}${workflowSidebarCollapsed ? " workflow-sidebar-collapsed" : ""}`}>
        <header className="admin-shell-header">
          <div className="admin-shell-brand">
            <div className="eyebrow">Agent Sandbox</div>
            <h1>Castor Control Plane</h1>
            <p>{activeTab.description}</p>
          </div>
          <div className="admin-shell-actions">
            <button
              className="admin-shell-button admin-shell-button-muted"
              onClick={() => setTheme((current) => (current === "dark" ? "light" : "dark"))}
              disabled={authBusy}
            >
              {theme === "dark" ? "Light mode" : "Dark mode"}
            </button>
            <div className="admin-directory-context">
              <span className="section-label">Directory</span>
              <strong>{selectedDirectoryPath}</strong>
            </div>
            <button
              className="admin-shell-button admin-shell-button-muted"
              onClick={() => setDirectoryPickerOpen(true)}
              disabled={authBusy}
            >
              Choose directory
            </button>
            <div className="admin-identity">
              <span>{session.username ?? "local operator"}</span>
              <span className={`admin-privilege ${privilegeUnlocked ? "unlocked" : ""}`}>
                {privilegeUnlocked ? "Privileges unlocked" : "Privileges locked"}
              </span>
            </div>
            <button
              className="admin-shell-button"
              onClick={() => void setPrivilegeLock(!privilegeUnlocked)}
              disabled={authBusy || adminState === null}
            >
              {privilegeUnlocked ? "Lock" : "Unlock"}
            </button>
            <button className="admin-shell-button admin-shell-button-muted" onClick={() => void signOut()} disabled={authBusy}>
              Sign out
            </button>
          </div>
        </header>

        <div className="admin-shell-layout">
          <nav className={`admin-shell-nav${workflowSidebarCollapsed && workflowFocus ? " collapsed" : ""}`} aria-label="Admin sections">
            <div className="admin-shell-nav-header">
              <div className="admin-shell-nav-header-row">
                <div className="section-label">Sections</div>
                {workflowFocus ? (
                  <button
                    type="button"
                    className="admin-shell-nav-toggle"
                    onClick={() => setWorkflowSidebarCollapsed((current) => !current)}
                    disabled={authBusy}
                    aria-label={workflowSidebarCollapsed ? "Expand sections" : "Collapse sections"}
                    title={workflowSidebarCollapsed ? "Expand sections" : "Collapse sections"}
                  >
                    <span aria-hidden="true">≡</span>
                  </button>
                ) : null}
              </div>
              <p>Operational views for machines, policy, reviews, and workflow state.</p>
            </div>
            {TABS.map((tab) => (
              <button
                key={tab.key}
                className={`admin-shell-tab ${tab.key === selectedTab ? "active" : ""}`}
                onClick={() => {
                  window.location.hash = tab.key;
                  setSelectedTab(tab.key);
                }}
              >
                <span>{workflowSidebarCollapsed && workflowFocus ? tab.shortLabel : tab.label}</span>
                <small>{tab.description}</small>
              </button>
            ))}
          </nav>
          <main className="admin-shell-main">
            {sessionError ? <div className="admin-shell-alert">{sessionError}</div> : null}
            {selectedTab === "workflow" ? (
              <div className="admin-workflow-panel">
                <WorkflowEditorApp />
              </div>
            ) : selectedTab === "vms" ? (
              <VmsPage />
            ) : selectedTab === "issues" ? (
              <IssuesPage />
            ) : selectedTab === "approvals" ? (
              <ApprovalsScreen />
            ) : selectedTab === "configuration" ? (
              <ConfigurationScreen />
            ) : selectedTab === "workers" ? (
              <WorkersScreen />
            ) : selectedTab === "sessions" ? (
              <SessionsScreen />
            ) : (
              <PlaceholderView title={activeTab.label} description={activeTab.description} />
            )}
          </main>
        </div>
      </div>
      <DirectoryChooserModal
        open={directoryPickerOpen}
        loading={directoryStateLoading}
        error={directoryStateError}
        selectedPath={selectedDirectoryPath}
        tree={directoryTree}
        onClose={() => setDirectoryPickerOpen(false)}
        onRefresh={() => loadDirectoryState(selectedDirectoryPath)}
        onSelect={(path) => {
          setSelectedDirectoryPath(path);
          setDirectoryPickerOpen(false);
        }}
      />
    </AdminUiContext.Provider>
  );
}
