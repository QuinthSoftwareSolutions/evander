import assert from "node:assert/strict";
import test from "node:test";

import {
  WORKFLOW_TUTORIAL_CATALOG,
  WORKFLOW_TUTORIAL_FIRST_STEPS,
  WORKFLOW_TUTORIAL_FIRST_STEPS_SEED_DOCUMENT,
  cloneWorkflowTutorialDocument,
  createWorkflowTutorialSession,
  workflowTutorialDefinitionById,
  workflowTutorialStepById,
  workflowTutorialStepIndex,
  workflowTutorialTargetNodeId,
} from "../src/workflowTutorials.ts";

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
