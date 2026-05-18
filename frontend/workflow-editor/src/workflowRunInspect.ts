import type { WorkflowNodeActivityCounts, WorkflowRunDetail } from "./types";

type WorkflowEdgeLike = {
  source_id: string;
  target_id: string;
};

type WorkflowGraphMetadataLike = {
  node_ids: string[];
};

export function connectedNodeIdsForRunAnchors(
  allNodeIds: ReadonlySet<string>,
  edges: ReadonlyArray<WorkflowEdgeLike>,
  anchorNodeIds: ReadonlySet<string>,
): Set<string> {
  const visibleNodeIds = new Set([...anchorNodeIds].filter((nodeId) => allNodeIds.has(nodeId)));
  if (visibleNodeIds.size === 0) return visibleNodeIds;

  const neighbors = new Map<string, Set<string>>();
  allNodeIds.forEach((nodeId) => neighbors.set(nodeId, new Set()));
  for (const edge of edges) {
    if (!allNodeIds.has(edge.source_id) || !allNodeIds.has(edge.target_id)) continue;
    neighbors.get(edge.source_id)?.add(edge.target_id);
    neighbors.get(edge.target_id)?.add(edge.source_id);
  }

  const stack = [...visibleNodeIds];
  while (stack.length > 0) {
    const current = stack.pop()!;
    for (const neighbor of neighbors.get(current) ?? []) {
      if (visibleNodeIds.has(neighbor)) continue;
      visibleNodeIds.add(neighbor);
      stack.push(neighbor);
    }
  }
  return visibleNodeIds;
}

export function sameGraphNodeIdsForRunAnchors(
  allNodeIds: ReadonlySet<string>,
  edges: ReadonlyArray<WorkflowEdgeLike>,
  graphs: ReadonlyArray<WorkflowGraphMetadataLike>,
  anchorNodeIds: ReadonlySet<string>,
): Set<string> {
  const candidateGraphs = graphs.filter((graph) =>
    graph.node_ids.some((nodeId) => allNodeIds.has(nodeId)),
  );
  const matchedGraph = candidateGraphs.find((graph) =>
    graph.node_ids.some((nodeId) => anchorNodeIds.has(nodeId)),
  );
  if (!matchedGraph) {
    return connectedNodeIdsForRunAnchors(allNodeIds, edges, anchorNodeIds);
  }
  return new Set(matchedGraph.node_ids.filter((nodeId) => allNodeIds.has(nodeId)));
}

export function workflowRunDetailActivityCounts(
  detail: WorkflowRunDetail,
): Record<string, WorkflowNodeActivityCounts> {
  const counts: Record<string, WorkflowNodeActivityCounts> = {};
  const detailedNodeIds = new Set<string>();
  const increment = (
    nodeId: string | null | undefined,
    status: string | null | undefined,
    count = 1,
  ) => {
    if (!nodeId || !status) return;
    counts[nodeId] ??= {};
    counts[nodeId][status] = (counts[nodeId][status] ?? 0) + count;
  };
  detail.nodes.forEach((node) => {
    if (node.node_id) detailedNodeIds.add(node.node_id);
    increment(node.node_id, node.status ?? node.delivery_state ?? "completed");
  });
  detail.active_nodes.forEach((activity) => {
    if (activity.node_id && detailedNodeIds.has(activity.node_id)) return;
    increment(activity.node_id, activity.status, activity.count);
  });
  const triggerNodeId = detail.source_node_id ?? detail.trigger_event?.trigger_id ?? null;
  if (triggerNodeId && !counts[triggerNodeId]) {
    increment(triggerNodeId, "completed");
  }
  return counts;
}
