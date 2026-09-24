import { type Node, type Viewport } from "@xyflow/react";
import { workflowNodeBounds as nodeBounds } from "../../workflowNodeSnap";
import {
  WORKFLOW_TUTORIAL_DEFINITIONS,
  type WorkflowTutorialRequiredAction,
  type WorkflowTutorialStep,
  workflowTutorialTargetNodeId,
} from "../../workflowTutorials";
import {
  type GraphNodeData,
  type TutorialBubbleSize,
  type WorkflowTutorialProgress,
} from "./editorTypes";
import { clamp, isRecord } from "./editorValues";

export function workflowTutorialProgressIds(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  const validTutorialIds = new Set(WORKFLOW_TUTORIAL_DEFINITIONS.map((definition) => definition.id));
  const seen = new Set<string>();
  const result: string[] = [];
  for (const entry of value) {
    if (typeof entry !== "string") continue;
    const tutorialId = entry.trim();
    if (!tutorialId || !validTutorialIds.has(tutorialId) || seen.has(tutorialId)) continue;
    seen.add(tutorialId);
    result.push(tutorialId);
  }
  return result;
}

export function normalizeWorkflowTutorialProgress(value: unknown): WorkflowTutorialProgress {
  if (!isRecord(value)) {
    return { completedTutorialIds: [], skippedTutorialIds: [] };
  }
  return {
    completedTutorialIds: workflowTutorialProgressIds(value.completedTutorialIds ?? value.completed_tutorial_ids),
    skippedTutorialIds: workflowTutorialProgressIds(value.skippedTutorialIds ?? value.skipped_tutorial_ids),
  };
}

export function workflowTutorialProgressApiPayload(progress: WorkflowTutorialProgress): Record<string, string[]> {
  return {
    completed_tutorial_ids: progress.completedTutorialIds,
    skipped_tutorial_ids: progress.skippedTutorialIds,
  };
}

export function decorateWorkflowTutorialNodes(
  nodes: Node<GraphNodeData>[],
  step: WorkflowTutorialStep | null,
): Node<GraphNodeData>[] {
  if (!step) return nodes;
  const targetNodeId = workflowTutorialTargetNodeId(step.target);
  const connectSourceNodeId = step.requiredAction.kind === "connect" ? step.requiredAction.sourceNodeId : null;
  const connectTargetNodeId = step.requiredAction.kind === "connect" ? step.requiredAction.targetNodeId : null;
  return nodes.map((node) => {
    const isTarget = targetNodeId !== null && node.id === targetNodeId;
    const isConnectParticipant =
      connectSourceNodeId !== null &&
      connectTargetNodeId !== null &&
      (node.id === connectSourceNodeId || node.id === connectTargetNodeId);
    return {
      ...node,
      style: {
        ...(node.style ?? {}),
        visibility: "visible",
      },
      data: {
        ...node.data,
        tutorialHighlight: (isTarget || isConnectParticipant) && step.highlightMode !== "none",
        tutorialBlocked: targetNodeId !== null ? !(isTarget || isConnectParticipant) : false,
        tutorialHighlightMode: step.highlightMode,
      },
    };
  });
}

export function tutorialNodeLabel(nodeId: string): string {
  switch (nodeId) {
    case "tutorial-trigger":
      return "Agent run completed";
    case "tutorial-effect":
      return "Prompt agent";
    default:
      return nodeId;
  }
}

export function tutorialFieldLabel(fieldKey: string): string {
  switch (fieldKey) {
    case "prompt":
      return "Prompt";
    default:
      return fieldKey;
  }
}

export function tutorialRequiredActionLabel(action: WorkflowTutorialRequiredAction): string {
  switch (action.kind) {
    case "next":
      return "Next";
    case "select-node":
      return `Select ${tutorialNodeLabel(action.nodeId)}`;
    case "add-node":
      return `Add ${action.nodeType}`;
    case "connect":
      return `Connect ${tutorialNodeLabel(action.sourceNodeId)} to ${tutorialNodeLabel(action.targetNodeId)}`;
    case "bind-field":
      return `Bind ${tutorialFieldLabel(action.fieldKey)}`;
    case "reset":
      return "Reset";
    default:
      return "Continue";
  }
}

