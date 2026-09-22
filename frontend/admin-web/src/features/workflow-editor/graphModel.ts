import {
  type WorkflowConfig,
  type WorkflowEdge,
  type WorkflowEditorCatalog,
  type WorkflowEditorDocument,
  type WorkflowGraphMetadata,
  type WorkflowLayout,
  type WorkflowNodeConfig,
  type WorkflowNodeKind,
  type WorkflowOutputSchemas,
} from "../../types";
import { rewriteWorkflowNodeConfigBindings } from "../../workflowBindings";
import {
  declaredOutputsForNode,
  deriveWorkflowOutputSchemas,
  isPassThroughGateType,
} from "../../workflowNodeOutputs";
import { isSwitchGateType, workflowOutputPortsForNode } from "../../workflowRoutes";
import {
  type CopiedWorkflowNode,
  type WorkflowDocumentLike,
  type WorkflowDocumentSurface,
} from "./editorTypes";
import { getCatalogEntry, isRecord } from "./editorValues";

export function emptyConfig(): WorkflowConfig {
  return {
    triggers: [],
    gates: [],
    effects: [],
    queries: [],
    edges: [],
  };
}

export function emptyCatalog(): WorkflowEditorCatalog {
  return { worker_id: "trigger-orchestrator", nodes: [], internal_events: [] };
}

