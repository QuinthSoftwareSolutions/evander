import { FormEvent, useEffect, useState, type ReactNode } from "react";

import logoDark from "../logo-dark.png";
import logoLight from "../logo-light.png";
import WorkflowEditorApp from "./App";
import { fetchJson } from "./features/admin/api";
import { AdminUiContext } from "./features/admin/AdminContext";
import type { AdminResult, DirectoryTreeNode } from "./features/admin/AdminContext";
import { ConfigurationScreen } from "./features/configuration/ConfigurationScreen";
import { IssuesPage } from "./features/issues/IssuesPage";
import { SessionsScreen } from "./features/sessions/SessionsScreen";
import { VmsPage } from "./features/vms/VmsPage";
import { WorkersScreen } from "./features/workers/WorkersScreen";
import { Tooltip } from "./features/admin/Tooltip";

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

type DirectoryMutationResult = AdminResult<unknown>;

type AdminTab =
  | "vms"
  | "issues"
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

function expandedDirectoryAncestors(path: string): string[] {
  const normalized = path.trim() || "root";
  if (normalized === "root") {
    return ["root"];
  }
  const segments = normalized.split("/").filter(Boolean);
  const ancestors = ["root"];
  let current = "";
  for (const segment of segments) {
    current = current ? `${current}/${segment}` : segment;
    ancestors.push(current);
  }
  return ancestors;
}

function findDirectoryNode(root: DirectoryTreeNode | null, path: string): DirectoryTreeNode | null {
  if (!root) return null;
  if (root.directory.path === path) return root;
  for (const child of root.children) {
    const match = findDirectoryNode(child, path);
    if (match) {
      return match;
    }
  }
  return null;
}

function DirectoryActionIcon({ children, viewBox = "0 0 24 24" }: { children: ReactNode; viewBox?: string }) {
  return (
    <svg aria-hidden="true" viewBox={viewBox} fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
      {children}
    </svg>
  );
}

function DirectoryAddIcon() {
  return (
    <DirectoryActionIcon>
      <path d="M12 5v14" />
      <path d="M5 12h14" />
    </DirectoryActionIcon>
  );
}

function DirectoryDeleteIcon() {
  return (
    <DirectoryActionIcon>
      <path d="M4.5 7.5h15" />
      <path d="M9.5 4.5h5" />
      <path d="M8.5 7.5v10" />
      <path d="M15.5 7.5v10" />
      <path d="M6.5 7.5l.8 11a1.5 1.5 0 0 0 1.5 1.4h6.4a1.5 1.5 0 0 0 1.5-1.4l.8-11" />
    </DirectoryActionIcon>
  );
}

