import type { WorkflowEditorNodePortDefinition, WorkflowNodeConfig } from "./types";

export type WorkflowConnectionRouteOption = {
  sourcePort: string | null;
  label: string;
  description?: string | null;
  routeClass: string;
};

export function normalizeWorkflowSourcePort(sourcePort: string | null | undefined): string | null {
  if (sourcePort === null || sourcePort === undefined) return null;
  const text = String(sourcePort).trim();
  return text || null;
}

export function normalizeWorkflowEdgeSourcePort(
  sourceNodeType: string | null | undefined,
  sourcePort: string | null | undefined,
): string | null {
  const normalized = normalizeWorkflowSourcePort(sourcePort);
  return sourceNodeType === "gate:throttle" && normalized === null ? "default" : normalized;
}

export function workflowConnectionRouteOptions(
  outputPorts: ReadonlyArray<WorkflowEditorNodePortDefinition>,
): WorkflowConnectionRouteOption[] {
  if (outputPorts.length === 0) {
    return [
      {
        sourcePort: null,
        label: "Default",
        description: "Standard downstream path.",
        routeClass: workflowRouteClass(null),
      },
    ];
  }
  return outputPorts.map((port) => ({
    sourcePort: port.key,
    label: port.label,
    description: port.description,
    routeClass: workflowRouteClass(port.key),
  }));
}

export function workflowOutputPortsForNode(
  node: WorkflowNodeConfig,
  catalogEntryOutputPorts: ReadonlyArray<WorkflowEditorNodePortDefinition>,
): WorkflowEditorNodePortDefinition[] {
  if (node.type !== "gate:switch") {
    return catalogEntryOutputPorts.map((port) => ({ ...port }));
  }

  const outputPorts: WorkflowEditorNodePortDefinition[] = [];
  const seenPorts = new Set<string>();

  for (const switchCase of Array.isArray(node.config.cases) ? node.config.cases : []) {
    if (!isRecord(switchCase)) continue;
    const portKey = readWorkflowPortKey(switchCase.port);
    if (!portKey || seenPorts.has(portKey)) continue;
    seenPorts.add(portKey);
    const label = typeof switchCase.label === "string" ? switchCase.label.trim() : "";
    outputPorts.push({
      key: portKey,
      label: label || humanizeWorkflowRouteKey(portKey),
    });
  }

  const defaultPort = readWorkflowPortKey(node.config.default_port);
  if (defaultPort && !seenPorts.has(defaultPort)) {
    seenPorts.add(defaultPort);
    outputPorts.push({
      key: defaultPort,
      label: "Default",
      description: "Standard downstream path.",
    });
  }

  return outputPorts;
}

/**
 * The workflow runtime can route any failed node through its standard `error`
 * port even when that port is not part of the node's configurable catalog ports.
 * Keep a physical React Flow handle available so persisted error edges render.
 */
export function workflowOutputHandleIds(
  outputPorts: ReadonlyArray<WorkflowEditorNodePortDefinition>,
): string[] {
  const outputPortIds = outputPorts.map((port) => port.key);
  return outputPortIds.includes("error") ? outputPortIds : [...outputPortIds, "error"];
}

export function workflowEdgeRouteLabel(
  sourcePort: string | null | undefined,
  outputPorts: ReadonlyArray<WorkflowEditorNodePortDefinition>,
): string | undefined {
  const normalized = normalizeWorkflowSourcePort(sourcePort);
  if (!normalized) return undefined;

  const port = outputPorts.find((candidate) => candidate.key === normalized);
  const fallbackLabel = port?.label || humanizeWorkflowRouteKey(normalized);
  if (isErrorRoute(normalized)) return fallbackLabel;
  if (!port) return fallbackLabel;

  const nonErrorPorts = outputPorts.filter((candidate) => !isErrorRoute(candidate.key));
  if (nonErrorPorts.length === 1 && nonErrorPorts[0]?.key === normalized) {
    return undefined;
  }
  return fallbackLabel;
}

export function workflowRouteClass(sourcePort: string | null | undefined): string {
  const normalized = normalizeWorkflowSourcePort(sourcePort)?.toLowerCase();
  if (!normalized) return "default";
  if (normalized === "error") return "error";
  if (normalized === "started") return "started";
  if (normalized === "completed") return "completed";
  if (normalized === "body" || normalized === "done") return "loop";
  return "named";
}

export function humanizeWorkflowRouteKey(sourcePort: string): string {
  return sourcePort
    .split(/[-_:]+/g)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ") || sourcePort;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function readWorkflowPortKey(value: unknown): string | null {
  return typeof value === "string" ? normalizeWorkflowSourcePort(value) : null;
}

function isErrorRoute(sourcePort: string): boolean {
  return sourcePort.toLowerCase() === "error";
}