export function nodeCollections(config: WorkflowConfig) {
  return {
    triggers: config.triggers ?? [],
    gates: config.gates ?? [],
    effects: config.effects ?? [],
    queries: config.queries ?? [],
  };
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

export function workflowNodeIds(config: WorkflowConfig): string[] {
  return workflowNodes(config).map((node) => node.id);
}

export function workflowGraphKey(nodeIds: string[]): string {
  return [...nodeIds].sort().join("\u0000");
}

export function connectedWorkflowGraphNodeIds(config: WorkflowConfig): string[][] {
  const orderedNodeIds = workflowNodeIds(config);
  const nodeIdSet = new Set(orderedNodeIds);
  const order = new Map(orderedNodeIds.map((nodeId, index) => [nodeId, index]));
  const neighbors = new Map<string, Set<string>>();
  orderedNodeIds.forEach((nodeId) => neighbors.set(nodeId, new Set()));
  for (const edge of config.edges ?? []) {
    if (!nodeIdSet.has(edge.source_id) || !nodeIdSet.has(edge.target_id)) continue;
    neighbors.get(edge.source_id)?.add(edge.target_id);
    neighbors.get(edge.target_id)?.add(edge.source_id);
  }
  const visited = new Set<string>();
  const components: string[][] = [];
  for (const nodeId of orderedNodeIds) {
    if (visited.has(nodeId)) continue;
    const stack = [nodeId];
    const component: string[] = [];
    while (stack.length > 0) {
      const current = stack.pop()!;
      if (visited.has(current)) continue;
      visited.add(current);
      component.push(current);
      const nextNodeIds = [...(neighbors.get(current) ?? [])]
        .filter((candidate) => !visited.has(candidate))
        .sort((left, right) => (order.get(right) ?? 0) - (order.get(left) ?? 0));
      stack.push(...nextNodeIds);
    }
    components.push(component.sort((left, right) => (order.get(left) ?? 0) - (order.get(right) ?? 0)));
  }
  return components;
}

export function slugifyGraphId(value: string): string {
  return value.replace(/[^a-zA-Z0-9_-]+/g, "-").replace(/^-+|-+$/g, "").toLowerCase() || "graph";
}

export function defaultWorkflowGraphName(config: WorkflowConfig, component: string[], index: number): string {
  const trigger = component
    .map((nodeId) => nodeById(config, nodeId))
    .find((node): node is WorkflowNodeConfig => Boolean(node && node.type.startsWith("trigger:")));
  const fallbackNode = nodeById(config, component[0] ?? "");
  return trigger?.display_name?.trim() || fallbackNode?.display_name?.trim() || `Graph ${index + 1}`;
}

export function defaultWorkflowGraphId(config: WorkflowConfig, component: string[], index: number): string {
  const trigger = component
    .map((nodeId) => nodeById(config, nodeId))
    .find((node): node is WorkflowNodeConfig => Boolean(node && node.type.startsWith("trigger:")));
  return `graph-${slugifyGraphId(trigger?.id ?? component[0] ?? String(index + 1))}`;
}

export function normalizeWorkflowGraphs(
  config: WorkflowConfig,
  graphs: ReadonlyArray<WorkflowGraphMetadata> = [],
  defaultDirectoryPath = "root",
): WorkflowGraphMetadata[] {
  const components = connectedWorkflowGraphNodeIds(config);
  const validNodeIds = new Set(workflowNodeIds(config));
  const sanitizedGraphs = graphs
    .map((graph) => ({
      id: graph.id.trim(),
      name: graph.name.trim(),
      description: graph.description?.trim() || null,
      directory_path:
        typeof graph.directory_path === "string" && graph.directory_path.trim()
          ? graph.directory_path.trim()
          : defaultDirectoryPath,
      user_ids: Array.from(
        new Set(
          (Array.isArray(graph.user_ids) ? graph.user_ids : [])
            .map((userId) => userId.trim())
            .filter(Boolean),
        ),
      ),
      node_ids: Array.from(new Set((graph.node_ids ?? []).map((nodeId) => nodeId.trim()).filter((nodeId) => validNodeIds.has(nodeId)))),
    }))
    .filter((graph) => graph.id && graph.name && graph.node_ids.length > 0);
  const exactGraphs = new Map(sanitizedGraphs.map((graph) => [workflowGraphKey(graph.node_ids), graph]));
  const usedGraphIds = new Set<string>();
  const usedResultIds = new Set<string>();
  return components.map((component, index) => {
    const componentKey = workflowGraphKey(component);
    let graph = exactGraphs.get(componentKey);
    if (!graph) {
      graph = sanitizedGraphs
        .filter((candidate) => !usedGraphIds.has(candidate.id))
        .map((candidate) => ({
          graph: candidate,
          overlap: candidate.node_ids.filter((nodeId) => component.includes(nodeId)).length,
        }))
        .filter((candidate) => candidate.overlap > 0)
        .sort((left, right) => right.overlap - left.overlap)[0]?.graph;
    }
    const baseId = graph?.id ?? defaultWorkflowGraphId(config, component, index);
    let id = baseId;
    let suffix = 2;
    while (usedResultIds.has(id)) {
      id = `${baseId}-${suffix}`;
      suffix += 1;
    }
    usedResultIds.add(id);
    if (graph) usedGraphIds.add(graph.id);
    return {
      id,
      name: graph?.name ?? defaultWorkflowGraphName(config, component, index),
      description: graph?.description ?? null,
      directory_path: graph?.directory_path ?? defaultDirectoryPath,
      user_ids: graph?.user_ids ?? [],
      node_ids: component,
    };
  });
}

export function fallbackPosition(index: number): { x: number; y: number } {
  return {
    x: 120 + (index % 4) * 280,
    y: 100 + Math.floor(index / 4) * 180,
  };
}

export function copiedWorkflowSelectionCenter(nodes: ReadonlyArray<CopiedWorkflowNode>): { x: number; y: number } | null {
  if (nodes.length === 0) return null;
  const left = Math.min(...nodes.map((node) => node.position.x));
  const top = Math.min(...nodes.map((node) => node.position.y));
  const right = Math.max(...nodes.map((node) => node.position.x + node.width));
  const bottom = Math.max(...nodes.map((node) => node.position.y + node.height));
  return {
    x: (left + right) / 2,
    y: (top + bottom) / 2,
  };
}

export function nodeKindFromType(type: string): WorkflowNodeKind {
  if (type.startsWith("trigger:")) return "trigger";
  if (type.startsWith("gate:")) return "gate";
  if (type.startsWith("query:")) return "query";
  return "effect";
}

export function kindCollectionKey(kind: WorkflowNodeKind): "triggers" | "gates" | "effects" | "queries" {
  if (kind === "trigger") return "triggers";
  if (kind === "gate") return "gates";
  if (kind === "query") return "queries";
  return "effects";
}

export function inboundLimit(node: WorkflowNodeConfig): number | null {
  const kind = nodeKindFromType(node.type);
  if (kind === "trigger") return 0;
  if (node.type === "gate:merge" || node.type === "gate:for" || node.type === "gate:while") {
    return null;
  }
  return 1;
}

export function edgeId(edge: WorkflowEdge): string {
  return `${edge.source_id}:${edge.source_port ?? ""}->${edge.target_id}`;
}

export function wouldCreateCycle(config: WorkflowConfig, sourceId: string, targetId: string): boolean {
  const nextEdges = [...config.edges, { source_id: sourceId, target_id: targetId }];
  const downstream = new Map<string, string[]>();
  for (const edge of nextEdges) {
    const current = downstream.get(edge.source_id) ?? [];
    current.push(edge.target_id);
    downstream.set(edge.source_id, current);
  }
  const stack = [...(downstream.get(targetId) ?? [])];
  const visited = new Set<string>();
  while (stack.length > 0) {
    const current = stack.pop()!;
    if (current === sourceId) return true;
    if (visited.has(current)) continue;
    visited.add(current);
    for (const child of downstream.get(current) ?? []) {
      stack.push(child);
    }
  }
  return false;
}

export function cloneConfig(config: WorkflowConfig): WorkflowConfig {
  return JSON.parse(JSON.stringify(config)) as WorkflowConfig;
}

export function cloneLayout(layout: WorkflowLayout): WorkflowLayout {
  return JSON.parse(JSON.stringify(layout)) as WorkflowLayout;
}

export function cloneNodeConfig(node: WorkflowNodeConfig): WorkflowNodeConfig {
  return JSON.parse(JSON.stringify(node)) as WorkflowNodeConfig;
}

export function cloneWorkflowDocumentSurface(document: WorkflowDocumentSurface): WorkflowDocumentSurface {
  return JSON.parse(JSON.stringify({
    config: document.config,
    layout: document.layout,
    graphs: document.graphs,
  })) as WorkflowDocumentSurface;
}

export function workflowComparableValue(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map((entry) => workflowComparableValue(entry));
  }
  if (isRecord(value)) {
    const result: Record<string, unknown> = {};
    for (const key of Object.keys(value).sort()) {
      const nextValue = workflowComparableValue(value[key]);
      if (nextValue !== undefined) {
        result[key] = nextValue;
      }
    }
    return result;
  }
  return value;
}

