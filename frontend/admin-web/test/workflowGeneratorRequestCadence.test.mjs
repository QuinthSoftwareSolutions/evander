import { readWorkflowEditorSource, readWorkflowEditorFunction } from "./workflowEditorSource.mjs";
import assert from "node:assert/strict";
import { test } from "node:test";

const app = readWorkflowEditorSource();

test("workflow generator model requests require an explicit action", () => {
  const generatorRequestSource = readWorkflowEditorFunction("generateWorkflowGeneratorDraft");
  const descriptionHandlerSource = readWorkflowEditorFunction("handleWorkflowGeneratorDescriptionChange");

  assert.match(app, /async function generateWorkflowGeneratorDraft\(\)/);
  assert.match(app, /onClick=\{generateWorkflowGeneratorDraft\}/);
  assert.match(app, /function renderWorkflowGeneratorCanvasPanel\(\)/);
  assert.match(app, /function renderWorkflowGeneratorCanvasControl\(\)/);
  assert.match(app, /className="workflow-generator-canvas-control"/);
  assert.match(app, /className="workflow-generator-canvas-panel"/);
  assert.match(app, /role="dialog" aria-modal="false"/);
  assert.match(app, /aria-label="Generate workflow"/);
  assert.match(app, /aria-haspopup="dialog"/);
  assert.doesNotMatch(readWorkflowEditorFunction("renderWorkflowGeneratorCanvasPanel"), /modal-backdrop/);
  assert.match(app, /const workflowMutationLocked = workflowEditLocked \|\| workflowGeneratorOpen;/);
  assert.match(app, /nodesDraggable=\{!workflowMutationLocked/);
  assert.match(app, /nodesConnectable=\{!workflowMutationLocked\}/);
  assert.match(app, /elementsSelectable=\{!workflowEditLocked\}/);
  assert.match(app, /deleteKeyCode=\{workflowMutationLocked \? null : \["Backspace", "Delete"\]\}/);
  assert.match(app, /WORKFLOW_GENERATOR_MIN_DESCRIPTION_LENGTH\s*=\s*24/);
  assert.doesNotMatch(app, /WORKFLOW_GENERATOR_DEBOUNCE_MS/);
  assert.match(generatorRequestSource, /async function generateWorkflowGeneratorDraft\(\)/);
  assert.doesNotMatch(generatorRequestSource, /useEffect\(/);
  assert.doesNotMatch(generatorRequestSource, /setTimeout\(/);
  assert.doesNotMatch(descriptionHandlerSource, /fetchJson/);
});

test("workflow generator accepts only drafts for the current description", () => {
  assert.match(
    app,
    /workflowGeneratorReadyDescription\s*!==\s*workflowGeneratorDescription\.trim\(\)/,
  );
  assert.match(
    app,
    /workflowGeneratorReadyDescription\s*===\s*generatorDescription/,
  );
});
