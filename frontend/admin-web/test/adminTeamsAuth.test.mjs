import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const configuration = readFileSync(
  new URL("../src/features/configuration/ConfigurationScreen.tsx", import.meta.url),
  "utf8",
);
test("configuration screen does not render raw secret-bearing Teams auth fields", () => {
  assert.doesNotMatch(configuration, /device_code/i);
  assert.doesNotMatch(configuration, /store_path/i);
  assert.doesNotMatch(configuration, /access_token/i);
  assert.doesNotMatch(configuration, /refresh_token/i);
});
