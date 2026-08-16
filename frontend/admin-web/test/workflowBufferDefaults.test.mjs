import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const app = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");

test("buffer defaults allow users to clear optional timeout", () => {
  assert.match(
    app,
    /if \(kind === "gate" && type === "gate:buffer"\) \{\n\s+return workflowNode\(id, "Buffer", type, \{\n\s+key: "buffer:\$\{\{system\.execution\.trace_id\}\}",\n\s+count: 10,\n\s+timeout: "PT10M"/,
  );
  assert.match(
    app,
    /if \(typeof gate\.config\.timeout === "string" && gate\.config\.timeout\.trim\(\) === ""\) \{\n\s+delete gate\.config\.timeout;\n\s+\}/,
  );
});
