export type WorkflowTargetKey = "target" | "session_id" | "vm_name";

export type WorkflowTargetSession = {
  session_id: string;
  vm_id?: string | null;
  is_expired?: boolean;
};

export type WorkflowTargetVm = {
  vm_name: string;
};

export type WorkflowTargetSessionListResponse = {
  items: WorkflowTargetSession[];
};

export type WorkflowTargetVmListResponse = {
  items: WorkflowTargetVm[];
};

export type WorkflowTargetInventory = {
  sessions: WorkflowTargetSession[];
  vms: WorkflowTargetVm[];
};

export type WorkflowTargetOption = {
  value: string;
  label: string;
};

export const EMPTY_WORKFLOW_TARGET_INVENTORY: WorkflowTargetInventory = {
  sessions: [],
  vms: [],
};

export function workflowTargetInventoryPaths(directoryPath: string): {
  sessions: string;
  vms: string;
} {
  const normalizedDirectoryPath = directoryPath.trim() || "root";
  const sessionQuery = new URLSearchParams({
    directory_path: normalizedDirectoryPath,
    direct_only: "false",
    include_expired: "true",
  });
  const vmQuery = new URLSearchParams({
    directory_path: normalizedDirectoryPath,
    direct_only: "false",
    include_inactive: "true",
  });
  return {
    sessions: `/api/admin/sessions?${sessionQuery.toString()}`,
    vms: `/api/admin/vms?${vmQuery.toString()}`,
  };
}

function normalizedOption(value: unknown, expired = false): WorkflowTargetOption | null {
  const normalizedValue = typeof value === "string" ? value.trim() : "";
  if (!normalizedValue) return null;
  return {
    value: normalizedValue,
    label: expired ? `${normalizedValue} (expired)` : normalizedValue,
  };
}

export function workflowTargetOptions(
  inventory: WorkflowTargetInventory,
  targetKey: WorkflowTargetKey,
): WorkflowTargetOption[] {
  const sessionOptions = inventory.sessions
    .map((session) => normalizedOption(session.session_id, session.is_expired === true))
    .filter((option): option is WorkflowTargetOption => option !== null);
  const vmOptions = inventory.vms
    .map((vm) => normalizedOption(vm.vm_name))
    .filter((option): option is WorkflowTargetOption => option !== null);
  const candidates = targetKey === "session_id"
    ? sessionOptions
    : targetKey === "vm_name"
      ? vmOptions
      : [...sessionOptions, ...vmOptions];
  const deduplicated = new Map<string, WorkflowTargetOption>();
  for (const option of candidates) {
    if (!deduplicated.has(option.value)) deduplicated.set(option.value, option);
  }
  return Array.from(deduplicated.values()).sort((left, right) =>
    left.value.localeCompare(right.value),
  );
}

export function workflowTargetInventoryFromSettledResults(
  sessionsResult: PromiseSettledResult<WorkflowTargetSessionListResponse>,
  vmsResult: PromiseSettledResult<WorkflowTargetVmListResponse>,
): WorkflowTargetInventory {
  return {
    sessions: sessionsResult.status === "fulfilled" && Array.isArray(sessionsResult.value.items)
      ? sessionsResult.value.items
      : [],
    vms: vmsResult.status === "fulfilled" && Array.isArray(vmsResult.value.items)
      ? vmsResult.value.items
      : [],
  };
}
