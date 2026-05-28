import assert from "node:assert/strict";
import test from "node:test";

import {
  declaredOutputsForNode,
  deriveWorkflowOutputSchemas,
  flattenOutputPaths,
  guaranteedUpstreamNodeIds,
  selectedNodeOutputContract,
  upstreamBindingOptions,
} from "../src/workflowNodeOutputs.ts";

function createWorkflowConfig() {
  return {
    triggers: [
      {
        id: "trigger-1",
        display_name: "Incoming ticket",
        type: "trigger:manual",
        outputs: {
          ticket: {
            type: "object",
            properties: {
              issue_number: { type: "integer" },
              labels: {
                type: "array",
                items: {
                  type: "object",
                  properties: {
                    name: { type: "string", format: "label" },
                  },
                },
              },
            },
          },
          status: { type: "string", enum: ["open", "closed"] },
        },
      },
      {
        id: "trigger-2",
        display_name: "Secondary stream",
        type: "trigger:manual",
        outputs: {
          status: { type: "string" },
        },
      },
    ],
    gates: [
      {
        id: "gate-filter-1",
        display_name: "Filter tickets",
        type: "gate:filter",
      },
      {
        id: "gate-debounce-1",
        display_name: "Debounce tickets",
        type: "gate:debounce",
      },
      {
        id: "gate-switch-1",
        display_name: "Switch tickets",
        type: "gate:switch",
      },
      {
        id: "gate-merge-1",
        display_name: "Merge streams",
        type: "gate:merge",
      },
      {
        id: "gate-map-1",
        display_name: "Map payload",
        type: "gate:map",
        template: {
          summary: "Ticket ready",
          attempts: 2,
          enabled: true,
          payload: ["first", "second"],
        },
      },
    ],
    effects: [
      {
        id: "effect-1",
        display_name: "Dispatch agent",
        type: "effect:agent:create",
      },
    ],
    queries: [],
    edges: [
      { source_id: "trigger-1", target_id: "gate-filter-1" },
      { source_id: "gate-filter-1", target_id: "gate-debounce-1" },
      { source_id: "gate-debounce-1", target_id: "gate-switch-1" },
      { source_id: "gate-debounce-1", target_id: "effect-1" },
      { source_id: "trigger-1", target_id: "gate-merge-1" },
      { source_id: "trigger-2", target_id: "gate-merge-1" },
    ],
  };
}

test("flattens object and array output paths with stable nested paths", () => {
  const schema = {
    type: "object",
    properties: {
      issue_number: { type: "integer" },
      labels: {
        type: "array",
        items: {
          type: "object",
          properties: {
            name: { type: "string" },
          },
        },
      },
    },
  };

  assert.deepEqual(flattenOutputPaths("ticket", schema), [
    "ticket",
    "ticket.issue_number",
    "ticket.labels",
    "ticket.labels.0",
    "ticket.labels.0.name",
  ]);
});

test("derives pass-through outputs for filter, debounce, and switch gates", () => {
  const config = createWorkflowConfig();
  const outputSchemas = deriveWorkflowOutputSchemas(config);

  assert.deepEqual(outputSchemas["gate-filter-1"], outputSchemas["trigger-1"]);
  assert.deepEqual(outputSchemas["gate-debounce-1"], outputSchemas["trigger-1"]);
  assert.deepEqual(outputSchemas["gate-switch-1"], outputSchemas["trigger-1"]);
});

test("builds a selected-node output contract for declared outputs", () => {
  const config = createWorkflowConfig();
  const contract = selectedNodeOutputContract(config, "trigger-1", deriveWorkflowOutputSchemas(config));

  assert.equal(contract.state, "declared");
  assert.equal(contract.note, "Outputs are declared by backend schema or node.outputs.");
  assert.deepEqual(contract.sourceNodeIds, ["trigger-1"]);
  assert.deepEqual(contract.rows.map((row) => row.path), [
    "ticket",
    "ticket.issue_number",
    "ticket.labels",
    "ticket.labels.0",
    "ticket.labels.0.name",
    "status",
  ]);
  assert.equal(contract.rows[0].bindingPath, "trigger-1.ticket");
  assert.equal(contract.rows[0].bindingExpression, "${{trigger-1.ticket}}");
  assert.equal(contract.rows[0].typeLabel, "object");
  assert.deepEqual(contract.rows[0].details, ["properties: issue_number, labels"]);
  assert.equal(contract.rows[2].typeLabel, "array<object>");
  assert.equal(contract.rows[3].typeLabel, "object");
  assert.equal(contract.rows[4].typeLabel, "string");
});

