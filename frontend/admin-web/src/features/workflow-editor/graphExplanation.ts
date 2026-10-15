import type { ChatPrompt } from "../chat/api";
import type { WorkflowEditorCatalog, WorkflowEditorDocument, WorkflowGraphMetadata } from "../../types";
import { workflowGraphScopedConfig } from "./bindingValidation";

/** Describe the current editor graph without including other graphs or host configuration. */
export function workflowGraphExplanationRequest(
  document: WorkflowEditorDocument,
  graph: WorkflowGraphMetadata,
  catalog: WorkflowEditorCatalog,
  hasUnsavedChanges: boolean,
): ChatPrompt {
  const scoped = workflowGraphScopedConfig(document.config, graph);
  const { triggers, gates, effects, queries, edges } = scoped;
  const nodeIds = new Set(graph.node_ids);
  const nodeTypes = new Set([...triggers, ...gates, ...effects, ...queries].map((node) => node.type));
  const snapshot = {
    graph,
    context_source: "workflow_editor_draft",
    redaction_note: "Sensitive settings and URL query values are redacted; do not infer their values.",
    saved_revision: document.revision,
    has_unsaved_changes: hasUnsavedChanges,
    workflow_pack: document.selected_workflow_pack?.reference ?? null,
    config: { triggers, gates, effects, queries, edges },
    output_schemas: Object.fromEntries(
      Object.entries(document.output_schemas ?? {}).filter(([id]) => nodeIds.has(id)),
    ),
    node_catalog: catalog.nodes.filter((entry) => nodeTypes.has(entry.type)).map((entry) => ({
      kind: entry.kind,
      type: entry.type,
      label: entry.label,
      description: entry.description,
      fields: entry.fields.map(({ key, label, description, kind }) => ({ key, label, description, kind })),
      input_ports: entry.input_ports,
      output_ports: entry.output_ports,
    })),
  };
  return {
    content: `Explain what the workflow graph ${JSON.stringify(graph.name)} does in plain language, using its current editor draft. Cover its triggers, decisions, data flow, actions, and relevant disabled or failure paths. Flag uncertain behavior. Explain only; do not change or run anything.`,
    context: sanitizeGraphExplanationValue(snapshot) as Record<string, unknown>,
  };
}

// Follow the platform's payload redaction conventions before graph context leaves the editor.
export function sanitizeGraphExplanationValue(value: unknown, key = ""): unknown {
  const normalizedKey = key.replace(/([a-z0-9])([A-Z])/g, "$1_$2").replace(/-/g, "_").toLowerCase();
  const sensitiveMarkers = ["token", "secret", "password", "authorization", "cookie", "private_key", "ssh_key", "api_key", "apikey", "passwd", "credential"];
  if (sensitiveMarkers.some((marker) => normalizedKey.includes(marker))
    || normalizedKey.split("_").some((part) => part === "pat" || part === "pwd")
    || ["headers", "env", "environment"].includes(normalizedKey)) return "[redacted]";
  if (Array.isArray(value)) return value.map((entry) => sanitizeGraphExplanationValue(entry));
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([name, entry]) => [name, sanitizeGraphExplanationValue(entry, name)]));
  }
  if (typeof value === "string") {
    try {
      const url = new URL(value);
      if (url.protocol === "https:" || url.protocol === "http:") {
        if (!url.username && !url.password && !url.search && !url.hash) return value;
        if (url.username) url.username = "[redacted]";
        if (url.password) url.password = "[redacted]";
        for (const name of [...url.searchParams.keys()]) url.searchParams.set(name, "[redacted]");
        if (url.hash) url.hash = "[redacted]";
        return url.toString();
      }
    } catch { /* Non-URL strings remain workflow data. */ }
    return value.replace(/\b(Bearer|Basic)\s+[^\s]+/gi, "$1 [redacted]");
  }
  return value;
}
