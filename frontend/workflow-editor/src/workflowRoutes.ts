import type { WorkflowEditorNodePortDefinition } from "./types";

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

export function workflowEdgeRouteLabel(
  sourcePort: string | null | undefined,
  outputPorts: ReadonlyArray<WorkflowEditorNodePortDefinition>,
): string | undefined {
  const normalized = normalizeWorkflowSourcePort(sourcePort);
  if (!normalized) return undefined;

  const port = outputPorts.find((candidate) => candidate.key === normalized);
  const fallbackLabel = port?.label || humanizeRouteKey(normalized);
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

function isErrorRoute(sourcePort: string): boolean {
  return sourcePort.toLowerCase() === "error";
}

function humanizeRouteKey(sourcePort: string): string {
  return sourcePort
    .split(/[-_:]+/g)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ") || sourcePort;
}