test("builds a selected-node output contract for pass-through and merge gates", () => {
  const config = createWorkflowConfig();
  const outputSchemas = deriveWorkflowOutputSchemas(config);

  const filterContract = selectedNodeOutputContract(config, "gate-filter-1", outputSchemas);
  assert.equal(filterContract.state, "derived");
  assert.equal(filterContract.note, "Pass-through gate: outputs are derived from trigger-1.");
  assert.deepEqual(filterContract.sourceNodeIds, ["trigger-1"]);
  assert.equal(filterContract.rows[1].bindingPath, "gate-filter-1.ticket.issue_number");
  assert.equal(filterContract.rows[1].bindingExpression, "${{gate-filter-1.ticket.issue_number}}");

  const debounceContract = selectedNodeOutputContract(config, "gate-debounce-1", outputSchemas);
  assert.equal(debounceContract.state, "derived");
  assert.equal(debounceContract.note, "Pass-through gate: outputs are derived from gate-filter-1.");

  const switchContract = selectedNodeOutputContract(config, "gate-switch-1", outputSchemas);
  assert.equal(switchContract.state, "derived");
  assert.equal(switchContract.note, "Pass-through gate: outputs are derived from gate-debounce-1.");
  assert.deepEqual(switchContract.sourceNodeIds, ["gate-debounce-1"]);

  const mergeContract = selectedNodeOutputContract(config, "gate-merge-1", outputSchemas);
  assert.equal(mergeContract.state, "empty");
  assert.equal(mergeContract.note, "Merge gate: no common upstream outputs are known.");
  assert.deepEqual(mergeContract.rows, []);
});

test("builds a selected-node output contract for map templates", () => {
  const config = createWorkflowConfig();
  const contract = selectedNodeOutputContract(config, "gate-map-1", deriveWorkflowOutputSchemas(config));

  assert.equal(contract.state, "derived");
  assert.equal(contract.note, "Map gate: outputs are derived from the map template.");
  assert.deepEqual(contract.rows.map((row) => [row.path, row.typeLabel]), [
    ["summary", "string"],
    ["attempts", "integer"],
    ["enabled", "boolean"],
    ["payload", "array"],
  ]);
});

test("returns empty contract states for missing or undeclared outputs", () => {
  const config = {
    triggers: [
      {
        id: "trigger-1",
        display_name: "Incoming ticket",
        type: "trigger:manual",
      },
    ],
    gates: [],
    effects: [],
    queries: [],
    edges: [],
  };

  const missingNodeContract = selectedNodeOutputContract(config, "missing", {});
  assert.equal(missingNodeContract.state, "unknown");
  assert.equal(missingNodeContract.note, "Selected node is not known.");

  const emptyContract = selectedNodeOutputContract(config, "trigger-1", {});
  assert.equal(emptyContract.state, "empty");
  assert.equal(emptyContract.note, "No known outputs are declared for this node.");
  assert.deepEqual(emptyContract.rows, []);
});

test("preserves binding option ordering for downstream fields", () => {
  const config = createWorkflowConfig();
  const outputSchemas = deriveWorkflowOutputSchemas(config);
  const options = upstreamBindingOptions(config, "effect-1", outputSchemas);

  assert.equal(options[0].label, "[System] now.utc_timestamp");
  assert.ok(options.some((option) => option.value === "${{gate-filter-1.ticket.issue_number}}"));
  assert.ok(options.some((option) => option.value === "${{trigger-1.ticket.issue_number}}"));
  assert.ok(options.some((option) => option.value === "${{gate-debounce-1.ticket.issue_number}}"));
  assert.ok(options.findIndex((option) => option.value === "${{gate-filter-1.ticket.issue_number}}") < options.findIndex((option) => option.value === "${{trigger-1.ticket.issue_number}}"));
  assert.ok(options.findIndex((option) => option.value === "${{gate-debounce-1.ticket.issue_number}}") < options.findIndex((option) => option.value === "${{trigger-1.ticket.issue_number}}"));
});

