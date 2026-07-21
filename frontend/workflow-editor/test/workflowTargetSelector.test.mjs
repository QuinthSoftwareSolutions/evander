import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const app = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");

test("collapses target selector triplets while preserving session and VM disambiguation", () => {
  const targetSelectorSet = app.match(/const TARGET_SELECTOR_NODE_TYPES = new Set\(\[([\s\S]*?)\]\);/)?.[1] ?? "";
  for (const nodeType of [
    "effect:vm:clone-repo",
    "effect:vm:bootstrap-python",
    "effect:vm:bootstrap-frontend",
    "effect:vm:start",
    "effect:vm:stop",
    "effect:vm:exec",
    "effect:vm:copy-files",
    "effect:vm:delete-files",
    "effect:agent:destroy",
    "effect:agent:clone-repo",
    "effect:agent:bootstrap-frontend",
    "effect:agent:copy-files",
    "effect:agent:prompt",
    "effect:agent:enqueue-prompt",
    "effect:agent:exec",
    "effect:artifact:stage",
    "effect:artifact:publish-artifacts",
    "effect:artifact:import-published",
    "effect:session:update-metadata",
    "effect:session:approve-mcp-tools-for-run",
    "effect:session:renew-if-expired",
    "query:session-context",
  ]) {
    assert.match(targetSelectorSet, new RegExp(`"${nodeType}"`));
  }

  assert.match(app, /const hasCollapsedTargetUi = TARGET_SELECTOR_NODE_TYPES\.has\(selectedNode\?\.type \?\? ""\);/);
  assert.match(app, /const \[inspectorEffectTargetKey, setInspectorEffectTargetKey\] = useState<EffectTargetKey>\("target"\);/);
  assert.match(app, /const effectTargetKey = hasCollapsedTargetUi \? inspectorEffectTargetKey : activeEffectTargetKey\(inspectorKnownValues\);/);
  assert.match(app, /effectTargetKey: nextDraft\.effectTargetKey \?\? inspectorEffectTargetKey,/);
  assert.match(app, /stageInspectorDraft\(\{ knownValues: next, effectTargetKey: nextKey \}\);/);
  assert.match(app, /<option value="target">Generic target<\/option>/);
  assert.match(app, /<option value="session_id">Session ID<\/option>/);
  assert.match(app, /<option value="vm_name">VM name<\/option>/);
  assert.match(app, /fetchJson<WorkflowTargetInventory>\(workflowTargetInventoryPath\(directoryPath\)\)/);
  assert.match(app, /if \(!hasCollapsedTargetUi \|\| isTutorialMode\) return;/);
  assert.match(app, /list=\{optionListId\}/);
  assert.match(app, /<datalist id=\{optionListId\}>/);
  assert.match(app, /value=\{option\.value\} label=\{option\.label\}/);
});
