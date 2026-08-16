import assert from "node:assert/strict";
import test from "node:test";

import {
  rewriteWorkflowConfigBindingReferences,
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
    config: {
      prompt: "Review ${{trigger-old.ticket.title}}",
      files: [
        {
          content: "${{effect-old.result.path}}",
        },
      ]
    }
  };

  assert.deepEqual(rewriteWorkflowNodeConfigBindings(node, idMap), {
    id: "effect-old",
    display_name: "Effect old",
    type: "effect:test",
    config: {
      prompt: "Review ${{trigger-new.ticket.title}}",
      files: [
        {
          content: "${{effect-new.result.path}}",
        },
      ]
    }
  });
});

test("rewrites workflow config binding references across all node collections", () => {
  const config = {
    runtime_dir: "/tmp/workflow",
    poll_interval_seconds: 30,
    lock_path: "/tmp/workflow.lock",
    triggers: [
      {
        id: "trigger-old",
        display_name: "Trigger old",
        type: "trigger:test",
        config: {
          prompt: "Start ${{trigger-old.ticket.title}}",
          metadata: {
            title: "Ticket ${{trigger-old.ticket.title}}",
            list: [
              "${{trigger-old.ticket.title}}",
              "${{effect-old.result.path}}",
              "${{workflow.run.id}}",
            ],
          }
        }
      },
    ],
    gates: [
      {
        id: "gate-old",
        display_name: "Gate old",
        type: "gate:test",
        config: {
          decision: "${{trigger-old.ticket.title ?? fallback-old.ticket.title}}",
          malformed: "Review ${{trigger-old.ticket ?? {bad}}}"
        }
      },
    ],
    effects: [
      {
        id: "effect-old",
        display_name: "Effect old",
        type: "effect:test",
        config: {
          prompt: "Review ${{trigger-old.ticket.title}} and ${{outside-node.ticket.title}}",
          payload: [
            {
              body: "Object ${{trigger-old.ticket.title}}",
              nested: {
                note: "Again ${{effect-old.result.path}}",
              },
            },
          ]
        }
      },
    ],
    queries: [
      {
        id: "query-old",
        display_name: "Query old",
        type: "query:test",
        config: {
          filter: "${{trigger-old.ticket.title ?? fallback-old.ticket.title}}"
        }
      },
    ],
    edges: [
      {
        source_id: "trigger-old",
        target_id: "gate-old",
      },
    ],
  };
  const nodeIdMap = new Map([
    ["trigger-old", "trigger-new"],
    ["gate-old", "gate-new"],
    ["effect-old", "effect-new"],
    ["fallback-old", "fallback-new"],
  ]);

  const rewritten = rewriteWorkflowConfigBindingReferences(config, nodeIdMap);

  assert.notStrictEqual(rewritten, config);
  assert.strictEqual(rewritten.edges, config.edges);
  assert.strictEqual(rewritten.runtime_dir, config.runtime_dir);
  assert.strictEqual(rewritten.poll_interval_seconds, config.poll_interval_seconds);
  assert.strictEqual(rewritten.lock_path, config.lock_path);
  assert.notStrictEqual(rewritten.triggers, config.triggers);
  assert.notStrictEqual(rewritten.gates, config.gates);
  assert.notStrictEqual(rewritten.effects, config.effects);
  assert.notStrictEqual(rewritten.queries, config.queries);
  assert.deepEqual(rewritten.triggers[0], {
    id: "trigger-old",
    display_name: "Trigger old",
    type: "trigger:test",
    config: {
      prompt: "Start ${{trigger-new.ticket.title}}",
      metadata: {
        title: "Ticket ${{trigger-new.ticket.title}}",
        list: [
          "${{trigger-new.ticket.title}}",
          "${{effect-new.result.path}}",
          "${{workflow.run.id}}",
        ],
      }
    }
  });
  assert.deepEqual(rewritten.gates[0], {
    id: "gate-old",
    display_name: "Gate old",
    type: "gate:test",
    config: {
      decision: "${{trigger-new.ticket.title ?? fallback-new.ticket.title}}",
      malformed: "Review ${{trigger-old.ticket ?? {bad}}}"
    }
  });
  assert.deepEqual(rewritten.effects[0], {
    id: "effect-old",
    display_name: "Effect old",
    type: "effect:test",
    config: {
      prompt: "Review ${{trigger-new.ticket.title}} and ${{outside-node.ticket.title}}",
      payload: [
        {
          body: "Object ${{trigger-new.ticket.title}}",
          nested: {
            note: "Again ${{effect-new.result.path}}",
          },
        },
      ]
    }
  });
  assert.deepEqual(rewritten.queries[0], {
    id: "query-old",
    display_name: "Query old",
    type: "query:test",
    config: {
      filter: "${{trigger-new.ticket.title ?? fallback-new.ticket.title}}"
    }
  });
  assert.equal(config.triggers[0].config.prompt, "Start ${{trigger-old.ticket.title}}");
  assert.equal(config.gates[0].config.decision, "${{trigger-old.ticket.title ?? fallback-old.ticket.title}}");
  assert.equal(config.effects[0].config.prompt, "Review ${{trigger-old.ticket.title}} and ${{outside-node.ticket.title}}");
  assert.equal(config.queries[0].config.filter, "${{trigger-old.ticket.title ?? fallback-old.ticket.title}}");
});

test("returns the original workflow config when no node ids are mapped", () => {
  const config = {
    triggers: [],
    gates: [],
    effects: [],
    queries: [],
    edges: [],
    runtime_dir: "/tmp/workflow",
  };

  assert.strictEqual(rewriteWorkflowConfigBindingReferences(config, new Map()), config);
});
