import assert from "node:assert/strict";
import test, { before } from "node:test";
import { createServer } from "vite";

let fastModeSelection, fastModeValue, fastModeLabel, FastModeSelect;
before(async () => {
  const server = await createServer({ server: { middlewareMode: true }, appType: "custom", logLevel: "error" });
  try {
    ({ fastModeSelection, fastModeValue, fastModeLabel } = await server.ssrLoadModule("/src/features/admin/fastMode.ts"));
    ({ FastModeSelect } = await server.ssrLoadModule("/src/features/admin/FastModeSelect.tsx"));
  } finally {
    await server.close();
  }
}, { timeout: 10000 });

test("speed controls preserve each API value and label Ultrafast distinctly", () => {
  for (const [value, selection, label] of [[null, "", "Standard"], [false, "false", "Standard"], [true, "true", "Fast"], ["ultrafast", "ultrafast", "Ultrafast"]]) {
    assert.equal(fastModeSelection(value), selection);
    assert.equal(fastModeValue(selection), value);
    assert.equal(fastModeLabel(value), label);
  }
  for (const invalid of ["standard", "unknown"]) assert.throws(() => fastModeValue(invalid));
});

test("prompt picker shows an inherited Ultrafast default and gates accelerated tiers by route", () => {
  for (const [harness, route, supported] of [["codex", "openai_public", true], ["codex", "local_openai", false], ["claude", "anthropic", false]]) {
    const element = FastModeSelect({ value: "", onChange: () => {}, harness, route, prompt: true, inheritedFastMode: "ultrafast" });
    const select = element.props.children.find((child) => child.type === "select");
    const options = select.props.children;
    assert.equal(options[0].props.children, "Session default (Ultrafast)");
    for (const value of ["true", "ultrafast"]) {
      assert.equal(options.find((option) => option.props.value === value).props.disabled, !supported);
    }
    assert.equal(options.find((option) => option.props.value === "false").props.disabled, undefined);
  }
});
