import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");

test("workflow editor exposes workflow pack selection and sends it on save", () => {
  assert.match(source, /function workflowEditorDocumentPath\(workflowPack/);
  assert.match(source, /params\.set\("workflow_pack", trimmedWorkflowPack\)/);
  assert.match(source, /function renderWorkflowPackIdentity\(\)/);
  assert.match(source, /function handleWorkflowPackChange/);
  assert.match(source, /workflow_pack: selectedWorkflowPackReference \|\| undefined/);
});

test("workflow editor uses selected workflow pack when generating node ids", () => {
  assert.match(source, /function workflowPackSlugFromReference\(packReference: unknown\)/);
  assert.match(
    source,
    /function selectedWorkflowPackSlug\(\s*config: WorkflowConfig,\s*selectedWorkflowPackReference\?: string \| null,/,
  );
  assert.match(
    source,
    /const selectedPackSlug = workflowPackSlugFromReference\(selectedWorkflowPackReference\);/,
  );
  assert.match(
    source,
    /generateNodeId\(\s*nextConfig,\s*templateKind,\s*templateNode\.type,\s*templateNode\.display_name,\s*selectedWorkflowPackReference,\s*\)/,
  );
  assert.match(
    source,
    /generateNodeId\(nextConfig, kind, type, catalogEntry\?\.label, selectedWorkflowPackReference\)/,
  );
});