export function tutorialRequiredActionDescription(action: WorkflowTutorialRequiredAction): string {
  switch (action.kind) {
    case "next":
      return "Press Next to continue.";
    case "select-node":
      return `Click ${tutorialNodeLabel(action.nodeId)} to continue.`;
    case "add-node":
      return `Add ${action.nodeType} at the tutorial position.`;
    case "connect":
      return `Right-click ${tutorialNodeLabel(action.sourceNodeId)}, choose Completed, then click ${tutorialNodeLabel(action.targetNodeId)}.`;
    case "bind-field":
      return `Bind ${tutorialFieldLabel(action.fieldKey)} to the trigger's Completed output.`;
    case "reset":
      return "Reset the tutorial to practice again.";
    default:
      return "Follow the tutorial prompt to continue.";
  }
}

export function isMeaningfulViewport(viewport: Viewport): boolean {
  return Math.abs(viewport.x) > 0.5 || Math.abs(viewport.y) > 0.5 || Math.abs(viewport.zoom - 1) > 0.01;
}

export function clampTutorialBubblePosition(
  preferred: { left: number; top: number },
  size: TutorialBubbleSize,
  canvasSize: { width: number; height: number },
  padding = 16,
): { left: number; top: number } {
  const maxLeft = Math.max(padding, canvasSize.width - size.width - padding);
  const maxTop = Math.max(padding, canvasSize.height - size.height - padding);
  return {
    left: clamp(preferred.left, padding, maxLeft),
    top: clamp(preferred.top, padding, maxTop),
  };
}

export function tutorialBubblePosition(
  step: WorkflowTutorialStep,
  nodes: ReadonlyArray<Node<GraphNodeData>>,
  viewport: Viewport,
  bubbleSize: TutorialBubbleSize,
  canvasSize: { width: number; height: number },
): { left: number; top: number } {
  const padding = 16;
  const gap = 20;
  if (step.target.kind === "canvas") {
    return clampTutorialBubblePosition(
      {
        left: (canvasSize.width - bubbleSize.width) / 2,
        top: (canvasSize.height - bubbleSize.height) / 2,
      },
      bubbleSize,
      canvasSize,
      padding,
    );
  }
  const targetNode = nodes.find((node) => node.id === step.target.nodeId);
  if (!targetNode) {
    return clampTutorialBubblePosition(
      {
        left: (canvasSize.width - bubbleSize.width) / 2,
        top: (canvasSize.height - bubbleSize.height) / 2,
      },
      bubbleSize,
      canvasSize,
      padding,
    );
  }
  const bounds = nodeBounds(targetNode);
  const left = bounds.left * viewport.zoom + viewport.x;
  const top = bounds.top * viewport.zoom + viewport.y;
  const right = bounds.right * viewport.zoom + viewport.x;
  const bottom = bounds.bottom * viewport.zoom + viewport.y;
  const centerX = (left + right) / 2;
  const centerY = (top + bottom) / 2;
  let preferredLeft = centerX - bubbleSize.width / 2;
  let preferredTop = centerY - bubbleSize.height / 2;
  switch (step.bubblePlacement) {
    case "left":
      preferredLeft = left - bubbleSize.width - gap;
      break;
    case "right":
      preferredLeft = right + gap;
      break;
    case "top":
      preferredTop = top - bubbleSize.height - gap;
      break;
    case "bottom":
      preferredTop = bottom + gap;
      break;
    case "center":
      preferredLeft = (canvasSize.width - bubbleSize.width) / 2;
      preferredTop = (canvasSize.height - bubbleSize.height) / 2;
      break;
    default:
      break;
  }
  if (step.bubblePlacement === "left" || step.bubblePlacement === "right") {
    preferredTop = centerY - bubbleSize.height / 2;
  }
  if (step.bubblePlacement === "top" || step.bubblePlacement === "bottom") {
    preferredLeft = centerX - bubbleSize.width / 2;
  }
  return clampTutorialBubblePosition({ left: preferredLeft, top: preferredTop }, bubbleSize, canvasSize, padding);
}