function DirectoryTreeBranch({
  node,
  selectedPath,
  expandedPaths,
  onToggle,
  canCreate,
  canDelete,
  mutationBusy,
  onRequestCreate,
  onRequestDelete,
  onSelect,
}: {
  node: DirectoryTreeNode;
  selectedPath: string;
  expandedPaths: Record<string, boolean>;
  onToggle: (path: string) => void;
  canCreate: boolean;
  canDelete: boolean;
  mutationBusy: boolean;
  onRequestCreate: (path: string) => void;
  onRequestDelete: (path: string) => void;
  onSelect: (path: string) => void;
}) {
  const hasChildren = node.children.length > 0;
  const isExpanded = expandedPaths[node.directory.path] ?? false;
  const canDeleteNode = canDelete && node.directory.path !== "root" && !hasChildren;
  return (
    <li>
      <div className="directory-tree-row">
        {hasChildren ? (
          <button
            type="button"
            className="directory-tree-toggle"
            aria-label={isExpanded ? `Collapse ${node.directory.path}` : `Expand ${node.directory.path}`}
            aria-expanded={isExpanded}
            onClick={() => onToggle(node.directory.path)}
          >
            <span aria-hidden="true">{isExpanded ? "▾" : "▸"}</span>
          </button>
        ) : (
          <span className="directory-tree-spacer" aria-hidden="true" />
        )}
        <button
          type="button"
          className={`directory-tree-button${node.directory.path === selectedPath ? " selected" : ""}`}
          onClick={() => onSelect(node.directory.path)}
        >
          <span className="directory-tree-label">{node.directory.path}</span>
        </button>
        <div className="directory-tree-actions">
          {canCreate ? (
            <Tooltip content={`Create a child directory under ${node.directory.path}.`}>
              <button
                type="button"
                className="directory-tree-action-button"
                aria-label={`Create child directory under ${node.directory.path}`}
                onClick={() => onRequestCreate(node.directory.path)}
                disabled={mutationBusy}
              >
                <DirectoryAddIcon />
              </button>
            </Tooltip>
          ) : null}
          {canDeleteNode ? (
            <Tooltip content={`Delete ${node.directory.path}. Only empty directories can be removed.`}>
              <button
                type="button"
                className="directory-tree-action-button danger"
                aria-label={`Delete ${node.directory.path}`}
                onClick={() => onRequestDelete(node.directory.path)}
                disabled={mutationBusy}
              >
                <DirectoryDeleteIcon />
              </button>
            </Tooltip>
          ) : null}
        </div>
      </div>
      {hasChildren && isExpanded ? (
        <ul className="directory-tree-list">
          {node.children.map((child) => (
            <DirectoryTreeBranch
              key={child.directory.path}
              node={child}
              selectedPath={selectedPath}
              expandedPaths={expandedPaths}
              onToggle={onToggle}
              canCreate={canCreate}
              canDelete={canDelete}
              mutationBusy={mutationBusy}
              onRequestCreate={onRequestCreate}
              onRequestDelete={onRequestDelete}
              onSelect={onSelect}
            />
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
  privilegedUnlocked,
  directoryScopedConfigEnabled,
  tree,
  onClose,
  onRefreshState,
  onSelect,
}: {
  open: boolean;
  loading: boolean;
  error: string | null;
  selectedPath: string;
  privilegedUnlocked: boolean;
  directoryScopedConfigEnabled: boolean;
  tree: AdminResult<DirectoryTreeNode> | null;
  onClose: () => void;
  onRefreshState: (path?: string) => Promise<void>;
  onSelect: (path: string) => void;
}) {
  const [expandedPaths, setExpandedPaths] = useState<Record<string, boolean>>({});
  const [createParentPath, setCreateParentPath] = useState<string | null>(null);
  const [newDirectoryName, setNewDirectoryName] = useState("");
  const [mutationBusy, setMutationBusy] = useState(false);
  const [mutationError, setMutationError] = useState<string | null>(null);
  const [mutationStatus, setMutationStatus] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      return;
    }
    setExpandedPaths((current) => {
      const next = { ...current };
      for (const path of expandedDirectoryAncestors(selectedPath)) {
        next[path] = true;
      }
      return next;
    });
  }, [open, selectedPath]);

  if (!open) return null;
  const root = tree?.ok ? tree.data : null;
  const canCreate = directoryScopedConfigEnabled && privilegedUnlocked;
  const canDelete = directoryScopedConfigEnabled && privilegedUnlocked;

  async function handleCreateDirectory() {
    if (!createParentPath) {
      return;
    }
    const name = newDirectoryName.trim();
    if (!name) {
      setMutationError("Directory name is required.");
      return;
    }
    setMutationBusy(true);
    setMutationError(null);
    setMutationStatus(null);
    try {
      const result = await fetchJson<DirectoryMutationResult>("/api/configuration/directories", {
        method: "POST",
        body: JSON.stringify({ parent_path: createParentPath, name }),
      });
      if (!result.ok) {
        throw new Error(result.errors[0]?.message ?? "Directory create failed.");
      }
      const createdPath = `${createParentPath}/${name}`;
      setNewDirectoryName("");
      setCreateParentPath(null);
      await onRefreshState(createdPath);
      setExpandedPaths((current) => {
        const next = { ...current };
        for (const path of expandedDirectoryAncestors(createdPath)) {
          next[path] = true;
        }
        return next;
      });
      setMutationStatus(`Created ${createdPath}.`);
    } catch (reason) {
      setMutationError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setMutationBusy(false);
    }
  }

  async function handleDeleteDirectory(path: string) {
    const node = findDirectoryNode(root, path);
    if (!canDelete || !node || path === "root" || node.children.length > 0) {
      return;
    }
    if (
      !window.confirm(
        `Delete ${path}?\n\nDeletion is only allowed for non-root directories with no child directories and no VMs with sessions.`,
      )
    ) {
      return;
    }
    setMutationBusy(true);
    setMutationError(null);
    setMutationStatus(null);
    try {
      const result = await fetchJson<DirectoryMutationResult>(
        `/api/configuration/directories?directory_path=${encodeURIComponent(path)}`,
        { method: "DELETE" },
      );
      if (!result.ok) {
        throw new Error(result.errors[0]?.message ?? "Directory delete failed.");
      }
      const deletedPath = path;
      const parentPath = path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "root";
      await onRefreshState(selectedPath === path ? parentPath || "root" : selectedPath);
      setMutationStatus(`Deleted ${deletedPath}.`);
    } catch (reason) {
      setMutationError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setMutationBusy(false);
    }
  }

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
          {mutationStatus ? <div className="callout success-callout">{mutationStatus}</div> : null}
          {mutationError ? <div className="callout error-callout">{mutationError}</div> : null}
          {error ? <div className="callout error-callout">{error}</div> : null}
          {loading && !root ? <div className="empty-state">Loading directories…</div> : null}
          {!loading && tree && !tree.ok ? <div className="empty-state">The directory tree could not be loaded.</div> : null}
          {root ? (
            <ul className="directory-tree-list root">
              <DirectoryTreeBranch
                node={root}
                selectedPath={selectedPath}
                expandedPaths={expandedPaths}
                onToggle={(path) => setExpandedPaths((current) => ({ ...current, [path]: !(current[path] ?? false) }))}
                canCreate={canCreate}
                canDelete={canDelete}
                mutationBusy={loading || mutationBusy}
                onRequestCreate={(path) => {
                  setCreateParentPath(path);
                  setNewDirectoryName("");
                  setMutationError(null);
                }}
                onRequestDelete={(path) => void handleDeleteDirectory(path)}
                onSelect={onSelect}
              />
            </ul>
          ) : null}
        </div>
      </div>
      {createParentPath ? (
        <div className="modal-backdrop nested-modal-backdrop" onClick={() => setCreateParentPath(null)} role="presentation">
          <div className="modal-card directory-create-modal" onClick={(event) => event.stopPropagation()} role="dialog" aria-modal="true">
            <div className="modal-header">
              <div>
                <div className="eyebrow">Directory</div>
                <h2>Create child directory</h2>
                <p>Create a child under {createParentPath}.</p>
              </div>
            </div>
            <div className="modal-body">
              <div className="field-stack">
                <input
                  type="text"
                  value={newDirectoryName}
                  placeholder="New child directory"
                  onChange={(event) => setNewDirectoryName(event.target.value)}
                  disabled={mutationBusy}
                  autoFocus
                />
              </div>
              <div className="inline-actions">
                <button type="button" className="admin-shell-button admin-shell-button-muted" onClick={() => setCreateParentPath(null)} disabled={mutationBusy}>
                  Cancel
                </button>
                <button type="button" className="admin-shell-button" onClick={() => void handleCreateDirectory()} disabled={mutationBusy}>
                  {mutationBusy ? "Working..." : "Create"}
                </button>
              </div>
            </div>
          </div>
        </div>
      ) : null}
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
            <div className="admin-shell-brand-lockup">
              <div className="admin-shell-brand-copy">
                <div className="admin-shell-title-row">
                  <img
                    className="admin-shell-logo"
                    src={theme === "dark" ? logoDark : logoLight}
                    alt="Castor Control Plane logo"
                  />
                  <h1>Castor Control Plane</h1>
                </div>
                <p>{activeTab.description}</p>
              </div>
            </div>
          </div>
          <div className="admin-shell-actions">
            <div className="admin-shell-action-group">
              <button
                className="admin-shell-button admin-shell-button-muted"
                onClick={() => setTheme((current) => (current === "dark" ? "light" : "dark"))}
                disabled={authBusy}
              >
                {theme === "dark" ? "Light mode" : "Dark mode"}
              </button>
              <button
                className="admin-shell-button admin-shell-button-muted"
                onClick={() => setDirectoryPickerOpen(true)}
                disabled={authBusy}
              >
                Choose directory
              </button>
            </div>
            <div className="admin-shell-info-group">
              <div className="admin-directory-context">
                <span className="section-label">Directory</span>
                <strong>{selectedDirectoryPath}</strong>
              </div>
              <div className="admin-identity">
                <span>{session.username ?? "local operator"}</span>
                <span className={`admin-privilege ${privilegeUnlocked ? "unlocked" : ""}`}>
                  {privilegeUnlocked ? "Privileges unlocked" : "Privileges locked"}
                </span>
              </div>
            </div>
            <div className="admin-shell-action-group">
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
          </div>
        </header>

        <div className="admin-shell-layout">
          <nav className={`admin-shell-nav${workflowSidebarCollapsed ? " collapsed" : ""}`} aria-label="Admin sections">
            <div className="admin-shell-nav-header">
              <div className="admin-shell-nav-header-row">
                <div className="section-label">Sections</div>
                <Tooltip content={workflowSidebarCollapsed ? "Expand the left navigation sections." : "Collapse the left navigation sections."}>
                  <button
                    type="button"
                    className="admin-shell-nav-toggle"
                    onClick={() => setWorkflowSidebarCollapsed((current) => !current)}
                    disabled={authBusy}
                    aria-label={workflowSidebarCollapsed ? "Expand sections" : "Collapse sections"}
                  >
                    <span aria-hidden="true">≡</span>
                  </button>
                </Tooltip>
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
                <span>{workflowSidebarCollapsed ? tab.shortLabel : tab.label}</span>
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
        privilegedUnlocked={privilegeUnlocked}
        directoryScopedConfigEnabled={adminState?.directory_scoped_config_enabled ?? false}
        tree={directoryTree}
        onClose={() => setDirectoryPickerOpen(false)}
        onRefreshState={(path) => loadDirectoryState(path ?? selectedDirectoryPath)}
        onSelect={(path) => {
          setSelectedDirectoryPath(path);
          setDirectoryPickerOpen(false);
        }}
      />
    </AdminUiContext.Provider>
  );
}