export function workflowValuesEqual(left: unknown, right: unknown): boolean {
  return JSON.stringify(workflowComparableValue(left)) === JSON.stringify(workflowComparableValue(right));
}

export function normalizeWorkflowEditorDocument<T extends WorkflowDocumentLike>(
  document: T,
  defaultDirectoryPath: string,
): T {
  const backendOutputSchemas = document.output_schemas ?? {};
  const normalizedConfig = normalizeDerivedNodeFields(cloneConfig(document.config), backendOutputSchemas);
  const outputSchemas = deriveWorkflowOutputSchemas(normalizedConfig, backendOutputSchemas);
  const normalizedGraphs = normalizeWorkflowGraphs(
    normalizedConfig,
    document.graphs ?? [],
    defaultDirectoryPath,
  );
  return {
    ...document,
    config: normalizedConfig,
    graphs: normalizedGraphs,
    output_schemas: outputSchemas,
  } as T;
}

export function blankWorkflowGeneratorDocument(
  baseDocument: WorkflowEditorDocument | null,
  defaultDirectoryPath: string,
): WorkflowEditorDocument {
  return normalizeWorkflowEditorDocument(
    {
      config_path: baseDocument?.config_path ?? "",
      layout_path: baseDocument?.layout_path ?? "",
      config_exists: Boolean(baseDocument?.config_exists),
      layout_exists: Boolean(baseDocument?.layout_exists),
      document_scope: baseDocument?.document_scope ?? "inline",
      workflow_packs: baseDocument?.workflow_packs ?? [],
      selected_workflow_pack: baseDocument?.selected_workflow_pack ?? null,
      config: emptyConfig(),
      layout: { nodes: {} },
      graphs: [],
      output_schemas: {},
      validation_diagnostics: [],
      revision: baseDocument?.revision ?? "generator-draft",
    },
    defaultDirectoryPath,
  );
}

