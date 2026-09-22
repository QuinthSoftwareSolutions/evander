import { readWorkflowEditorSource, readWorkflowEditorFunction } from "./workflowEditorSource.mjs";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const app = readWorkflowEditorSource();

function sourceBetween(startText, endText) {
  const functionName = /^function ([A-Za-z0-9_]+)/.exec(startText)?.[1];
  if (functionName) {
    const implementation = readWorkflowEditorFunction(functionName);
    return functionName === "workflowTutorialIsAtFinalStep"
      ? implementation + readWorkflowEditorFunction("markActiveWorkflowTutorialCompletedIfFinished")
      : implementation;
  }
  const start = app.indexOf(startText);
  assert.notEqual(start, -1, `missing start marker: ${startText}`);
  const end = app.indexOf(endText, start);
  assert.notEqual(end, -1, `missing end marker: ${endText}`);
  return app.slice(start, end);
}

test("tutorial edits do not replace the shared workflow graph state", () => {
  const tutorialPatchBranch = sourceBetween(
    "if (isTutorialMode && activeWorkflowTutorial) {",
    "patchDocument(",
  );

  assert.match(tutorialPatchBranch, /setActiveWorkflowTutorial/);
  assert.doesNotMatch(tutorialPatchBranch, /setFlowNodes/);
  assert.doesNotMatch(tutorialPatchBranch, /setFlowEdges/);
});

test("tutorial progress uses account preferences and the chooser can skip or restart without auto-starting", () => {
  const progressHelpers = readFileSync(
    new URL("../src/features/workflow-editor/tutorialModel.ts", import.meta.url),
    "utf8",
  );
  const chooserBranch = sourceBetween(
    "function renderWorkflowTutorialChooser()",
    "function renderWorkflowTutorialLauncher()",
  );

  assert.match(progressHelpers, /workflowTutorialProgressApiPayload/);
  assert.match(progressHelpers, /normalizeWorkflowTutorialProgress/);
  assert.match(progressHelpers, /workflowTutorialProgressIds/);
  assert.match(app, /fetchJson<unknown>\(WORKFLOW_TUTORIAL_PROGRESS_API_PATH\)/);
  assert.match(app, /method: "PUT"/);
  assert.doesNotMatch(app, /workflow-editor:tutorial-progress/);
  assert.match(app, /const \[workflowTutorialChooserOpen, setWorkflowTutorialChooserOpen\]/);
  assert.match(app, /const hasUnfinishedUnskippedTutorials =/);
  assert.match(app, /setWorkflowTutorialChooserOpen\(true\)/);
  assert.match(app, /updateWorkflowTutorialProgressStatus\(definition\.id, "skipped"\)/);
  assert.match(chooserBranch, /startWorkflowTutorial\(definition\.id\)/);
  assert.match(chooserBranch, /restartWorkflowTutorial\(definition\.id\)/);
  assert.match(chooserBranch, /skipWorkflowTutorial\(definition\.id\)/);
  assert.match(chooserBranch, /clearWorkflowTutorialProgressStatus\(definition\.id\)/);
});

test("tutorial final reset and exit persist completion without changing skip", () => {
  const completionHelpers = sourceBetween(
    "function workflowTutorialIsAtFinalStep(",
    "function advanceWorkflowTutorial()",
  );
  const resetBranch = sourceBetween(
    "function resetWorkflowTutorial()",
    "function exitWorkflowTutorial(",
  );
  const exitBranch = sourceBetween(
    "function exitWorkflowTutorial(",
    "function exitInspectMode()",
  );
  const skipBranch = sourceBetween(
    "function skipWorkflowTutorial(",
    "function workflowTutorialChooserStatusFor(",
  );

  assert.match(completionHelpers, /definition\.steps\.length - 1/);
  assert.match(completionHelpers, /updateWorkflowTutorialProgressStatus\(activeWorkflowTutorial\.tutorialId, "completed"\)/);
  assert.match(resetBranch, /markActiveWorkflowTutorialCompletedIfFinished\(\)/);
  assert.match(exitBranch, /markActiveWorkflowTutorialCompletedIfFinished\(\)/);
  assert.match(exitBranch, /markCompleted\?: boolean/);
  assert.match(skipBranch, /exitWorkflowTutorial\(\{ markCompleted: false \}\)/);
});

test("tutorial connect decoration keeps both source and target nodes active", () => {
  const decorateBranch = sourceBetween(
    "function decorateWorkflowTutorialNodes(",
    "function tutorialRequiredActionLabel(",
  );

  assert.match(decorateBranch, /connectSourceNodeId/);
  assert.match(decorateBranch, /connectTargetNodeId/);
  assert.match(decorateBranch, /isConnectParticipant/);
  assert.match(decorateBranch, /tutorialBlocked: targetNodeId !== null \? !\(isTarget \|\| isConnectParticipant\) : false,/);
  assert.match(decorateBranch, /visibility: "visible"/);
});

test("tutorial connect route checks stay exact on the right-click path", () => {
  const connectionBranch = sourceBetween(
    "function startPendingConnection(route: WorkflowConnectionRouteOption) {",
    "function updatePendingConnectionCursor(clientX: number, clientY: number)",
  );
  const contextMenuBranch = sourceBetween(
    "function handleNodeContextMenu(event: React.MouseEvent, node: Node<GraphNodeData>) {",
    "function handleWorkflowNodeClick(event: React.MouseEvent, node: Node<GraphNodeData>) {",
  );

  assert.match(connectionBranch, /expectedAction\.sourceNodeId === nodeConnectionMenu\.nodeId/);
  assert.match(connectionBranch, /expectedAction\.sourcePort === route\.sourcePort/);
  assert.match(contextMenuBranch, /expectedAction\?\.kind === "connect"/);
  assert.match(contextMenuBranch, /expectedAction\.sourceNodeId === node\.id/);
});

test("tutorial connection completion selects the target before bind-field steps", () => {
  const pendingConnectionBranch = sourceBetween(
    "if (pendingConnection) {",
    "setWorkflowTutorialHint(tutorialActionHintFor(expectedAction));",
  );

  assert.match(pendingConnectionBranch, /setProgrammaticSelection\(\[node\.id\], node\.id\);/);
  assert.match(pendingConnectionBranch, /completeTutorialAction\(connectAction\);/);
  assert.ok(
    pendingConnectionBranch.indexOf("setProgrammaticSelection([node.id], node.id);") <
      pendingConnectionBranch.indexOf("completeTutorialAction(connectAction);"),
  );
});

test("tutorial camera effect depends on a stable target key", () => {
  const cameraStart = app.indexOf('if (!isTutorialMode || !activeWorkflowTutorialStep || !flowInstance) return;');
  assert.notEqual(cameraStart, -1, "missing tutorial camera effect");
  const dependencyStart = app.indexOf("}, [activeWorkflowTutorialStep?.camera", cameraStart);
  assert.notEqual(dependencyStart, -1, "missing tutorial camera dependency list");
  const dependencyEnd = app.indexOf(");", dependencyStart);
  assert.notEqual(dependencyEnd, -1, "missing tutorial camera effect close");
  const cameraEffect = app.slice(cameraStart, dependencyEnd);
  const cameraDependencies = app.slice(dependencyStart, dependencyEnd);

  assert.match(app, /const tutorialCameraTargetKey =/);
  assert.match(cameraEffect, /tutorialCameraTargetNode/);
  assert.match(cameraDependencies, /tutorialCameraTargetKey/);
  assert.doesNotMatch(cameraDependencies, /displayedFlowNodes/);
});
