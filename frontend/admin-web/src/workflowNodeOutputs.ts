import type { WorkflowConfig, WorkflowNodeConfig, WorkflowOutputSchemas } from "./types";

export type WorkflowBindingOption = {
  value: string;
  label: string;
};

export type WorkflowUpstreamIndex = {
  ancestorNodeIdsByNodeId: Map<string, string[]>;
  guaranteedUpstreamNodeIdsByNodeId: Map<string, string[]>;
};

export type WorkflowNodeOutputContractState = "declared" | "derived" | "empty" | "unknown";

export type WorkflowNodeOutputContractRow = {
  path: string;
  bindingPath: string;
  bindingExpression: string;
  schema: unknown;
  typeLabel: string;
  state: WorkflowNodeOutputContractState;
  note: string | null;
  details: string[];
};

export type WorkflowNodeOutputContract = {
  nodeId: string;
  nodeType: string | null;
  sourceNodeIds: string[];
  state: WorkflowNodeOutputContractState;
  note: string;
  rows: WorkflowNodeOutputContractRow[];
};

export function isPassThroughGateType(type: string | null | undefined): boolean {
  return type === "gate:filter" || type === "gate:dedupe" || type === "gate:debounce" || type === "gate:throttle" || type === "gate:switch" || type === "gate:classifier:switch";
}

function isCounterGateType(type: string | null | undefined): boolean {
  return type === "gate:counter";
}

const SYSTEM_BINDING_OPTIONS: ReadonlyArray<WorkflowBindingOption> = [
  { value: "${{system.now.utc_timestamp}}", label: "[System] now.utc_timestamp" },
  { value: "${{system.now.utc_timestamp_compact}}", label: "[System] now.utc_timestamp_compact" },
  { value: "${{system.now.unix_seconds}}", label: "[System] now.unix_seconds" },
  { value: "${{system.execution.trace_id}}", label: "[System] execution.trace_id" },
  { value: "${{system.execution.event_id}}", label: "[System] execution.event_id" },
  { value: "${{system.execution.node_id}}", label: "[System] execution.node_id" },
  { value: "${{system.execution.started_at}}", label: "[System] execution.started_at" },
  { value: "${{system.execution.started_at_compact}}", label: "[System] execution.started_at_compact" },
  { value: "${{system.unique.compact}}", label: "[System] unique.compact" },
];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function nodeCollections(config: WorkflowConfig) {
  return {
    triggers: config.triggers ?? [],
    gates: config.gates ?? [],
    effects: config.effects ?? [],
    queries: config.queries ?? [],
  };
}

function hasDeclaredOutputs(node: WorkflowNodeConfig | undefined): node is WorkflowNodeConfig & { outputs: Record<string, unknown> } {
  return Boolean(node && isRecord(node.outputs) && Object.keys(node.outputs).length > 0);
}

function hasOutputSchemaEntries(schema: Record<string, unknown> | undefined): boolean {
  return Boolean(schema && Object.keys(schema).length > 0);
}

function hasDynamicDeserializeContent(node: WorkflowNodeConfig): boolean {
  const content = node.config.content;
  return typeof content !== "string" || content.trim() === "" || content.includes("${{");
}

function deserializeOutputSchema(
  node: WorkflowNodeConfig,
  backendSchema: Record<string, unknown> | undefined,
  nodeOutputs: Record<string, unknown> | undefined,
): Record<string, unknown> | undefined {
  const backendHasEntries = hasOutputSchemaEntries(backendSchema);
  const dynamicContent = hasDynamicDeserializeContent(node);
  const nodeHasDeclaredOutputs = hasDeclaredOutputs(node);
  if (!dynamicContent && backendHasEntries) {
    return backendSchema;
  }
  if ((dynamicContent || !backendHasEntries) && nodeHasDeclaredOutputs) {
    return nodeOutputs;
  }
  return backendSchema ?? nodeOutputs;
}

export function workflowNodes(config: WorkflowConfig): WorkflowNodeConfig[] {
  const collections = nodeCollections(config);
  return [
    ...collections.triggers,
    ...collections.gates,
    ...collections.effects,
    ...collections.queries,
  ];
}

export function nodeById(config: WorkflowConfig, nodeId: string): WorkflowNodeConfig | undefined {
  return workflowNodes(config).find((node) => node.id === nodeId);
}

