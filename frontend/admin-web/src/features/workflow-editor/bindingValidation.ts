import {
  type WorkflowConfig,
  type WorkflowGraphMetadata,
  type WorkflowNodeConfig,
  type WorkflowOutputSchemas,
  type WorkflowValidationDiagnostic,
} from "../../types";
import {
  BindingParseError,
  isParsedBindingExpression,
  isParsedBindingReference,
  parseBindingExpression,
  type ParsedBindingExpression,
  type ParsedBindingReference,
} from "../../workflowBindings";
import {
  buildWorkflowUpstreamIndex,
  declaredOutputsForNode,
  deriveWorkflowOutputSchemas,
} from "../../workflowNodeOutputs";
import { AGENT_CREATE_NODE_TYPE, CLIENT_RECOMPUTED_DIAGNOSTIC_CODES } from "./editorConstants";
import { type WorkflowValidationCacheEntry } from "./editorTypes";
import { isRecord } from "./editorValues";
import { workflowNodes } from "./graphModel";

export function validateWorkflowBindingDiagnostics(
  config: WorkflowConfig,
  outputSchemas: WorkflowOutputSchemas = deriveWorkflowOutputSchemas(config),
): WorkflowValidationDiagnostic[] {
  const diagnostics: WorkflowValidationDiagnostic[] = [];
  const nodes = workflowNodes(config);
  const nodesById = new Map(nodes.map((node) => [node.id, node]));
  const allNodeIds = new Set(nodes.map((node) => node.id));
  const upstreamIndex = buildWorkflowUpstreamIndex(config);

  for (const node of nodes) {
    const ancestorNodeIds = upstreamIndex.ancestorNodeIdsByNodeId.get(node.id) ?? [];
    const guaranteedNodeIds = new Set(
      upstreamIndex.guaranteedUpstreamNodeIdsByNodeId.get(node.id) ?? [],
    );
    const declaredOutputs = new Map(
      ancestorNodeIds
        .map((upstreamNodeId) => [upstreamNodeId, declaredOutputsForNode(nodesById.get(upstreamNodeId), outputSchemas)] as const),
    );
    for (const [fieldPath, value] of walkEditableNodeConfigValues(node)) {
      diagnostics.push(
        ...validateBindingValue({
          value,
          fieldPath,
          node,
          declaredOutputs,
          guaranteedNodeIds,
          allNodeIds,
        }),
      );
    }
  }
  return diagnostics;
}

export function validateWorkflowAgentCreateDiagnostics(
  config: WorkflowConfig,
): WorkflowValidationDiagnostic[] {
  const diagnostics: WorkflowValidationDiagnostic[] = [];
  for (const effect of config.effects ?? []) {
    if (effect.type !== AGENT_CREATE_NODE_TYPE) continue;
    const executionBackend = typeof effect.config.execution_backend === "string"
      ? effect.config.execution_backend.trim()
      : "";
    if (executionBackend) continue;
    diagnostics.push({
      code: "agent_create_missing_execution_backend",
      severity: "error",
      node_id: effect.id,
      node_type: AGENT_CREATE_NODE_TYPE,
      field_path: "execution_backend",
      message: "effect:agent:create requires an execution backend.",
    });
  }
  return diagnostics;
}

export function validateWorkflowClientDiagnostics(
  config: WorkflowConfig,
  outputSchemas: WorkflowOutputSchemas = deriveWorkflowOutputSchemas(config),
): WorkflowValidationDiagnostic[] {
  return mergeWorkflowDiagnostics(
    validateWorkflowAgentCreateDiagnostics(config),
    validateWorkflowBindingDiagnostics(config, outputSchemas),
  );
}

export function validateWorkflowGraphBindingDiagnostics(
  config: WorkflowConfig,
  graph: WorkflowGraphMetadata,
  outputSchemas: WorkflowOutputSchemas = deriveWorkflowOutputSchemas(config),
): WorkflowValidationDiagnostic[] {
  const graphConfig = workflowGraphScopedConfig(config, graph);
  return validateWorkflowClientDiagnostics(
    graphConfig,
    deriveWorkflowOutputSchemas(graphConfig, outputSchemas),
  );
}

