import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const app = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");

function sourceBetween(startText, endText) {
  const start = app.indexOf(startText);
  assert.notEqual(start, -1, `missing start marker: ${startText}`);
  const end = app.indexOf(endText, start);
  assert.notEqual(end, -1, `missing end marker: ${endText}`);
  return app.slice(start, end);
}

test("regular workflow document patches preserve the current canvas viewport", () => {
  const applyBranch = sourceBetween(
    "function applyWorkflowDocumentState(",
    "function patchDocument(",
  );
  const patchBranch = sourceBetween(
    "function patchDocument(",
    "function patchWorkflowDocument(",
  );
  const saveBranch = sourceBetween(
    "async function saveDocument()",
    "function updateSelectedTriggerEnabled(",
  );

  assert.match(applyBranch, /options: \{ autoFit\?: boolean \} = \{\}/);
  assert.match(applyBranch, /if \(options\.autoFit\) \{\s+hasAutoFitRef\.current = false;\s+\}/);
  assert.doesNotMatch(patchBranch, /autoFit: true/);
  assert.doesNotMatch(saveBranch, /autoFit: true/);
});