export function directUpstreamNodeIds(config: WorkflowConfig, targetId: string): string[] {
  return config.edges.filter((edge) => edge.target_id === targetId).map((edge) => edge.source_id);
}

export function deriveWorkflowOutputSchemas(
  config: WorkflowConfig,
  baseOutputSchemas: WorkflowOutputSchemas = {},
): WorkflowOutputSchemas {
  const nodesById = new Map(workflowNodes(config).map((node) => [node.id, node]));
  const cache = new Map<string, Record<string, unknown>>();
  const visiting = new Set<string>();

  function resolve(nodeId: string): Record<string, unknown> {
    const cached = cache.get(nodeId);
    if (cached) return cached;
    const node = nodesById.get(nodeId);
    if (!node) return {};
    if (visiting.has(nodeId)) {
      const fallback = isRecord(baseOutputSchemas[nodeId]) ? baseOutputSchemas[nodeId] : undefined;
      return copyOutputSchema(fallback);
    }

    visiting.add(nodeId);
    let schema: Record<string, unknown>;
    if (isPassThroughGateType(node.type)) {
      schema = copyOutputSchema(resolve(directUpstreamNodeIds(config, node.id)[0] ?? ""));
    } else if (isCounterGateType(node.type)) {
      schema = {
        ...copyOutputSchema(resolve(directUpstreamNodeIds(config, node.id)[0] ?? "")),
        count: { type: "integer" },
      };
    } else if (node.type === "gate:event:validate") {
      schema = {
        ...copyOutputSchema(resolve(directUpstreamNodeIds(config, node.id)[0] ?? "")),
        valid: { type: "boolean" },
        event_type: { type: "string" },
        schema_revision: { type: "string" },
        schema_source: { type: ["string", "null"] },
        failures: {
          type: "array",
          items: {
            type: "object",
            properties: { code: { type: "string" }, path: { type: "string" }, message: { type: "string" } },
            required: ["code", "path", "message"],
          },
        },
      };
    } else if (node.type === "gate:merge") {
      schema = mergeDeclaredOutputIntersection(
        directUpstreamNodeIds(config, node.id).map((upstreamNodeId) => resolve(upstreamNodeId)),
      );
    } else if (node.type === "gate:map" && isRecord(node.config.template)) {
      schema = mapTemplateOutputSchema(node.config.template);
    } else if (node.type === "gate:aggregate" && isRecord(node.config.metrics)) {
      schema = aggregateMetricOutputSchema(node.config.metrics);
    } else if (node.type === "gate:buffer") {
      schema = bufferOutputSchema();
    } else {
      const backendSchema = isRecord(baseOutputSchemas[node.id]) ? baseOutputSchemas[node.id] : undefined;
      const nodeOutputs = isRecord(node.outputs) ? node.outputs : undefined;
      schema = copyOutputSchema(
        node.type === "gate:deserialize"
          ? deserializeOutputSchema(node, backendSchema, nodeOutputs)
          : node.type === "query:classification:evaluate"
            ? {...backendSchema, ...nodeOutputs, ...(isRecord(node.config.questions) ? {answers: classificationAnswerSchema(node.config.questions)} : {})}
            : backendSchema ?? nodeOutputs,
      );
    }
    visiting.delete(nodeId);
    cache.set(nodeId, schema);
    return schema;
  }

  const outputSchemas: WorkflowOutputSchemas = {};
  for (const node of workflowNodes(config)) {
    outputSchemas[node.id] = copyOutputSchema(resolve(node.id));
  }
  return outputSchemas;
}

export function declaredOutputsForNode(
  node: WorkflowNodeConfig | undefined,
  outputSchemas: WorkflowOutputSchemas = {},
): Record<string, unknown> {
  if (!node) return {};
  const schema = isRecord(outputSchemas[node.id]) ? outputSchemas[node.id] : undefined;
  if (schema) return copyOutputSchema(schema);
  return copyOutputSchema(isRecord(node.outputs) ? node.outputs : undefined);
}

export function flattenOutputPaths(prefix: string, schema: unknown): string[] {
  return flattenOutputPathEntries(prefix, schema).map((entry) => entry.path);
}