export function workflowGraphScopedConfig(
  config: WorkflowConfig,
  graph: WorkflowGraphMetadata,
): WorkflowConfig {
  const graphNodeIds = new Set(graph.node_ids);
  const includesGraphNode = (node: WorkflowNodeConfig) => graphNodeIds.has(node.id);
  return {
    ...config,
    triggers: (config.triggers ?? []).filter(includesGraphNode),
    gates: (config.gates ?? []).filter(includesGraphNode),
    effects: (config.effects ?? []).filter(includesGraphNode),
    queries: (config.queries ?? []).filter(includesGraphNode),
    edges: (config.edges ?? []).filter((edge) =>
      graphNodeIds.has(edge.source_id) && graphNodeIds.has(edge.target_id),
    ),
  };
}

export function workflowGraphValidationSignature(
  config: WorkflowConfig,
  graph: WorkflowGraphMetadata,
  outputSchemas: WorkflowOutputSchemas,
): string {
  const graphNodeIds = new Set(graph.node_ids);
  const nodes = workflowNodes(config)
    .filter((node) => graphNodeIds.has(node.id))
    .map((node) => [node.id, node] as const)
    .sort(([left], [right]) => left.localeCompare(right));
  const edges = (config.edges ?? [])
    .filter((edge) => graphNodeIds.has(edge.source_id) && graphNodeIds.has(edge.target_id))
    .map((edge) => ({
      source_id: edge.source_id,
      source_port: edge.source_port ?? null,
      target_id: edge.target_id,
      allow_cycle: Boolean(edge.allow_cycle),
    }))
    .sort((left, right) =>
      `${left.source_id}\u0000${left.source_port ?? ""}\u0000${left.target_id}`.localeCompare(
        `${right.source_id}\u0000${right.source_port ?? ""}\u0000${right.target_id}`,
      ),
    );
  const schemas = [...graphNodeIds]
    .sort()
    .map((nodeId) => [nodeId, outputSchemas[nodeId] ?? null] as const);
  return JSON.stringify({
    node_ids: [...graphNodeIds].sort(),
    nodes,
    edges,
    schemas,
  });
}

export function cachedVisibleValidationDiagnostics(
  cache: Map<string, WorkflowValidationCacheEntry>,
  visibleGraphs: ReadonlyArray<WorkflowGraphMetadata>,
): WorkflowValidationDiagnostic[] {
  const diagnostics: WorkflowValidationDiagnostic[] = [];
  for (const graph of visibleGraphs) {
    const entry = cache.get(graph.id);
    if (!entry) continue;
    for (const diagnostic of entry.diagnostics) {
      diagnostics.push(diagnostic);
    }
  }
  return dedupeWorkflowDiagnostics(diagnostics);
}

export function workflowDiagnosticsForGraphs(
  diagnostics: ReadonlyArray<WorkflowValidationDiagnostic>,
  graphs: ReadonlyArray<WorkflowGraphMetadata>,
): WorkflowValidationDiagnostic[] {
  const graphNodeIds = new Set(graphs.flatMap((graph) => graph.node_ids));
  return diagnostics.filter((diagnostic) =>
    diagnostic.node_id ? graphNodeIds.has(diagnostic.node_id) : true,
  );
}

export function remoteOnlyWorkflowDiagnostics(
  diagnostics: ReadonlyArray<WorkflowValidationDiagnostic>,
): WorkflowValidationDiagnostic[] {
  return diagnostics.filter((diagnostic) => !CLIENT_RECOMPUTED_DIAGNOSTIC_CODES.has(diagnostic.code));
}

export function mergeWorkflowDiagnostics(
  first: ReadonlyArray<WorkflowValidationDiagnostic>,
  second: ReadonlyArray<WorkflowValidationDiagnostic>,
): WorkflowValidationDiagnostic[] {
  return dedupeWorkflowDiagnostics([...first, ...second]);
}

