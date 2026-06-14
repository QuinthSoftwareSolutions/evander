import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const configuration = readFileSync(
  new URL("../src/features/configuration/ConfigurationScreen.tsx", import.meta.url),
  "utf8",
);
const workers = readFileSync(
  new URL("../src/features/workers/WorkersScreen.tsx", import.meta.url),
  "utf8",
);

test("configuration screen uses structured editors for string lists and json fields", () => {
  assert.match(configuration, /StringListEditor/);
  assert.match(configuration, /JsonTextEditor/);
  assert.match(configuration, /formatJsonTextDraftValue/);
  assert.match(configuration, /parseJsonTextDraftValue/);
  assert.match(configuration, /if \(field\.kind === "string_list"\)/);
  assert.match(configuration, /if \(field\.kind === "json"\)/);
  assert.match(configuration, /if \(field\.kind === "multiline_text"\)/);
  assert.doesNotMatch(
    configuration,
    /field\.kind === "multiline_text"\s*\|\|\s*field\.kind === "string_list"\s*\|\|\s*field\.kind === "json"/,
  );
});

test("workers screen uses structured editors for string lists and json fields", () => {
  assert.match(workers, /StringListEditor/);
  assert.match(workers, /JsonTextEditor/);
  assert.match(workers, /formatJsonTextDraftValue/);
  assert.match(workers, /parseJsonTextDraftValue/);
  assert.match(workers, /if \(field\.kind === "string_list"\)/);
  assert.match(workers, /if \(field\.kind === "json"\)/);
  assert.match(workers, /if \(field\.kind === "multiline_text"\)/);
  assert.doesNotMatch(
    workers,
    /field\.kind === "multiline_text"\s*\|\|\s*field\.kind === "string_list"\s*\|\|\s*field\.kind === "json"/,
  );
});