export function upstreamBindingOptions(
  config: WorkflowConfig,
  nodeId: string,
  outputSchemas: WorkflowOutputSchemas = deriveWorkflowOutputSchemas(config),
): WorkflowBindingOption[] {
  const options: WorkflowBindingOption[] = [...SYSTEM_BINDING_OPTIONS];
  const seen = new Set<string>();
  for (const option of SYSTEM_BINDING_OPTIONS) {
    seen.add(option.label.replace(/^\[System\] /, ""));
    seen.add(option.value);
  }
  const upstreamIndex = buildWorkflowUpstreamIndex(config);
  for (const upstreamNodeId of upstreamIndex.guaranteedUpstreamNodeIdsByNodeId.get(nodeId) ?? []) {
    const outputs = declaredOutputsForNode(nodeById(config, upstreamNodeId), outputSchemas);
    for (const outputName of Object.keys(outputs)) {
      for (const outputPath of flattenOutputPaths(outputName, outputs[outputName])) {
        const ref = `${upstreamNodeId}.${outputPath}`;
        if (seen.has(`\${{${ref}}}`)) continue;
        seen.add(`\${{${ref}}}`);
        options.push({ value: `\${{${ref}}}`, label: ref });
      }
    }
  }
  return options;
}

export function selectedNodeOutputContract(
  config: WorkflowConfig,
  nodeId: string,
  outputSchemas: WorkflowOutputSchemas = {},
): WorkflowNodeOutputContract {
  const node = nodeById(config, nodeId);
  if (!node) {
    return {
      nodeId,
      nodeType: null,
      sourceNodeIds: [],
      state: "unknown",
      note: "Selected node is not known.",
      rows: [],
    };
  }

  const derivedOutputSchemas = deriveWorkflowOutputSchemas(config, outputSchemas);
  const nodeOutputs = declaredOutputsForNode(node, derivedOutputSchemas);

  if (isPassThroughGateType(node.type)) {
    const sourceNodeId = directUpstreamNodeIds(config, node.id)[0] ?? null;
    if (!sourceNodeId) {
      return {
        nodeId: node.id,
        nodeType: node.type,
        sourceNodeIds: [],
        state: "empty",
        note: "Pass-through gate: no upstream outputs are available to expose.",
        rows: [],
      };
    }

    const sourceNode = nodeById(config, sourceNodeId);
    const sourceOutputs = declaredOutputsForNode(sourceNode, derivedOutputSchemas);
    if (Object.keys(sourceOutputs).length === 0) {
      return {
        nodeId: node.id,
        nodeType: node.type,
        sourceNodeIds: [sourceNodeId],
        state: "empty",
        note: "Pass-through gate: derived source has no outputs.",
        rows: [],
      };
    }

    return buildOutputContract(node, sourceOutputs, {
      state: "derived",
      note: `Pass-through gate: outputs are derived from ${sourceNodeId}.`,
      sourceNodeIds: [sourceNodeId],
    });
  }

  if (node.type === "gate:merge") {
    const sourceNodeIds = directUpstreamNodeIds(config, node.id);
    if (sourceNodeIds.length === 0) {
      return {
        nodeId: node.id,
        nodeType: node.type,
        sourceNodeIds: [],
        state: "empty",
        note: "Merge gate: no upstream outputs are available to expose.",
        rows: [],
      };
    }

    const sourceOutputs = sourceNodeIds.map((sourceNodeId) => declaredOutputsForNode(nodeById(config, sourceNodeId), derivedOutputSchemas));
    const mergedOutputs = mergeDeclaredOutputIntersection(sourceOutputs);
    if (Object.keys(mergedOutputs).length === 0) {
      return {
        nodeId: node.id,
        nodeType: node.type,
        sourceNodeIds,
        state: "empty",
        note: "Merge gate: no common upstream outputs are known.",
        rows: [],
      };
    }

    return buildOutputContract(node, mergedOutputs, {
      state: "derived",
      note: "Merge gate: outputs are the common compatible upstream intersection.",
      sourceNodeIds,
    });
  }

  if (node.type === "gate:map" && isRecord(node.config.template)) {
    const templateOutputs = mapTemplateOutputSchema(node.config.template);
    if (Object.keys(templateOutputs).length === 0) {
      return {
        nodeId: node.id,
        nodeType: node.type,
        sourceNodeIds: [node.id],
        state: "empty",
        note: "Map gate: the template does not declare any outputs.",
        rows: [],
      };
    }

    return buildOutputContract(node, templateOutputs, {
      state: "derived",
      note: "Map gate: outputs are derived from the map template.",
      sourceNodeIds: [node.id],
    });
  }

  if (node.type === "gate:aggregate" && isRecord(node.config.metrics)) {
    const aggregateOutputs = aggregateMetricOutputSchema(node.config.metrics);
    return buildOutputContract(node, aggregateOutputs, {
      state: "derived",
      note: "Aggregate gate: outputs are count plus one output per configured metric.",
      sourceNodeIds: [node.id],
    });
  }

  if (node.type === "gate:buffer") {
    return buildOutputContract(node, bufferOutputSchema(), {
      state: "derived",
      note: "Buffer gate: outputs are the released batch metadata.",
      sourceNodeIds: [node.id],
    });
  }

  if (Object.keys(nodeOutputs).length === 0) {
    return {
      nodeId: node.id,
      nodeType: node.type,
      sourceNodeIds: [node.id],
      state: "empty",
      note: "No known outputs are declared for this node.",
      rows: [],
    };
  }

  return buildOutputContract(node, nodeOutputs, {
    state: "declared",
    note: "Outputs are declared by backend schema or node.outputs.",
    sourceNodeIds: [node.id],
  });
}

