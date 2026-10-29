export type WorkflowTargetKey = "target" | "session_id" | "vm_name";

export type WorkflowTargetSession = {
  session_id: string;
  vm_id?: string | null;
  is_expired?: boolean;
};

export type WorkflowTargetVm = {
  vm_name: string;
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

export function workflowTargetInventoryPath(directoryPath: string): string {
  const normalizedDirectoryPath = directoryPath.trim() || "root";
  const query = new URLSearchParams({
    directory_path: normalizedDirectoryPath,
  });
  return `/api/workflow-editor/target-catalog?${query.toString()}`;
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
