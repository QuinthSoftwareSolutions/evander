export const SNAP_THRESHOLD = 24;
export const FALLBACK_NODE_WIDTH = 212;
export const FALLBACK_NODE_HEIGHT = 108;

export type SnapGuide = {
  orientation: "horizontal" | "vertical";
  position: number;
};

type NodeBounds = {
  left: number;
  centerX: number;
  right: number;
  top: number;
  centerY: number;
  bottom: number;
};

type SnappableNode = {
  id: string;
  position: { x: number; y: number };
  measured?: { width?: number; height?: number };
  width?: number;
  height?: number;
};

export function workflowNodeBounds(node: SnappableNode): NodeBounds {
  const width = node.measured?.width ?? node.width ?? FALLBACK_NODE_WIDTH;
  const height = node.measured?.height ?? node.height ?? FALLBACK_NODE_HEIGHT;
  const left = node.position.x;
  const top = node.position.y;
  return {
    left,
    centerX: left + width / 2,
    right: left + width,
    top,
    centerY: top + height / 2,
    bottom: top + height,
  };
}

function closestSnapDelta(
  sourceAnchors: number[],
  targetAnchors: number[],
  threshold: number,
): { delta: number; guide: number } | null {
  let closest: { delta: number; guide: number } | null = null;
  for (const sourceAnchor of sourceAnchors) {
    for (const targetAnchor of targetAnchors) {
      const delta = targetAnchor - sourceAnchor;
      if (Math.abs(delta) > threshold) continue;
      if (!closest || Math.abs(delta) < Math.abs(closest.delta)) {
        closest = { delta, guide: targetAnchor };
      }
    }
  }
  return closest;
}

export function snapDraggedNodes<T extends SnappableNode>(
  activeNode: T,
  draggedNodes: T[],
  allNodes: T[],
  threshold = SNAP_THRESHOLD,
): { snappedNodes: T[]; guides: SnapGuide[] } {
  const draggedNodeIds = new Set(draggedNodes.map((node) => node.id));
  const stationaryNodes = allNodes.filter((node) => !draggedNodeIds.has(node.id));
  if (stationaryNodes.length === 0) {
    return { snappedNodes: draggedNodes, guides: [] };
  }
  const activeBounds = workflowNodeBounds(activeNode);
  let bestX: { delta: number; guide: number } | null = null;
  let bestY: { delta: number; guide: number } | null = null;

  for (const candidate of stationaryNodes) {
    const candidateBounds = workflowNodeBounds(candidate);
    const xSnap = closestSnapDelta(
      [activeBounds.left, activeBounds.centerX, activeBounds.right],
      [candidateBounds.left, candidateBounds.centerX, candidateBounds.right],
      threshold,
    );
    if (xSnap && (!bestX || Math.abs(xSnap.delta) < Math.abs(bestX.delta))) {
      bestX = xSnap;
    }
    const ySnap = closestSnapDelta(
      [activeBounds.top, activeBounds.centerY, activeBounds.bottom],
      [candidateBounds.top, candidateBounds.centerY, candidateBounds.bottom],
      threshold,
    );
    if (ySnap && (!bestY || Math.abs(ySnap.delta) < Math.abs(bestY.delta))) {
      bestY = ySnap;
    }
  }

  if (!bestX && !bestY) {
    return { snappedNodes: draggedNodes, guides: [] };
  }

  const xDelta = bestX?.delta ?? 0;
  const yDelta = bestY?.delta ?? 0;
  return {
    snappedNodes: draggedNodes.map((node) => ({
      ...node,
      position: {
        x: node.position.x + xDelta,
        y: node.position.y + yDelta,
      },
    }) as T),
    guides: [
      ...(bestX ? [{ orientation: "vertical" as const, position: bestX.guide }] : []),
      ...(bestY ? [{ orientation: "horizontal" as const, position: bestY.guide }] : []),
    ],
  };
}