export function uniqueWorkflowNodeId(existingIds: ReadonlySet<string>, requestedId: string): string {
  const base = slugifyGraphId(requestedId || "generated-node");
  let candidate = base;
  let index = 2;
  while (existingIds.has(candidate)) {
    candidate = `${base}-${index}`;
    index += 1;
  }
  return candidate;
}

export function uniqueWorkflowGraphId(existingIds: ReadonlySet<string>, requestedId: string): string {
  const base = slugifyGraphId(requestedId || "generated-workflow");
  let candidate = base;
  let index = 2;
  while (existingIds.has(candidate)) {
    candidate = `${base}-${index}`;
    index += 1;
  }
  return candidate;
}

export function mergeGeneratedDraftIntoWorkflow(
  baseDocument: WorkflowEditorDocument,
  draftDocument: WorkflowEditorDocument,
  defaultDirectoryPath: string,
): { document: WorkflowEditorDocument; importedNodeIds: string[] } {
  const nextConfig = cloneConfig(baseDocument.config);
  nextConfig.triggers ??= [];
  nextConfig.gates ??= [];
  nextConfig.effects ??= [];
  nextConfig.queries ??= [];
  nextConfig.edges ??= [];

  const existingNodeIds = new Set(workflowNodeIds(nextConfig));
  const idMap = new Map<string, string>();
  const generatedNodes = workflowNodes(draftDocument.config);
  for (const node of generatedNodes) {
    const nextId = uniqueWorkflowNodeId(existingNodeIds, node.id);
    existingNodeIds.add(nextId);
    idMap.set(node.id, nextId);
  }

  const importedNodeIds: string[] = [];
  for (const node of generatedNodes) {
    const kind = nodeKindFromType(node.type);
    const nextId = idMap.get(node.id) ?? node.id;
    const nextNode = rewriteWorkflowNodeConfigBindings(
      {
        ...cloneNodeConfig(node),
        id: nextId,
      },
      idMap,
    );
    nextConfig[kindCollectionKey(kind)].push(nextNode);
    importedNodeIds.push(nextId);
  }

  const nextLayout = cloneLayout(baseDocument.layout);
  const draftPositions = generatedNodes
    .map((node, index) => draftDocument.layout.nodes[node.id] ?? fallbackPosition(index));
  const basePositions = Object.values(nextLayout.nodes);
  const draftMinX = draftPositions.length > 0 ? Math.min(...draftPositions.map((position) => position.x)) : 0;
  const draftMinY = draftPositions.length > 0 ? Math.min(...draftPositions.map((position) => position.y)) : 0;
  const baseMaxX = basePositions.length > 0 ? Math.max(...basePositions.map((position) => position.x)) : null;
  const xOffset = baseMaxX === null ? 0 : baseMaxX + 360 - draftMinX;
  const yOffset = baseMaxX === null ? 0 : 100 - draftMinY;
  generatedNodes.forEach((node, index) => {
    const nextId = idMap.get(node.id);
    if (!nextId) return;
    const position = draftDocument.layout.nodes[node.id] ?? fallbackPosition(index);
    nextLayout.nodes[nextId] = {
      x: position.x + xOffset,
      y: position.y + yOffset,
    };
  });

  const existingEdgeIds = new Set(nextConfig.edges.map(edgeId));
  for (const edge of draftDocument.config.edges ?? []) {
    const sourceId = idMap.get(edge.source_id);
    const targetId = idMap.get(edge.target_id);
    if (!sourceId || !targetId) continue;
    const nextEdge: WorkflowEdge = {
      ...edge,
      source_id: sourceId,
      target_id: targetId,
    };
    const nextEdgeId = edgeId(nextEdge);
    if (existingEdgeIds.has(nextEdgeId)) continue;
    existingEdgeIds.add(nextEdgeId);
    nextConfig.edges.push(nextEdge);
  }

  const existingGraphIds = new Set((baseDocument.graphs ?? []).map((graph) => graph.id));
  const generatedGraphs = (draftDocument.graphs.length > 0
    ? draftDocument.graphs
    : normalizeWorkflowGraphs(draftDocument.config, [], defaultDirectoryPath)
  ).map((graph, index) => {
    const nextId = uniqueWorkflowGraphId(existingGraphIds, graph.id || `generated-workflow-${index + 1}`);
    existingGraphIds.add(nextId);
    return {
      ...graph,
      id: nextId,
      directory_path: graph.directory_path || defaultDirectoryPath,
      node_ids: graph.node_ids.map((nodeId) => idMap.get(nodeId)).filter((nodeId): nodeId is string => Boolean(nodeId)),
    };
  }).filter((graph) => graph.node_ids.length > 0);
  const remappedDraftOutputSchemas = remapGeneratedDraftOutputSchemas(
    draftDocument.output_schemas ?? {},
    idMap,
  );

  const nextDocument = normalizeWorkflowEditorDocument(
    {
      ...baseDocument,
      config: nextConfig,
      layout: nextLayout,
      graphs: [...(baseDocument.graphs ?? []), ...generatedGraphs],
      output_schemas: {
        ...(baseDocument.output_schemas ?? {}),
        ...remappedDraftOutputSchemas,
      },
      validation_diagnostics: [],
      revision: baseDocument.revision,
    },
    defaultDirectoryPath,
  );
  return { document: nextDocument, importedNodeIds };
}

