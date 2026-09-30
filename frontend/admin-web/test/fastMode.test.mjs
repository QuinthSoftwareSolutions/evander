import assert from "node:assert/strict";
import test from "node:test";

import { fastModeSelection, fastModeValue } from "../src/features/admin/fastMode.ts";

test("standard speed is an explicit false override rather than session inheritance", () => {
  assert.equal(fastModeValue("false"), false);
  assert.equal(fastModeValue("true"), true);
  assert.equal(fastModeValue(""), null);
  assert.equal(fastModeSelection(false), "false");
  assert.equal(fastModeSelection(true), "true");
  assert.equal(fastModeSelection(null), "");
  assert.throws(() => fastModeValue("standard"));
});