function buildOutputContract(
  node: WorkflowNodeConfig,
  outputs: Record<string, unknown>,
  metadata: {
    state: WorkflowNodeOutputContractState;
    note: string;
    sourceNodeIds: string[];
  },
): WorkflowNodeOutputContract {
  return {
    nodeId: node.id,
    nodeType: node.type,
    sourceNodeIds: metadata.sourceNodeIds,
    state: metadata.state,
    note: metadata.note,
    rows: flattenOutputPathEntriesFromOutputs(node.id, outputs, metadata.state, metadata.note),
  };
}

function flattenOutputPathEntriesFromOutputs(
  nodeId: string,
  outputs: Record<string, unknown>,
  state: WorkflowNodeOutputContractState,
  note: string,
): WorkflowNodeOutputContractRow[] {
  return Object.entries(outputs).flatMap(([outputName, schema]) =>
    flattenOutputPathEntries(outputName, schema).map((entry) => ({
      path: entry.path,
      bindingPath: `${nodeId}.${entry.path}`,
      bindingExpression: `\${{${nodeId}.${entry.path}}}`,
      schema: entry.schema,
      typeLabel: outputSchemaTypeLabel(entry.schema),
      state,
      note,
      details: outputSchemaDetails(entry.schema),
    })),
  );
}

function flattenOutputPathEntries(
  prefix: string,
  schema: unknown,
): Array<{ path: string; schema: unknown }> {
  const entries = [{ path: prefix, schema }];
  if (!isRecord(schema)) return entries;
  const schemaType = typeof schema.type === "string" ? schema.type : "";
  if (schemaType === "object" && isRecord(schema.properties)) {
    for (const [key, childSchema] of Object.entries(schema.properties)) {
      const childKey = key.trim();
      if (!childKey) continue;
      for (const childEntry of flattenOutputPathEntries(`${prefix}.${childKey}`, childSchema)) {
        entries.push(childEntry);
      }
    }
  }
  if (schemaType === "array" && schema.items !== undefined) {
    for (const childEntry of flattenOutputPathEntries(`${prefix}.0`, schema.items)) {
      entries.push(childEntry);
    }
  }
  return entries;
}

function collectAncestorNodeIdsFromInbound(
  inboundByTarget: Map<string, string[]>,
  nodeId: string,
): string[] {
  const ordered: string[] = [];
  const queue = [...(inboundByTarget.get(nodeId) ?? [])];
  const seen = new Set<string>();
  for (let index = 0; index < queue.length; index += 1) {
    const current = queue[index]!;
    if (seen.has(current)) continue;
    seen.add(current);
    ordered.push(current);
    queue.push(...(inboundByTarget.get(current) ?? []));
  }
  return ordered;
}

export function guaranteedUpstreamNodeIds(config: WorkflowConfig, nodeId: string): string[] {
  return buildWorkflowUpstreamIndex(config).guaranteedUpstreamNodeIdsByNodeId.get(nodeId) ?? [];
}

