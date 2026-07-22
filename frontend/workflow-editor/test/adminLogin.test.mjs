import assert from "node:assert/strict";
import fs from "node:fs/promises";
import test from "node:test";

const adminRootSource = await fs.readFile(new URL("../src/AdminRoot.tsx", import.meta.url), "utf8");

test("admin login username field starts empty", () => {
  assert.match(adminRootSource, /<input name="username" autoComplete="username" \/>/);
  assert.doesNotMatch(adminRootSource, /<input name="username" defaultValue=/);
});