export function workflowEditableDocumentsEqual(left: WorkflowDocumentSurface, right: WorkflowDocumentSurface): boolean {
  return workflowValuesEqual(left, right);
}

export function workflowUndoDocumentsEqual(left: WorkflowDocumentSurface, right: WorkflowDocumentSurface): boolean {
  return workflowValuesEqual(
    {
      config: left.config,
      layout: left.layout,
      graphs: left.graphs,
    },
    {
      config: right.config,
      layout: right.layout,
      graphs: right.graphs,
    },
  );
}

export function dirtyWorkflowNodeIds(
  current: WorkflowEditorDocument | null,
  baseline: WorkflowEditorDocument | null,
  nodeEditOrigins: Record<string, string>,
): string[] {
  if (!current || !baseline) return [];
  const baselineNodesById = new Map(workflowNodes(baseline.config).map((node) => [node.id, node]));
  const dirtyNodeIds: string[] = [];
  for (const node of workflowNodes(current.config)) {
    const originId = nodeEditOrigins[node.id] ?? node.id;
    const baselineNode = baselineNodesById.get(originId);
    if (!baselineNode) {
      dirtyNodeIds.push(node.id);
      continue;
    }
    if (originId !== node.id) {
      dirtyNodeIds.push(node.id);
      continue;
    }
    if (!workflowValuesEqual(node, baselineNode)) {
      dirtyNodeIds.push(node.id);
      continue;
    }
    if (!workflowValuesEqual(current.layout.nodes[node.id], baseline.layout.nodes[originId])) {
      dirtyNodeIds.push(node.id);
    }
  }
  return dirtyNodeIds;
}

export function slugifyNodeType(type: string): string {
  return type.replace(/[^a-zA-Z0-9]+/g, "-").replace(/^-+|-+$/g, "").toLowerCase() || "node";
}

export function workflowPackReferenceValue(packReference: unknown): string {
  if (typeof packReference === "string") return packReference;
  if (packReference && typeof packReference === "object" && "reference" in packReference) {
    return String((packReference as { reference?: unknown }).reference ?? "");
  }
  return String(packReference ?? "");
}

export function workflowPackSlugFromReference(packReference: unknown): string | null {
  const reference = workflowPackReferenceValue(packReference).trim();
  if (!reference) return null;
  const packFileName = reference.split(/[\\/]/).pop() ?? reference;
  const withoutJson = packFileName.replace(/\.json$/i, "");
  const withoutVersion = withoutJson.replace(/\.v[0-9]+(?:-example)?$/i, "");
  return slugifyGraphId(withoutVersion || withoutJson);
}

export function selectedWorkflowPackSlug(
  config: WorkflowConfig,
  selectedWorkflowPackReference?: string | null,
): string | null {
  const selectedPackSlug = workflowPackSlugFromReference(selectedWorkflowPackReference);
  if (selectedPackSlug) return selectedPackSlug;
  const workflowPacks = config.workflow_packs;
  if (!Array.isArray(workflowPacks) || workflowPacks.length !== 1) return null;
  return workflowPackSlugFromReference(workflowPacks[0]);
}