test("suggests only guaranteed upstream bindings after branch merge", () => {
  const config = {
    triggers: [
      {
        id: "trigger-1",
        display_name: "Trigger",
        type: "trigger:manual",
        outputs: { ticket: { type: "object" } },
      },
    ],
    gates: [
      {
        id: "gate-merge",
        display_name: "Merge",
        type: "gate:merge",
        outputs: { shared: { type: "string" } },
      },
    ],
    effects: [
      {
        id: "effect-pre",
        display_name: "Pre",
        type: "effect:agent:prompt",
        outputs: { session_id: { type: "string" } },
      },
      {
        id: "effect-a",
        display_name: "A",
        type: "effect:agent:prompt",
        outputs: { branch_value: { type: "string" }, shared: { type: "string" } },
      },
      {
        id: "effect-b",
        display_name: "B",
        type: "effect:agent:prompt",
        outputs: { branch_value: { type: "string" }, shared: { type: "string" } },
      },
      {
        id: "effect-after",
        display_name: "After",
        type: "effect:agent:prompt",
      },
    ],
    queries: [],
    edges: [
      { source_id: "trigger-1", target_id: "effect-pre" },
      { source_id: "effect-pre", target_id: "effect-a" },
      { source_id: "effect-pre", target_id: "effect-b" },
      { source_id: "effect-a", target_id: "gate-merge" },
      { source_id: "effect-b", target_id: "gate-merge" },
      { source_id: "gate-merge", target_id: "effect-after" },
    ],
  };
  const outputSchemas = deriveWorkflowOutputSchemas(config);
  const options = upstreamBindingOptions(config, "effect-after", outputSchemas);

  assert.deepEqual(guaranteedUpstreamNodeIds(config, "effect-after"), [
    "gate-merge",
    "effect-pre",
    "trigger-1",
  ]);
  assert.ok(options.some((option) => option.value === "${{effect-pre.session_id}}"));
  assert.ok(options.some((option) => option.value === "${{gate-merge.shared}}"));
  assert.ok(!options.some((option) => option.value === "${{effect-a.branch_value}}"));
  assert.ok(!options.some((option) => option.value === "${{effect-b.branch_value}}"));
});

test("declared outputs fall back to node outputs when no derived schema exists", () => {
  const node = {
    id: "trigger-1",
    display_name: "Incoming ticket",
    type: "trigger:manual",
    outputs: {
      ticket: { type: "object" },
    },
  };

  assert.deepEqual(declaredOutputsForNode(node, {}), {
    ticket: { type: "object" },
  });
});

test("state node output schemas expose graph-local bindings", () => {
  const config = {
    triggers: [
      {
        id: "trigger-1",
        display_name: "State trigger",
        type: "trigger:manual",
      },
    ],
    gates: [],
    effects: [
      {
        id: "effect-state-update",
        display_name: "Update workflow state",
        type: "effect:state:update",
        namespace: "workflow",
        key: "cursor",
        operation: "set",
        value: { cursor: "abc-123" },
      },
    ],
    queries: [
      {
        id: "query-state-get",
        display_name: "Read workflow state",
        type: "query:state:get",
        namespace: "workflow",
        key: "cursor",
      },
    ],
    edges: [
      { source_id: "trigger-1", target_id: "effect-state-update" },
      { source_id: "effect-state-update", target_id: "query-state-get" },
    ],
  };
  const backendOutputSchemas = {
    "effect-state-update": {
      success: { type: "boolean" },
      found: { type: "boolean" },
      directory_path: { type: "string" },
      graph_id: { type: "string" },
      namespace: { type: "string" },
      key: { type: "string" },
      value: {
        type: "object",
        properties: {
          cursor: { type: "string" },
        },
      },
      version: { type: "integer" },
      created_at: { type: "string" },
      updated_at: { type: "string" },
    },
    "query-state-get": {
      success: { type: "boolean" },
      found: { type: "boolean" },
      directory_path: { type: "string" },
      graph_id: { type: "string" },
      namespace: { type: "string" },
      key: { type: "string" },
      value: {
        type: "object",
        properties: {
          cursor: { type: "string" },
        },
      },
      version: { type: "integer" },
      created_at: { type: "string" },
      updated_at: { type: "string" },
    },
  };
  const outputSchemas = deriveWorkflowOutputSchemas(config, backendOutputSchemas);

  const updateContract = selectedNodeOutputContract(config, "effect-state-update", outputSchemas);
  assert.equal(updateContract.state, "declared");
  assert.ok(updateContract.rows.some((row) => row.path === "graph_id"));
  assert.ok(updateContract.rows.some((row) => row.path === "value"));
  assert.ok(updateContract.rows.some((row) => row.path === "value.cursor"));
  assert.ok(updateContract.rows.some((row) => row.path === "version"));

  const bindingOptions = upstreamBindingOptions(config, "query-state-get", outputSchemas);
  assert.ok(bindingOptions.some((option) => option.value === "${{effect-state-update.graph_id}}"));
  assert.ok(bindingOptions.some((option) => option.value === "${{effect-state-update.value}}"));
  assert.ok(
    bindingOptions.some((option) => option.value === "${{effect-state-update.value.cursor}}"),
  );
  assert.ok(bindingOptions.some((option) => option.value === "${{effect-state-update.version}}"));
});
