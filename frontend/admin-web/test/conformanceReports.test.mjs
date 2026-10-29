import assert from "node:assert/strict";
import test from "node:test";

import {
  summarizeRequirementCounts,
} from "../src/features/reports/conformanceReports.ts";

test("requirement count summaries are stable and concise", () => {
  const payload = {
    pass: { pass: 5, unknown: 1 },
    fail: { fail: 1, missing: 2, stale: 1 },
  };

  assert.equal(
    summarizeRequirementCounts(payload.pass),
    "Pass 5, Unknown 1",
  );
  assert.equal(
    summarizeRequirementCounts(payload.fail),
    "Fail 1, Missing 2, Stale 1",
  );
});
