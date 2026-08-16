import type { WorkflowNodeConfig } from "./types";

export function workflowTriggerEnabled(node: WorkflowNodeConfig | undefined): boolean {
  if (!node || !node.type.startsWith("trigger:")) return true;
  const value = node.config.enabled;
  if (typeof value === "boolean") {
    return value;
  }
  return true;
}

export function workflowTriggerEnabledValue(enabled: boolean): boolean | undefined {
  return enabled ? undefined : false;
}
