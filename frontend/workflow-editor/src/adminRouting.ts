export type BuiltinAdminTab =
  | "dashboard"
  | "agents"
  | "approvals"
  | "artifacts"
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

export type AdminTabDefinition = {
  key: AdminTab;
  label: string;
  shortLabel: string;
  description: string;
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

export const TABS: ReadonlyArray<AdminTabDefinition> = [
  {
    key: "dashboard",
    label: "Dashboard",
    shortLabel: "Ops",
    description: "System overview and live operations.",
  },
  { key: "agents", label: "Agents", shortLabel: "Agents", description: "Active and historical execution targets." },
  { key: "approvals", label: "Approvals", shortLabel: "Approvals", description: "Review guarded approval requests and queued decisions." },
  { key: "artifacts", label: "Artifacts", shortLabel: "Artifacts", description: "Resolve and download durable agent artifact archives." },
  { key: "skills", label: "Skills", shortLabel: "Skills", description: "Managed Codex skill library." },
  { key: "vms", label: "VMs", shortLabel: "VMs", description: "VM host lifecycle and machine details." },
  { key: "issues", label: "Issues", shortLabel: "Issues", description: "Local issue intake and issue records." },
  { key: "sessions", label: "Sessions", shortLabel: "Sessions", description: "Session state, tools, and wake schedules." },
  { key: "tool-visibility", label: "Tool Visibility", shortLabel: "Visibility", description: "Effective tool visibility, transport binding, and denied execution evidence." },
  { key: "mcp-tools", label: "MCP Tools", shortLabel: "MCP", description: "Directory-scoped MCP tool approval and deny policy." },
  { key: "tool-calls", label: "Tool Calls", shortLabel: "Calls", description: "Search MCP tool invocation audit entries." },
  { key: "configuration", label: "Configuration", shortLabel: "Config", description: "Directory-scoped config and policy." },
  { key: "reports", label: "Reports", shortLabel: "Reports", description: "Workflow activity and operational reporting." },
  {
    key: "users",
    label: "Users & Groups",
    shortLabel: "Users",
    description: "Azure SSO users, groups, and permissions.",
  },
  { key: "workers", label: "Workers", shortLabel: "Workers", description: "Background worker state, logs, and settings." },
  { key: "workflow", label: "Workflow", shortLabel: "Flow", description: "Workflow editor and runtime activity." },
];

const TAB_ALIASES: Readonly<Record<string, BuiltinAdminTab>> = {
  settings: "configuration",
};

const ADDON_TAB_PATTERN = /^addon:[a-z0-9][a-z0-9._-]{0,127}$/;

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
