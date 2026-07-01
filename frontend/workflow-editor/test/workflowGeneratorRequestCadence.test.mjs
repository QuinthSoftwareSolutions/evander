import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const app = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");

test("workflow generator model requests require an explicit action", () => {
  const generatorFetchIndex = app.indexOf('"/api/workflow-editor/generate-draft"');
  const descriptionHandlerIndex = app.indexOf("function handleWorkflowGeneratorDescriptionChange");
  const generatorActionIndex = app.indexOf("async function generateWorkflowGeneratorDraft");
  assert.notEqual(generatorFetchIndex, -1);
  assert.notEqual(descriptionHandlerIndex, -1);
  assert.notEqual(generatorActionIndex, -1);
  const generatorRequestSource = app.slice(
    Math.max(0, generatorFetchIndex - 1200),
    generatorFetchIndex + 300,
  );
  const descriptionHandlerSource = app.slice(descriptionHandlerIndex, generatorActionIndex);

  assert.match(app, /async function generateWorkflowGeneratorDraft\(\)/);
  assert.match(app, /onClick=\{generateWorkflowGeneratorDraft\}/);
  assert.match(app, /function renderWorkflowGeneratorDialog\(\)/);
  assert.match(app, /function renderWorkflowGeneratorCanvasControl\(\)/);
  assert.match(app, /className="workflow-generator-canvas-control"/);
  assert.match(app, /aria-label="Generate workflow prompt"/);
  assert.match(app, /aria-haspopup="dialog"/);
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
