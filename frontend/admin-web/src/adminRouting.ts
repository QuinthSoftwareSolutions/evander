export type BuiltinAdminTab =
  | "dashboard"
  | "agents"
  | "subscriptions"
  | "approvals"
  | "artifacts"
  | "addons"
  | "connectors"
  | "host-directories"
  | "git-repositories"
  | "skills"
  | "vms"
  | "issues"
  | "sessions"
  | "tool-visibility"
  | "mcp-tools"
  | "tool-calls"
  | "configuration"
  | "reports"
  | "users"
  | "workers"
  | "workflow";

export type AdminTab = BuiltinAdminTab | `addon:${string}`;

export type AdminNavigationGroup =
  | "overview"
  | "operations"
  | "automation"
  | "extensions"
  | "infrastructure"
  | "governance";

export type AdminNavigationGroupDefinition = {
  key: AdminNavigationGroup;
  label: string;
};

export type AdminTabDefinition = {
  key: AdminTab;
  label: string;
  shortLabel: string;
  description: string;
  group: AdminNavigationGroup;
  addonId?: string;
  apiBasePath?: string;
  uiPath?: string | null;
};

export type AdminAddonNavigationPayload = {
  addon_id: string;
  display_name: string;
  tab_key: `addon:${string}`;
  label: string;
  short_label: string;
  description: string;
  api_base_path: string;
  ui_path?: string | null;
};

export type AdminHashParamValue = string | number | boolean | null | undefined;
export type AdminUsersMode = "users" | "groups";

export const ADMIN_NAVIGATION_GROUPS: ReadonlyArray<AdminNavigationGroupDefinition> = [
  { key: "overview", label: "Overview" },
  { key: "operations", label: "Operations" },
  { key: "automation", label: "Automation" },
  { key: "extensions", label: "Extensions" },
  { key: "infrastructure", label: "Infrastructure" },
  { key: "governance", label: "Governance" },
];

export const TABS: ReadonlyArray<AdminTabDefinition> = [
  {
    key: "dashboard",
    label: "Dashboard",
    shortLabel: "Ops",
    description: "System overview and live operations.",
    group: "overview",
  },
  { key: "reports", label: "Reports", shortLabel: "Reports", description: "Workflow activity and operational reporting.", group: "overview" },
  { key: "agents", label: "Agents", shortLabel: "Agents", description: "Active and historical execution targets.", group: "operations" },
  { key: "subscriptions", label: "Subscriptions", shortLabel: "Subs", description: "Worker-owned ChatGPT subscription profiles and capacity.", group: "infrastructure" },
  { key: "sessions", label: "Sessions", shortLabel: "Sessions", description: "Session state, tools, and wake schedules.", group: "operations" },
  { key: "issues", label: "Issues", shortLabel: "Issues", description: "Local issue intake and issue records.", group: "operations" },
  { key: "approvals", label: "Approvals", shortLabel: "Approvals", description: "Review guarded approval requests and queued decisions.", group: "operations" },
  { key: "artifacts", label: "Artifacts", shortLabel: "Artifacts", description: "Resolve and download durable agent artifact archives.", group: "operations" },
  { key: "workflow", label: "Workflow", shortLabel: "Flow", description: "Workflow editor and runtime activity.", group: "automation" },
  { key: "skills", label: "Skills", shortLabel: "Skills", description: "Managed Codex skill library.", group: "automation" },
  { key: "addons", label: "Add-ons", shortLabel: "Add-ons", description: "Enable, disable, and inspect runtime add-ons.", group: "extensions" },
  { key: "connectors", label: "Connectors", shortLabel: "Connect", description: "Configure approved files and REST integrations for agent sessions.", group: "extensions" },
  { key: "host-directories", label: "Host Directories", shortLabel: "Files", description: "Manage directory-scoped host filesystem resources and recovery.", group: "extensions" },
  { key: "git-repositories", label: "Git Repositories", shortLabel: "Git", description: "Register and manage directory-scoped repositories for agent code transport.", group: "infrastructure" },
  { key: "vms", label: "VMs", shortLabel: "VMs", description: "VM host lifecycle and machine details.", group: "infrastructure" },
  { key: "workers", label: "Workers", shortLabel: "Workers", description: "Background worker state, logs, and settings.", group: "infrastructure" },
  { key: "tool-visibility", label: "Tool Visibility", shortLabel: "Visibility", description: "Effective tool visibility, transport binding, and denied execution evidence.", group: "governance" },
  { key: "mcp-tools", label: "MCP Tools", shortLabel: "MCP", description: "Directory-scoped MCP tool approval and deny policy.", group: "governance" },
  { key: "tool-calls", label: "Tool Calls", shortLabel: "Calls", description: "Search MCP tool invocation audit entries.", group: "governance" },
  { key: "configuration", label: "Configuration", shortLabel: "Config", description: "Directory-scoped config and policy.", group: "governance" },
  {
    key: "users",
    label: "Users & Groups",
    shortLabel: "Users",
    description: "Admin users, groups, and permissions.",
    group: "governance",
  },
];

