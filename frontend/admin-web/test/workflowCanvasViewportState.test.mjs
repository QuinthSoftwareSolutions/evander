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

  assert.match(
    applyBranch,
    /options: \{ autoFit\?: boolean; markClean\?: boolean; nodeEditOrigins\?: Record<string, string> \} = \{\}/,
  );
  assert.match(applyBranch, /if \(options\.autoFit\) \{\s+hasAutoFitRef\.current = false;\s+\}/);
  assert.doesNotMatch(patchBranch, /autoFit: true/);
  assert.doesNotMatch(saveBranch, /autoFit: true/);
});

test("catalog click-created workflow nodes use the current canvas center", () => {
  const addNodeBranch = sourceBetween(
    "function addNode(",
    "function deleteSelectedNode()",
  );
  const createListBranch = sourceBetween(
    'className="create-group-actions create-subgroup-actions"',
    '<div className="sidebar-section">',
  );

  assert.match(addNodeBranch, /requestedPosition\) \?\? nodePositionAtCanvasCenter\(\) \?\? fallbackPosition/);
  assert.match(createListBranch, /onClick=\{\(\) => addNode\(entry\.kind, entry\.type\)\}/);
});
