import assert from "node:assert/strict";
import test from "node:test";

import {
  rewriteWorkflowBindingReferences,
  rewriteWorkflowNodeConfigBindings,
} from "../src/workflowBindings.ts";

const idMap = new Map([
  ["trigger-old", "trigger-new"],
  ["effect-old", "effect-new"],
  ["fallback-old", "fallback-new"],
  ["node.with.dots", "node-copy.with.dots"],
]);

test("rewrites exact copied-node bindings", () => {
  assert.equal(
    rewriteWorkflowBindingReferences("${{trigger-old.ticket.title}}", idMap),
    "${{trigger-new.ticket.title}}",
  );
});

test("rewrites only copied-node references in template strings", () => {
  assert.equal(
    rewriteWorkflowBindingReferences(
      "Review ${{trigger-old.ticket.title}} from ${{outside-node.ticket.title}}",
      idMap,
    ),
    "Review ${{trigger-new.ticket.title}} from ${{outside-node.ticket.title}}",
  );
});

test("rewrites primary and fallback references", () => {
  assert.equal(
    rewriteWorkflowBindingReferences(
      "${{effect-old.target_dir ?? fallback-old.target_dir}}",
      idMap,
    ),
    "${{effect-new.target_dir ?? fallback-new.target_dir}}",
  );
});

test("rewrites node ids that contain dots", () => {
  assert.equal(
    rewriteWorkflowBindingReferences("${{node.with.dots.output.path}}", idMap),
    "${{node-copy.with.dots.output.path}}",
  );
});

test("leaves malformed bindings unchanged", () => {
  assert.equal(
    rewriteWorkflowBindingReferences("Review ${{trigger-old.ticket ?? {bad}}}", idMap),
    "Review ${{trigger-old.ticket ?? {bad}}}",
  );
  assert.equal(
    rewriteWorkflowBindingReferences("Review ${{trigger-old.ticket.title", idMap),
    "Review ${{trigger-old.ticket.title",
  );
});

test("rewrites nested node config values without changing node identity fields", () => {
  const node = {
    id: "effect-old",
    display_name: "Effect old",
    type: "effect:test",
    prompt: "Review ${{trigger-old.ticket.title}}",
    files: [
      {
        content: "${{effect-old.result.path}}",
      },
    ],
  };

  assert.deepEqual(rewriteWorkflowNodeConfigBindings(node, idMap), {
    id: "effect-old",
    display_name: "Effect old",
    type: "effect:test",
    prompt: "Review ${{trigger-new.ticket.title}}",
    files: [
      {
        content: "${{effect-new.result.path}}",
      },
    ],
  });
});