const TAB_ALIASES: Readonly<Record<string, BuiltinAdminTab>> = {
  settings: "configuration",
};

const ADDON_TAB_PATTERN = /^addon:[a-z0-9][a-z0-9._-]{0,127}$/;
const ADDON_IFRAME_HASH_PARAM_NAMES = [
  "view",
  "human_review_request_id",
  "conversation_id",
  "case_id",
  "team_id",
  "project_id",
] as const;

export function tabsForExecutionBackend(
  executionBackend: "libvirt_vm" | "container_worker_vm" | null,
  addonTabs: ReadonlyArray<AdminTabDefinition> = [],
): ReadonlyArray<AdminTabDefinition> {
  return [
    ...TABS.filter((tab) => tab.key !== "vms" || executionBackend === "libvirt_vm"),
    ...addonTabs,
  ];
}

export function tabFromHash(hash: string): AdminTab | null {
  const raw = hash.replace(/^#/, "").trim();
  const path = raw.split("?")[0]?.trim() ?? "";
  const [baseTab = ""] = path.split("/");
  if (!baseTab) {
    return null;
  }
  const resolvedTab = TAB_ALIASES[baseTab] ?? baseTab;
  if (resolvedTab === "users") {
    return "users";
  }
  if (path.includes("/")) {
    return null;
  }
  if (ADDON_TAB_PATTERN.test(resolvedTab)) {
    return resolvedTab as AdminTab;
  }
  return TABS.some((tab) => tab.key === resolvedTab) ? (resolvedTab as AdminTab) : null;
}

export function currentTab(hash: string): AdminTab {
  return tabFromHash(hash) ?? "dashboard";
}

export function nextPathFromHash(hash: string): string | null {
  const normalizedHash = hash.trim();
  if (!tabFromHash(normalizedHash)) {
    return null;
  }
  return `/${normalizedHash.startsWith("#") ? normalizedHash : `#${normalizedHash}`}`;
}

export function adminHash(tab: AdminTab, params: Record<string, AdminHashParamValue> = {}): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value == null) continue;
    const text = typeof value === "string" ? value.trim() : String(value);
    if (!text) continue;
    query.set(key, text);
  }
  const queryText = query.toString();
  return `#${tab}${queryText ? `?${queryText}` : ""}`;
}

export function addonTabsFromPayload(
  addons: ReadonlyArray<AdminAddonNavigationPayload> | null | undefined,
): ReadonlyArray<AdminTabDefinition> {
  if (!addons?.length) {
    return [];
  }
  const tabs: AdminTabDefinition[] = [];
  const seen = new Set<string>();
  for (const addon of addons) {
    if (!ADDON_TAB_PATTERN.test(addon.tab_key) || seen.has(addon.tab_key)) {
      continue;
    }
    seen.add(addon.tab_key);
    tabs.push({
      key: addon.tab_key,
      label: addon.label || addon.display_name || addon.addon_id,
      shortLabel: addon.short_label || addon.label || addon.addon_id,
      description: addon.description || addon.display_name || addon.addon_id,
      group: "extensions",
      addonId: addon.addon_id,
      apiBasePath: addon.api_base_path,
      uiPath: addon.ui_path ?? null,
    });
  }
  return tabs;
}

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

export function hashQuery(hash: string): URLSearchParams {
  const raw = hash.replace(/^#/, "").trim();
  const queryStart = raw.indexOf("?");
  if (queryStart < 0) {
    return new URLSearchParams();
  }
  return new URLSearchParams(raw.slice(queryStart + 1));
}

export function hashQueryParam(hash: string, name: string): string | null {
  const value = hashQuery(hash).get(name);
  const normalized = value?.trim() ?? "";
  return normalized || null;
}

export function updateHashQueryParam(
  hash: string,
  name: string,
  value: AdminHashParamValue,
): string {
  const raw = hash.replace(/^#/, "").trim();
  const path = raw.split("?")[0]?.trim() || "dashboard";
  const query = hashQuery(hash);
  const text = typeof value === "string" ? value.trim() : value == null ? "" : String(value);
  if (text) {
    query.set(name, text);
  } else {
    query.delete(name);
  }
  const queryText = query.toString();
  return `#${path}${queryText ? `?${queryText}` : ""}`;
}

export function addonIframeHashQuery(
  hash: string,
  addonTab: AdminTab,
): string {
  if (!addonTab.startsWith("addon:") || tabFromHash(hash) !== addonTab) {
    return "";
  }
  const source = hashQuery(hash);
  const forwarded = new URLSearchParams();
  for (const name of ADDON_IFRAME_HASH_PARAM_NAMES) {
    const value = source.get(name)?.trim() ?? "";
    if (value) {
      forwarded.set(name, value);
    }
  }
  return forwarded.toString();
}
