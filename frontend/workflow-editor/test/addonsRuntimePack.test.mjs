import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("../src/features/addons/AddonsPage.tsx", import.meta.url), "utf8");
const typesSource = readFileSync(new URL("../src/features/admin/types.ts", import.meta.url), "utf8");

test("add-ons page exposes runtime workflow pack status and refresh", () => {
  assert.match(typesSource, /export type AdminAddonRuntimeWorkflowPack = \{/);
  assert.match(typesSource, /runtime_workflow_pack\?: AdminAddonRuntimeWorkflowPack \| null;/);
  assert.match(source, /function refreshRuntimeWorkflowPack\(addon: AdminAddonStatus\)/);
  assert.match(source, /runtime-workflow-pack\/refresh/);
  assert.match(source, /expected_revision: state\.revision/);
  assert.match(source, /window\.confirm\(`Refresh \$\{packReference\}\?/);
  assert.match(source, /<h3>Runtime workflow pack<\/h3>/);
  assert.match(source, /Refresh runtime pack/);
});
