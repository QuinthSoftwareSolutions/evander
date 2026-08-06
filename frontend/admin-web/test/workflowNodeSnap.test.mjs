import assert from "node:assert/strict";
import test from "node:test";

import {
  SNAP_THRESHOLD,
  snapDraggedNodes,
} from "../src/workflowNodeSnap.ts";

function node(id, x, y, width = 100, height = 60) {
  return {
    id,
    position: { x, y },
    measured: { width, height },
  };
}

test("snaps dragged nodes within the alignment threshold", () => {
  const stationary = node("stationary", 300, 100);
  const dragged = node("dragged", 292, 220);

  const { snappedNodes, guides } = snapDraggedNodes(dragged, [dragged], [stationary, dragged]);

  assert.deepEqual(snappedNodes[0].position, { x: 300, y: 220 });
  assert.deepEqual(guides, [{ orientation: "vertical", position: 300 }]);
});

test("snaps selected groups by the active node delta", () => {
  const stationary = node("stationary", 300, 100);
  const active = node("active", 292, 220);
  const sibling = node("sibling", 500, 420);

  const { snappedNodes } = snapDraggedNodes(active, [active, sibling], [stationary, active, sibling]);
  const snappedById = new Map(snappedNodes.map((snapped) => [snapped.id, snapped]));

  assert.deepEqual(snappedById.get("active")?.position, { x: 300, y: 220 });
  assert.deepEqual(snappedById.get("sibling")?.position, { x: 508, y: 420 });
});

test("snaps horizontal and vertical alignment together", () => {
  const stationary = node("stationary", 300, 100);
  const dragged = node("dragged", 292, 94);

  const { snappedNodes, guides } = snapDraggedNodes(dragged, [dragged], [stationary, dragged]);

  assert.deepEqual(snappedNodes[0].position, { x: 300, y: 100 });
  assert.deepEqual(guides, [
    { orientation: "vertical", position: 300 },
    { orientation: "horizontal", position: 100 },
  ]);
});

test("does not snap outside the alignment threshold", () => {
  const stationary = node("stationary", 300, 100);
  const dragged = node("dragged", 300 + SNAP_THRESHOLD + 1, 220);

  const { snappedNodes, guides } = snapDraggedNodes(dragged, [dragged], [stationary, dragged]);

  assert.deepEqual(snappedNodes[0].position, dragged.position);
  assert.deepEqual(guides, []);
});
