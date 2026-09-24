import { readWorkflowEditorSource } from "./workflowEditorSource.mjs";
import assert from "node:assert/strict";
import test from "node:test";

const app = readWorkflowEditorSource();

test("workflow run snapshot refreshes keep document load errors separate", () => {
  const runSnapshotEffect = app.match(
    /fetchJson<WorkflowRunSnapshot>\("\/api\/workflow-editor\/runs"\)([\s\S]*?)return \(\) => \{\s*cancelled = true;/,
  )?.[1] ?? "";

  assert.match(runSnapshotEffect, /applyWorkflowRunSnapshot\(runSnapshotPayload\);/);
  assert.match(runSnapshotEffect, /setRunSnapshotError\(null\);/);
  assert.match(runSnapshotEffect, /setRunSnapshotError\(reason instanceof Error \? reason\.message : String\(reason\)\);/);
  assert.doesNotMatch(runSnapshotEffect, /setError\(null\);/);
});

test("workflow run stream is gated by page visibility", () => {
  assert.match(app, /function currentPageVisible\(\): boolean/);
  assert.match(app, /globalThis\.document\.addEventListener\("visibilitychange", updateVisibility\)/);
  assert.match(
    app,
    /if \(workflowRunReadLocked \|\| !pageVisible\) \{[\s\S]*setRuntimePulseNodeIds\(\(current\) => current\.size === 0 \? current : new Set<string>\(\)\);/,
  );
  assert.match(app, /const stream = new EventSource\("\/api\/workflow-editor\/runs\/stream"\);/);
});