export function dedupeWorkflowDiagnostics(
  diagnostics: ReadonlyArray<WorkflowValidationDiagnostic>,
): WorkflowValidationDiagnostic[] {
  const nextDiagnostics: WorkflowValidationDiagnostic[] = [];
  const seen = new Set<string>();
  for (const diagnostic of diagnostics) {
    const key = [
      diagnostic.code,
      diagnostic.severity ?? "error",
      diagnostic.node_id ?? "",
      diagnostic.node_type ?? "",
      diagnostic.field_path ?? "",
      diagnostic.reference ?? "",
      diagnostic.message,
    ].join("\u0000");
    if (seen.has(key)) continue;
    seen.add(key);
    nextDiagnostics.push(diagnostic);
  }
  return nextDiagnostics;
}

export function scheduleWorkflowValidation(callback: () => void): () => void {
  const idleGlobal = globalThis as typeof globalThis & {
    requestIdleCallback?: (callback: () => void, options?: { timeout?: number }) => number;
    cancelIdleCallback?: (handle: number) => void;
  };
  if (
    typeof idleGlobal.requestIdleCallback === "function" &&
    typeof idleGlobal.cancelIdleCallback === "function"
  ) {
    const handle = idleGlobal.requestIdleCallback(callback, { timeout: 250 });
    return () => idleGlobal.cancelIdleCallback?.(handle);
  }
  const handle = globalThis.setTimeout(callback, 0);
  return () => globalThis.clearTimeout(handle);
}

export function walkEditableNodeConfigValues(node: WorkflowNodeConfig): Array<[string, unknown]> {
  const values: Array<[string, unknown]> = [];
  for (const [key, value] of Object.entries(node.config)) {
    walkConfigValue(value, key, values);
  }
  return values;
}

export function walkConfigValue(value: unknown, path: string, values: Array<[string, unknown]>) {
  if (isRecord(value)) {
    if (Object.keys(value).length === 0) {
      values.push([path, value]);
      return;
    }
    for (const [key, childValue] of Object.entries(value)) {
      walkConfigValue(childValue, `${path}.${key}`, values);
    }
    return;
  }
  if (Array.isArray(value)) {
    if (value.length === 0) {
      values.push([path, value]);
      return;
    }
    value.forEach((childValue, index) => {
      walkConfigValue(childValue, `${path}[${index}]`, values);
    });
    return;
  }
  values.push([path, value]);
}

export function validateBindingValue({
  value,
  fieldPath,
  node,
  declaredOutputs,
  guaranteedNodeIds,
  allNodeIds,
}: {
  value: unknown;
  fieldPath: string;
  node: WorkflowNodeConfig;
  declaredOutputs: Map<string, Record<string, unknown>>;
  guaranteedNodeIds: Set<string>;
  allNodeIds: Set<string>;
}): WorkflowValidationDiagnostic[] {
  if (typeof value !== "string" || !value.includes("${{")) return [];

  const diagnostics: WorkflowValidationDiagnostic[] = [];
  let index = 0;
  while (true) {
    const start = value.indexOf("${{", index);
    if (start === -1) return diagnostics;
    const end = value.indexOf("}}", start + 3);
    if (end === -1) {
      diagnostics.push({
        code: "malformed_binding",
        message: `${fieldPath} contains malformed output reference`,
        node_id: node.id,
        node_type: node.type,
        field_path: fieldPath,
        reference: value.slice(start),
      });
      return diagnostics;
    }

    const sourceText = value.slice(start, end + 2);
    const expressionText = value.slice(start + 3, end);
    try {
      const expression = parseBindingExpression(expressionText, sourceText);
      diagnostics.push(
        ...validateBindingExpression({
          expression,
          fieldPath,
          node,
          declaredOutputs,
          guaranteedNodeIds,
          allNodeIds,
        }),
      );
    } catch (error) {
      const parseError = error instanceof BindingParseError
        ? error
        : new BindingParseError("malformed_binding", "Malformed output reference");
      diagnostics.push({
        code: parseError.code,
        message: parseError.code === "malformed_binding_fallback"
          ? `${fieldPath} has malformed fallback syntax`
          : `${fieldPath} contains malformed output reference`,
        node_id: node.id,
        node_type: node.type,
        field_path: fieldPath,
        reference: sourceText,
      });
    }
    index = end + 2;
  }
}