export function generateNodeId(
  config: WorkflowConfig,
  kind: WorkflowNodeKind,
  type: string,
  label?: string | null,
  selectedWorkflowPackReference?: string | null,
): string {
  const collections = nodeCollections(config);
  const existingIds = new Set(
    [...collections.triggers, ...collections.gates, ...collections.effects, ...collections.queries].map(
      (node) => node.id,
    ),
  );
  const labelPart = slugifyGraphId(label?.trim() || slugifyNodeType(type).replace(`${kind}-`, ""));
  const packSlug = selectedWorkflowPackSlug(config, selectedWorkflowPackReference);
  const base = [packSlug, kind, labelPart].filter(Boolean).join("-");
  let index = 2;
  let candidate = base;
  while (existingIds.has(candidate)) {
    candidate = `${base}-${index}`;
    index += 1;
  }
  return candidate;
}

export function nodeById(config: WorkflowConfig, nodeId: string): WorkflowNodeConfig | undefined {
  const collections = nodeCollections(config);
  return [...collections.triggers, ...collections.gates, ...collections.effects, ...collections.queries].find(
    (node) => node.id === nodeId,
  );
}

export function validateConnection(
  config: WorkflowConfig,
  catalog: WorkflowEditorCatalog,
  sourceId: string,
  targetId: string,
  sourcePort?: string | null,
): string | null {
  if (!sourceId || !targetId) return "Edge endpoints are required.";
  if (sourceId === targetId) return "Nodes cannot connect to themselves.";
  if (
    config.edges.some(
      (edge) => edge.source_id === sourceId && edge.target_id === targetId && (edge.source_port ?? "") === (sourcePort ?? ""),
    )
  ) {
    return "That edge already exists.";
  }
  const sourceNode = nodeById(config, sourceId);
  const targetNode = nodeById(config, targetId);
  if (!sourceNode) return "Source node does not exist.";
  if (!targetNode) return "Target node does not exist.";
  const sourceCatalogEntry = getCatalogEntry(catalog, sourceNode.type);
  const allowedPorts = workflowOutputPortsForNode(sourceNode, sourceCatalogEntry?.output_ports ?? []);
  if (isSwitchGateType(sourceNode.type) && allowedPorts.length === 0) {
    return `Node ${sourceId} does not have any configured output ports.`;
  }
  const selectedPort = sourcePort ?? (sourceNode.type === "gate:throttle" ? "default" : null);
  const allowsImplicitDefault = sourceNode.type.startsWith("gate:") &&
    !isSwitchGateType(sourceNode.type) &&
    allowedPorts.every((port) => port.key === "error");
  if (
    (isSwitchGateType(sourceNode.type) || allowedPorts.length > 0) &&
    !(allowsImplicitDefault && selectedPort === null) &&
    !allowedPorts.some((port) => port.key === selectedPort)
  ) {
    return `Node ${sourceId} requires selecting one of its named output ports.`;
  }
  const limit = inboundLimit(targetNode);
  const inboundCount = config.edges.filter((edge) => edge.target_id === targetId).length;
  if (limit !== null && inboundCount >= limit) {
    return `Node ${targetId} already has the maximum supported inbound connections.`;
  }
  if (wouldCreateCycle(config, sourceId, targetId)) {
    if (
      (sourceNode.type === "gate:for" || sourceNode.type === "gate:while")
      && sourcePort === "body"
    ) return null;
    if (targetNode.type === "gate:for" || targetNode.type === "gate:while") return null;
    return "That edge would create a cycle.";
  }
  return null;
}

export function copyOutputSchema(schema: Record<string, unknown> | undefined): Record<string, unknown> {
  return { ...(schema ?? {}) };
}

export function rewriteWorkflowGraphNodeIds(
  graphs: ReadonlyArray<WorkflowGraphMetadata>,
  nodeIdMap: ReadonlyMap<string, string>,
): WorkflowGraphMetadata[] {
  if (nodeIdMap.size === 0) return graphs.map((graph) => ({ ...graph, node_ids: [...graph.node_ids] }));
  return graphs.map((graph) => ({
    ...graph,
    node_ids: graph.node_ids.map((nodeId) => nodeIdMap.get(nodeId) ?? nodeId),
  }));
}

