export type AdminTab =
  | "dashboard"
  | "agents"
  | "approvals"
  | "skills"
  | "vms"
  | "issues"
  | "sessions"
  | "tool-calls"
  | "configuration"
  | "reports"
  | "users"
  | "workers"
  | "workflow";

export type AdminTabDefinition = {
  key: AdminTab;
  label: string;
  shortLabel: string;
  description: string;
};

export type AdminHashParamValue = string | number | boolean | null | undefined;

export const TABS: ReadonlyArray<AdminTabDefinition> = [
  {
    key: "dashboard",
    label: "Dashboard",
    shortLabel: "Ops",
    description: "System overview and live operations.",
  },
  { key: "agents", label: "Agents", shortLabel: "Agents", description: "Active and historical execution targets." },
  { key: "approvals", label: "Approvals", shortLabel: "Approvals", description: "Review guarded approval requests and queued decisions." },
  { key: "skills", label: "Skills", shortLabel: "Skills", description: "Managed Codex skill library." },
  { key: "vms", label: "VMs", shortLabel: "VMs", description: "VM host lifecycle and machine details." },
  { key: "issues", label: "Issues", shortLabel: "Issues", description: "Local issue intake and VM creation." },
  { key: "sessions", label: "Sessions", shortLabel: "Sessions", description: "Session state, tools, and wake schedules." },
  { key: "tool-calls", label: "Tool Calls", shortLabel: "Calls", description: "Search MCP tool invocation audit entries." },
  { key: "configuration", label: "Configuration", shortLabel: "Config", description: "Directory-scoped config and policy." },
  { key: "reports", label: "Reports", shortLabel: "Reports", description: "Workflow activity and operational reporting." },
  { key: "users", label: "Users", shortLabel: "Users", description: "Azure SSO users and roles." },
  { key: "workers", label: "Workers", shortLabel: "Workers", description: "Background worker state, logs, and settings." },
  { key: "workflow", label: "Workflow", shortLabel: "Flow", description: "Workflow editor and runtime activity." },
];

const TAB_ALIASES: Readonly<Record<string, AdminTab>> = {
  settings: "configuration",
};

export function tabsForExecutionBackend(
  executionBackend: "libvirt_vm" | "container_worker_vm" | null,
): ReadonlyArray<AdminTabDefinition> {
  return TABS.filter((tab) => tab.key !== "vms" || executionBackend === "libvirt_vm");
}

export function tabFromHash(hash: string): AdminTab | null {
  const raw = hash.replace(/^#/, "").trim();
  const tabName = raw.split("?")[0]?.trim() ?? "";
  if (!tabName) {
    return null;
  }
  const resolvedTab = TAB_ALIASES[tabName] ?? tabName;
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