export function validateBindingExpression({
  expression,
  fieldPath,
  node,
  declaredOutputs,
  guaranteedNodeIds,
  allNodeIds,
}: {
  expression: ParsedBindingExpression;
  fieldPath: string;
  node: WorkflowNodeConfig;
  declaredOutputs: Map<string, Record<string, unknown>>;
  guaranteedNodeIds: Set<string>;
  allNodeIds: Set<string>;
}): WorkflowValidationDiagnostic[] {
  const diagnostics = validateBindingReference({
    reference: expression.primary,
    fieldPath,
    node,
    declaredOutputs,
    guaranteedNodeIds,
    allNodeIds,
  });
  if (!expression.hasFallback) return diagnostics;
  const fallback = expression.fallback;
  if (isParsedBindingExpression(fallback)) {
    diagnostics.push(
      ...validateBindingExpression({
        expression: fallback,
        fieldPath,
        node,
        declaredOutputs,
        guaranteedNodeIds,
        allNodeIds,
      }),
    );
  } else if (isParsedBindingReference(fallback)) {
    diagnostics.push(
      ...validateBindingReference({
        reference: fallback,
        fieldPath,
        node,
        declaredOutputs,
        guaranteedNodeIds,
        allNodeIds,
      }),
    );
  }
  return diagnostics;
}

export function validateBindingReference({
  reference,
  fieldPath,
  node,
  declaredOutputs,
  guaranteedNodeIds,
  allNodeIds,
}: {
  reference: ParsedBindingReference;
  fieldPath: string;
  node: WorkflowNodeConfig;
  declaredOutputs: Map<string, Record<string, unknown>>;
  guaranteedNodeIds: Set<string>;
  allNodeIds: Set<string>;
}): WorkflowValidationDiagnostic[] {
  const resolved = resolveAvailableBindingReference(reference, declaredOutputs, allNodeIds);
  const outputRef = `${resolved.nodeId}.${resolved.outputPath}`;
  if (resolved.nodeId === "system") return [];
  if (!declaredOutputs.has(resolved.nodeId)) {
    const relation = allNodeIds.has(resolved.nodeId) ? "non-upstream" : "deleted";
    return [{
      code: "invalid_binding_node",
      message: `${fieldPath} references ${relation} node ${resolved.nodeId}`,
      node_id: node.id,
      node_type: node.type,
      field_path: fieldPath,
      reference: outputRef,
    }];
  }

  const outputName = resolved.outputPath.split(".", 1)[0] ?? "";
  if (!(outputName in (declaredOutputs.get(resolved.nodeId) ?? {}))) {
    return [{
      code: "missing_binding_output",
      message: `${fieldPath} references missing output ${outputRef}`,
      node_id: node.id,
      node_type: node.type,
      field_path: fieldPath,
      reference: outputRef,
    }];
  }
  if (!guaranteedNodeIds.has(resolved.nodeId)) {
    return [{
      code: "conditional_binding_node",
      message: `${fieldPath} references conditionally available upstream node ${resolved.nodeId}; bind through a merge-projected output when this value must be available after branching`,
      severity: "warning",
      node_id: node.id,
      node_type: node.type,
      field_path: fieldPath,
      reference: outputRef,
    }];
  }
  return [];
}

export function resolveAvailableBindingReference(
  reference: ParsedBindingReference,
  declaredOutputs: Map<string, Record<string, unknown>>,
  allNodeIds: Set<string>,
): ParsedBindingReference {
  const segments = `${reference.nodeId}.${reference.outputPath}`.split(".").filter(Boolean);
  for (let index = segments.length - 1; index > 0; index -= 1) {
    const candidateNodeId = segments.slice(0, index).join(".");
    const candidateOutputPath = segments.slice(index).join(".");
    if (candidateNodeId === "system" || declaredOutputs.has(candidateNodeId) || allNodeIds.has(candidateNodeId)) {
      return { nodeId: candidateNodeId, outputPath: candidateOutputPath };
    }
  }
  return reference;
}