export function buildWorkflowUpstreamIndex(config: WorkflowConfig): WorkflowUpstreamIndex {
  const nodeIds = workflowNodes(config).map((node) => node.id);
  const nodeIdSet = new Set(nodeIds);
  const inboundByTarget = new Map<string, string[]>();
  const predecessors = new Map<string, Set<string>>();
  for (const currentNodeId of nodeIds) {
    inboundByTarget.set(currentNodeId, []);
    predecessors.set(currentNodeId, new Set<string>());
  }
  for (const edge of config.edges) {
    const inbound = inboundByTarget.get(edge.target_id) ?? [];
    inbound.push(edge.source_id);
    inboundByTarget.set(edge.target_id, inbound);
    if (edge.allow_cycle) continue;
    if (!nodeIdSet.has(edge.source_id) || !nodeIdSet.has(edge.target_id)) continue;
    predecessors.get(edge.target_id)?.add(edge.source_id);
  }

  const ancestorNodeIdsByNodeId = new Map<string, string[]>();
  for (const currentNodeId of nodeIds) {
    ancestorNodeIdsByNodeId.set(
      currentNodeId,
      collectAncestorNodeIdsFromInbound(inboundByTarget, currentNodeId),
    );
  }

  const dominators = new Map<string, Set<string>>();
  for (const currentNodeId of nodeIds) {
    const preds = predecessors.get(currentNodeId) ?? new Set<string>();
    dominators.set(currentNodeId, preds.size === 0 ? new Set([currentNodeId]) : new Set(nodeIds));
  }

  const sortedNodeIds = [...nodeIds].sort();
  let changed = true;
  while (changed) {
    changed = false;
    for (const currentNodeId of sortedNodeIds) {
      const preds = predecessors.get(currentNodeId) ?? new Set<string>();
      let nextDominators: Set<string>;
      if (preds.size === 0) {
        nextDominators = new Set([currentNodeId]);
      } else {
        const shared = new Set(nodeIds);
        for (const predecessorId of preds) {
          for (const candidate of [...shared]) {
            if (!(dominators.get(predecessorId) ?? new Set([predecessorId])).has(candidate)) {
              shared.delete(candidate);
            }
          }
        }
        nextDominators = new Set([...shared, currentNodeId]);
      }
      const currentDominators = dominators.get(currentNodeId) ?? new Set<string>();
      if (!setsEqual(currentDominators, nextDominators)) {
        dominators.set(currentNodeId, nextDominators);
        changed = true;
      }
    }
  }

  const guaranteedUpstreamNodeIdsByNodeId = new Map<string, string[]>();
  for (const currentNodeId of nodeIds) {
    const guaranteed = dominators.get(currentNodeId) ?? new Set<string>();
    guaranteedUpstreamNodeIdsByNodeId.set(
      currentNodeId,
      (ancestorNodeIdsByNodeId.get(currentNodeId) ?? []).filter((upstreamNodeId) =>
        upstreamNodeId !== currentNodeId && guaranteed.has(upstreamNodeId),
      ),
    );
  }

  return {
    ancestorNodeIdsByNodeId,
    guaranteedUpstreamNodeIdsByNodeId,
  };
}

function copyOutputSchema(schema: Record<string, unknown> | undefined): Record<string, unknown> {
  return { ...(schema ?? {}) };
}

function setsEqual(left: Set<string>, right: Set<string>): boolean {
  if (left.size !== right.size) return false;
  for (const item of left) {
    if (!right.has(item)) return false;
  }
  return true;
}

function mapTemplateOutputSchema(template: Record<string, unknown>): Record<string, unknown> {
  const schema: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(template)) {
    const outputName = key.trim();
    if (!outputName) continue;
    schema[outputName] = inferSchemaFromTemplateValue(value);
  }
  return schema;
}

function aggregateMetricOutputSchema(metrics: Record<string, unknown>): Record<string, unknown> {
  const schema: Record<string, unknown> = { count: { type: "integer" } };
  for (const [key, rawMetric] of Object.entries(metrics)) {
    const outputName = key.trim();
    if (!outputName || outputName === "count" || !isRecord(rawMetric)) continue;
    const operation = typeof rawMetric.operation === "string" ? rawMetric.operation.trim().toLowerCase() : "";
    if (operation === "sum") {
      schema[outputName] = { type: "number" };
    } else if (operation === "min" || operation === "max" || operation === "avg") {
      schema[outputName] = { type: ["number", "null"] };
    }
  }
  return schema;
}