export function rewriteWorkflowOutputSchemaNodeIds(
  outputSchemas: WorkflowOutputSchemas = {},
  nodeIdMap: ReadonlyMap<string, string>,
  fallbackOutputSchemas: WorkflowOutputSchemas = {},
): WorkflowOutputSchemas {
  if (nodeIdMap.size === 0) return { ...outputSchemas };
  const nextSchemas: WorkflowOutputSchemas = {};
  for (const [nodeId, schema] of Object.entries(outputSchemas)) {
    const nextNodeId = nodeIdMap.get(nodeId) ?? nodeId;
    const fallbackSchema = isRecord(fallbackOutputSchemas[nextNodeId])
      ? fallbackOutputSchemas[nextNodeId]
      : undefined;
    nextSchemas[nextNodeId] = copyOutputSchema(fallbackSchema ?? (isRecord(schema) ? schema : undefined));
  }
  for (const nextNodeId of nodeIdMap.values()) {
    if (Object.prototype.hasOwnProperty.call(nextSchemas, nextNodeId)) continue;
    const fallbackSchema = isRecord(fallbackOutputSchemas[nextNodeId])
      ? fallbackOutputSchemas[nextNodeId]
      : undefined;
    if (fallbackSchema) nextSchemas[nextNodeId] = copyOutputSchema(fallbackSchema);
  }
  return nextSchemas;
}

export function remapGeneratedDraftOutputSchemas(
  outputSchemas: WorkflowOutputSchemas = {},
  nodeIdMap: ReadonlyMap<string, string>,
): WorkflowOutputSchemas {
  const nextSchemas: WorkflowOutputSchemas = {};
  for (const [draftNodeId, importedNodeId] of nodeIdMap.entries()) {
    const schema = outputSchemas[draftNodeId];
    if (isRecord(schema)) {
      nextSchemas[importedNodeId] = copyOutputSchema(schema);
    }
  }
  return nextSchemas;
}

export function normalizeDerivedNodeFields(
  config: WorkflowConfig,
  baseOutputSchemas: WorkflowOutputSchemas = {},
): WorkflowConfig {
  const nextConfig = cloneConfig(config);
  const outputSchemas = deriveWorkflowOutputSchemas(nextConfig, baseOutputSchemas);
  for (const gate of nextConfig.gates ?? []) {
    if (isPassThroughGateType(gate.type)) {
      gate.outputs = declaredOutputsForNode(gate, outputSchemas);
    } else if (gate.type === "gate:map") {
      gate.outputs = declaredOutputsForNode(gate, outputSchemas);
    } else if (gate.type === "gate:aggregate") {
      gate.outputs = declaredOutputsForNode(gate, outputSchemas);
    } else if (gate.type === "gate:buffer") {
      if (typeof gate.config.timeout === "string" && gate.config.timeout.trim() === "") {
        delete gate.config.timeout;
      }
      gate.outputs = declaredOutputsForNode(gate, outputSchemas);
    } else if (gate.type === "gate:deserialize") {
      gate.outputs = declaredOutputsForNode(gate, outputSchemas);
    } else if (gate.type === "gate:serialize") {
      gate.outputs = declaredOutputsForNode(gate, outputSchemas);
    } else if (gate.type === "gate:dedupe") {
      gate.outputs = declaredOutputsForNode(gate, outputSchemas);
    } else if (gate.type === "gate:await-agent") {
      delete gate.config.completion_trigger_id;
      gate.outputs = declaredOutputsForNode(gate, outputSchemas);
    } else if (gate.type === "gate:for") {
      gate.outputs = declaredOutputsForNode(gate, outputSchemas);
    } else if (gate.type === "gate:while") {
      gate.outputs = declaredOutputsForNode(gate, outputSchemas);
    } else if (gate.type === "gate:merge") {
      gate.outputs = declaredOutputsForNode(gate, outputSchemas);
    }
  }
  return nextConfig;
}
