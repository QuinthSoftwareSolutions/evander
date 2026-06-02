import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

test("admin save requests include expected revisions", () => {
  const app = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
  const workers = readFileSync(
    new URL("../src/features/workers/WorkersScreen.tsx", import.meta.url),
    "utf8",
  );
  const configuration = readFileSync(
    new URL("../src/features/configuration/ConfigurationScreen.tsx", import.meta.url),
    "utf8",
  );

  assert.match(app, /const expectedRevision = savedDocument\?\.revision \?\? document\.revision/);
  assert.match(app, /expected_revision:\s*expectedRevision/);
  assert.match(app, /setSavedDocument\(normalizedSavedDocument\)/);
  assert.match(workers, /expected_revision:\s*detail\?\.config\.revision/);
  assert.match(configuration, /expected_revision:\s*snapshot\.revision/);
  assert.match(configuration, /removed_fields:\s*removedFields/);
  assert.match(configuration, /removedFields\.push\(field\.field_name\)/);
});

test("admin fetch helper preserves conflict status", () => {
  const api = readFileSync(
    new URL("../src/features/admin/api.ts", import.meta.url),
    "utf8",
  );

  assert.match(api, /class ApiError extends Error/);
  assert.match(api, /this\.status = status/);
});
