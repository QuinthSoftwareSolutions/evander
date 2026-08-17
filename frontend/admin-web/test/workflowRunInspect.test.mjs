import assert from "node:assert/strict";
import test from "node:test";

import {
  sameGraphNodeIdsForRunAnchors,
  workflowRunDetailActivityCounts,
} from "../src/workflowRunInspect.ts";

test("run inspect falls back to the connected component when graph metadata is absent", () => {
  const visibleNodeIds = sameGraphNodeIdsForRunAnchors(
    new Set(["trigger-1", "agent-1", "notify-1", "trigger-2", "unrelated-1"]),
    [
      { source_id: "trigger-1", target_id: "agent-1" },
      { source_id: "agent-1", target_id: "notify-1" },
      { source_id: "trigger-2", target_id: "unrelated-1" },
    ],
    [],
    new Set(["trigger-1", "agent-1"]),
  );

  assert.deepEqual([...visibleNodeIds].sort(), ["agent-1", "notify-1", "trigger-1"]);
});

test("run inspect uses matched graph metadata when it is available", () => {
  const visibleNodeIds = sameGraphNodeIdsForRunAnchors(
    new Set(["trigger-1", "agent-1", "notify-1", "trigger-2", "unrelated-1"]),
    [
      { source_id: "trigger-1", target_id: "agent-1" },
      { source_id: "agent-1", target_id: "notify-1" },
      { source_id: "trigger-2", target_id: "unrelated-1" },
    ],
    [
      {
        id: "graph-1",
        name: "Run graph",
        directory_path: "root",
        user_ids: [],
        node_ids: ["trigger-1", "agent-1", "notify-1"],
      },
      {
        id: "graph-2",
        name: "Other graph",
        directory_path: "root",
        user_ids: [],
        node_ids: ["trigger-2", "unrelated-1"],
      },
    ],
    new Set(["agent-1"]),
  );

  assert.deepEqual([...visibleNodeIds].sort(), ["agent-1", "notify-1", "trigger-1"]);
});

test("run detail activity counts do not double-count active nodes with detail records", () => {
  const counts = workflowRunDetailActivityCounts({
    source_node_id: "trigger-1",
    trigger_event: { trigger_id: "trigger-1" },
    nodes: [
      {
        node_id: "effect-failed",
        status: "failed",
      },
      {
        node_id: "effect-completed",
        delivery_state: "delivered",
      },
    ],
    active_nodes: [
      {
        node_id: "effect-failed",
        status: "failed",
        count: 1,
      },
      {
        node_id: "effect-waiting",
        status: "waiting",
        count: 2,
      },
    ],
  });

  assert.deepEqual(counts, {
    "effect-failed": { failed: 1 },
    "effect-completed": { delivered: 1 },
    "effect-waiting": { waiting: 2 },
    "trigger-1": { completed: 1 },
  });
});
