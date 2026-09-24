import assert from "node:assert/strict";
import test from "node:test";
import {classifierSwitchConfig, classifierSwitchProblems} from "../src/features/classification/classifierSwitchDraft.ts";
import {workflowOutputPortsForNode, workflowOutputHandleIds} from "../src/workflowRoutes.ts";
import {deriveWorkflowOutputSchemas} from "../src/workflowNodeOutputs.ts";

const config = {state: "${{incoming.message}}", instructions: "Select a route", cases: [{port: "terra", label: "Level 2", description: "Requests level 2"}], default_port: "standard"};

test("Smart switch draft isolates test configuration and validates routes and cutoffs", () => {
  assert.deepEqual(classifierSwitchProblems(config), []);
  assert.deepEqual(classifierSwitchConfig({...config, provider: "", model: undefined, min_probability: null, outputs: {message: {type: "string"}}}), config);
  for (const patch of [{cases: []}, {default_port: "terra"}, {default_port: "error"}, {min_probability: 2}, {min_probability: "0.8"}, {instructions: " "}, {cases: [{port: "terra", description: ""}]}]) {
    assert.ok(classifierSwitchProblems({...config, ...patch}).length);
  }
  assert.deepEqual(classifierSwitchProblems({...config, min_probability: 0}), []);
});

test("Smart switch exposes described route handles and inherits input bindings", () => {
  const node = {id: "smart", display_name: "Smart switch", type: "gate:classifier:switch", config};
  const ports = workflowOutputPortsForNode(node, []);
  assert.deepEqual(ports.map(p => p.key), ["terra", "standard"]);
  assert.equal(ports[0].description, "Requests level 2");
  assert.deepEqual(workflowOutputHandleIds(ports), ["terra", "standard", "error"]);
  const workflow = {triggers: [{id: "incoming", display_name: "Incoming", type: "trigger:manual", config: {}, outputs: {message: {type: "string"}}}], gates: [node], effects: [], queries: [], edges: [{source_id: "incoming", target_id: "smart"}]};
  assert.deepEqual(deriveWorkflowOutputSchemas(workflow).smart, {message: {type: "string"}});
});
