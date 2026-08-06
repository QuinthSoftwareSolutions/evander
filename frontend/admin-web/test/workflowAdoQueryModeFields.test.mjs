import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const app = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");

test("ADO query mode hides inactive inspector fields", () => {
  assert.match(app, /function adoQueryModeHidesField\(/);
  assert.match(app, /function pruneAdoQueryModeHiddenFieldValues\(/);
  assert.doesNotMatch(app, /adoQueryModeDisablesField/);
  assert.match(
    app,
    /if \(adoQueryModeHidesField\(selectedNodeType, field\.key, inspectorKnownValues\)\) \{\s*return null;\s*\}/,
  );
  assert.match(
    app,
    /if \(isAdoQueryModeNodeType\(selectedNode\.type\)\) \{\s*nextKnownValues = pruneAdoQueryModeHiddenFieldValues\(selectedNode\.type, nextKnownValues\);/,
  );
  assert.match(
    app,
    /for \(const fieldKey of ADO_STRUCTURED_FILTER_FIELD_KEYS\) \{\s*delete nextStringListDrafts\[fieldKey\];/,
  );
});