function bufferOutputSchema(): Record<string, unknown> {
  return {
    items: { type: "array" },
    count: { type: "integer" },
    release_reason: { type: "string" },
  };
}

function inferSchemaFromTemplateValue(value: unknown): Record<string, unknown> {
  if (typeof value === "boolean") return { type: "boolean" };
  if (typeof value === "number") return { type: Number.isInteger(value) ? "integer" : "number" };
  if (value === null) return { type: "null" };
  if (typeof value === "string") return { type: "string" };
  if (Array.isArray(value)) return { type: "array" };
  if (isRecord(value)) return { type: "object" };
  return { type: "object" };
}

function mergeDeclaredOutputIntersection(outputs: Record<string, unknown>[]): Record<string, unknown> {
  if (outputs.length === 0) return {};
  const sharedKeys = new Set(Object.keys(outputs[0]));
  for (const current of outputs.slice(1)) {
    for (const key of [...sharedKeys]) {
      if (!(key in current)) {
        sharedKeys.delete(key);
        continue;
      }
      if (JSON.stringify(outputs[0][key]) !== JSON.stringify(current[key])) {
        sharedKeys.delete(key);
      }
    }
  }
  const merged: Record<string, unknown> = {};
  for (const key of Object.keys(outputs[0])) {
    if (sharedKeys.has(key)) merged[key] = outputs[0][key];
  }
  return merged;
}

function outputSchemaTypeLabel(schema: unknown): string {
  if (!isRecord(schema)) return "unknown";
  const schemaType = typeof schema.type === "string" ? schema.type : "";
  if (schemaType === "array") {
    if (schema.items === undefined) return "array";
    return `array<${outputSchemaTypeLabel(schema.items)}>`;
  }
  if (
    schemaType === "string" ||
    schemaType === "integer" ||
    schemaType === "number" ||
    schemaType === "boolean" ||
    schemaType === "null" ||
    schemaType === "object"
  ) {
    return schemaType;
  }
  if (Array.isArray(schema.enum) && schema.enum.length > 0) {
    return "enum";
  }
  return "unknown";
}

function outputSchemaDetails(schema: unknown): string[] {
  if (!isRecord(schema)) return [];
  const details: string[] = [];
  if (typeof schema.format === "string" && schema.format.trim()) {
    details.push(`format: ${schema.format.trim()}`);
  }
  if (Array.isArray(schema.enum) && schema.enum.length > 0) {
    const preview = schema.enum
      .slice(0, 4)
      .map((value) => (typeof value === "string" ? value : JSON.stringify(value)))
      .join(", ");
    details.push(`enum: ${preview}${schema.enum.length > 4 ? ", ..." : ""}`);
  }
  if (typeof schema.type === "string" && schema.type === "object" && isRecord(schema.properties)) {
    const propertyNames = Object.keys(schema.properties)
      .map((key) => key.trim())
      .filter(Boolean);
    if (propertyNames.length > 0) {
      details.push(`properties: ${propertyNames.join(", ")}`);
    }
  }
  if (typeof schema.type === "string" && schema.type === "array" && schema.items !== undefined) {
    details.push(`items: ${outputSchemaTypeLabel(schema.items)}`);
  }
  return details;
}

export function classificationAnswerSchema(questions: unknown): Record<string, unknown> {
  const properties: Record<string, unknown> = {};
  if (isRecord(questions)) for (const [id, question] of Object.entries(questions)) {
    if (!isRecord(question)) continue;
    if (!["choice", "score", "boolean"].includes(String(question.type))) continue;
    const fields: Record<string, unknown> = {type: {type: "string"}};
    if (question.type === "boolean") {
      fields.value = {type: "boolean"};
      fields.probability_true = {type: "number"};
    }
    else {
      fields[question.type === "choice" ? "choice" : "score"] = {type: question.type === "choice" ? "string" : "number"};
      fields.confidence = {type: "number"};
      fields.probabilities = {type: "object"};
      if (question.type === "score") fields.legend = {type: "object"};
    }
    properties[id] = {type: "object", properties: fields};
  }
  return {type: "object", properties, additionalProperties: {type: "object"}};
}
