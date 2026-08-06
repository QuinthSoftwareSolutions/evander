import type { WorkflowNodeConfig } from "./types";

export function workflowTriggerEnabled(node: WorkflowNodeConfig | undefined): boolean {
  if (!node || !node.type.startsWith("trigger:")) return true;
  const value = node.enabled;
  if (typeof value === "boolean") return value;
  const nestedConfig = node.config;
  if (isRecord(nestedConfig) && typeof nestedConfig.enabled === "boolean") {
    return nestedConfig.enabled;
  }
  return true;
}

export function workflowTriggerEnabledValue(enabled: boolean): boolean | undefined {
  return enabled ? undefined : false;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
