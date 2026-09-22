import { readWorkflowEditorFunction } from "./workflowEditorSource.mjs";
import assert from "node:assert/strict";
import { test } from "node:test";

test("regular workflow document patches preserve the current canvas viewport", () => {
  const applyBranch = readWorkflowEditorFunction("applyWorkflowDocumentState");
  const patchBranch = readWorkflowEditorFunction("patchDocument");
  const saveBranch = readWorkflowEditorFunction("saveDocument");

  assert.match(
    applyBranch,
    /options: \{ autoFit\?: boolean; markClean\?: boolean; nodeEditOrigins\?: Record<string, string> \} = \{\}/,
  );
  assert.match(applyBranch, /if \(options\.autoFit\) \{\s+hasAutoFitRef\.current = false;\s+\}/);
  assert.doesNotMatch(patchBranch, /autoFit: true/);
  assert.doesNotMatch(saveBranch, /autoFit: true/);
});

test("catalog click-created workflow nodes use the current canvas center", () => {
  const addNodeBranch = readWorkflowEditorFunction("addNode");
  const createListBranch = readWorkflowEditorFunction("renderDesktopCreatePanel");

  assert.match(addNodeBranch, /requestedPosition\) \?\? nodePositionAtCanvasCenter\(\) \?\? fallbackPosition/);
  assert.match(createListBranch, /onClick=\{\(\) => addNode\(entry\.kind, entry\.type\)\}/);
});
