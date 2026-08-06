import assert from "node:assert/strict";
import test from "node:test";

import {
  WORKFLOW_TUTORIAL_CATALOG,
  WORKFLOW_TUTORIAL_DEFINITIONS,
  WORKFLOW_TUTORIAL_FIRST_STEPS,
  WORKFLOW_TUTORIAL_FIRST_STEPS_DOCUMENT,
  WORKFLOW_TUTORIAL_FIRST_STEPS_SEED_DOCUMENT,
  cloneWorkflowTutorialDocument,
  createWorkflowTutorialSession,
  workflowTutorialDefinitionById,
  workflowTutorialStepById,
  workflowTutorialStepIndex,
  workflowTutorialTargetNodeId,
} from "../src/workflowTutorials.ts";

test("first steps tutorial is registered with the expected walkthrough order", () => {
  assert.equal(WORKFLOW_TUTORIAL_DEFINITIONS.length, 1);
  assert.equal(WORKFLOW_TUTORIAL_FIRST_STEPS.id, "first-steps");
  assert.equal(WORKFLOW_TUTORIAL_FIRST_STEPS.title, "First steps");
  assert.deepEqual(
    WORKFLOW_TUTORIAL_FIRST_STEPS.steps.map((step) => step.id),
    [
      "welcome",
      "select-trigger",
      "explain-trigger",
      "add-effect",
      "connect-nodes",
      "bind-prompt",
      "finish",
    ],
  );
  assert.equal(WORKFLOW_TUTORIAL_FIRST_STEPS.steps[0].requiredAction.kind, "next");
  assert.equal(WORKFLOW_TUTORIAL_FIRST_STEPS.steps[1].requiredAction.kind, "select-node");
  assert.match(WORKFLOW_TUTORIAL_FIRST_STEPS.steps[1].body, /The trigger starts the workflow\./);
  assert.equal(WORKFLOW_TUTORIAL_FIRST_STEPS.steps[3].requiredAction.kind, "add-node");
  assert.match(WORKFLOW_TUTORIAL_FIRST_STEPS.steps[3].body, /Effects run downstream work\./);
  assert.equal(WORKFLOW_TUTORIAL_FIRST_STEPS.steps[4].requiredAction.kind, "connect");
  assert.equal(WORKFLOW_TUTORIAL_FIRST_STEPS.steps[4].target.nodeId, "tutorial-trigger");
  assert.match(WORKFLOW_TUTORIAL_FIRST_STEPS.steps[4].body, /Right-click the trigger, choose Completed, then click Prompt agent/);
  assert.equal(WORKFLOW_TUTORIAL_FIRST_STEPS.steps[4].bubblePlacement, "bottom");
  assert.equal(WORKFLOW_TUTORIAL_FIRST_STEPS.steps[5].requiredAction.kind, "bind-field");
  assert.match(WORKFLOW_TUTORIAL_FIRST_STEPS.steps[5].body, /Bind the Prompt field to the trigger's Completed output/);
  assert.equal(WORKFLOW_TUTORIAL_FIRST_STEPS.steps[6].requiredAction.kind, "reset");
});

test("tutorial definition exposes a seed document and a completed reference document", () => {
  assert.deepEqual(
    WORKFLOW_TUTORIAL_FIRST_STEPS_SEED_DOCUMENT.config.triggers.map((node) => node.id),
    ["tutorial-trigger"],
  );
  assert.deepEqual(
    WORKFLOW_TUTORIAL_FIRST_STEPS_DOCUMENT.config.effects.map((node) => node.id),
    ["tutorial-effect"],
  );
  assert.deepEqual(WORKFLOW_TUTORIAL_FIRST_STEPS_SEED_DOCUMENT.graphs[0].node_ids, ["tutorial-trigger"]);
  assert.deepEqual(WORKFLOW_TUTORIAL_FIRST_STEPS_DOCUMENT.graphs[0].node_ids, [
    "tutorial-trigger",
    "tutorial-effect",
  ]);
  assert.equal(WORKFLOW_TUTORIAL_FIRST_STEPS_DOCUMENT.config.edges[0].source_port, "completed");
  assert.equal(
    WORKFLOW_TUTORIAL_FIRST_STEPS_DOCUMENT.config.effects[0].prompt,
    "${{tutorial-trigger.completed}}",
  );
  assert.equal(WORKFLOW_TUTORIAL_FIRST_STEPS_DOCUMENT.output_schemas?.["tutorial-trigger"]?.completed?.type, "string");
});

test("tutorial helpers provide lookup and cloning utilities", () => {
  const definition = workflowTutorialDefinitionById("first-steps");
  assert.equal(definition?.id, "first-steps");
  assert.equal(workflowTutorialStepIndex(WORKFLOW_TUTORIAL_FIRST_STEPS, "bind-prompt"), 5);
  assert.equal(workflowTutorialStepById(WORKFLOW_TUTORIAL_FIRST_STEPS, "connect-nodes")?.camera, "fit-node");
  assert.equal(
    workflowTutorialTargetNodeId(WORKFLOW_TUTORIAL_FIRST_STEPS.steps[0].target),
    null,
  );
  assert.equal(
    workflowTutorialTargetNodeId(WORKFLOW_TUTORIAL_FIRST_STEPS.steps[1].target),
    "tutorial-trigger",
  );

  const clonedDocument = cloneWorkflowTutorialDocument(WORKFLOW_TUTORIAL_FIRST_STEPS_SEED_DOCUMENT);
  assert.notStrictEqual(clonedDocument, WORKFLOW_TUTORIAL_FIRST_STEPS_SEED_DOCUMENT);
  clonedDocument.config.triggers[0].display_name = "Changed";
  assert.equal(WORKFLOW_TUTORIAL_FIRST_STEPS_SEED_DOCUMENT.config.triggers[0].display_name, "Agent run completed");

  const session = createWorkflowTutorialSession();
  assert.equal(session.tutorialId, "first-steps");
  assert.equal(session.stepIndex, 0);
  assert.notStrictEqual(session.document, WORKFLOW_TUTORIAL_FIRST_STEPS_SEED_DOCUMENT);
  assert.deepEqual(session.document.config.triggers.map((node) => node.id), ["tutorial-trigger"]);
  assert.equal(WORKFLOW_TUTORIAL_CATALOG.nodes.length, 2);
});
