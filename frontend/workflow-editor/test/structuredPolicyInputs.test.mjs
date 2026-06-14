import assert from "node:assert/strict";
import test from "node:test";

import {
  dedupeStrings,
  encodeStringListDraftValue,
  formatJsonTextDraftValue,
  jsonObjectDraftRowsFromValue,
  normalizeLeadingDotExtension,
  normalizeLeadingDotExtensions,
  parseJsonTextDraftValue,
  parseJsonObjectDraftRows,
  parseRegexPatternDraftRows,
  parseStringListDraftValue,
  parseStringMapDraftRows,
  regexPatternDraftRowsFromValue,
  stringMapDraftRowsFromValue,
  validateRegexPattern,
  validateJsonTextDraftValue,
} from "../src/features/admin/structuredPolicyInputs.ts";

test("dedupeStrings trims blanks and preserves first-seen values", () => {
  assert.deepEqual(dedupeStrings([" alpha ", "", "beta", "alpha", "beta", "gamma "]), [
    "alpha",
    "beta",
    "gamma",
  ]);
});

test("string list helpers normalize and encode canonical rows", () => {
  assert.deepEqual(parseStringListDraftValue("first\n second \nfirst\n"), ["first", "second"]);
  assert.equal(encodeStringListDraftValue([" first ", "second", "", "first"]), "first\nsecond");
});

test("leading-dot extension helpers normalize and dedupe", () => {
  assert.equal(normalizeLeadingDotExtension("txt"), ".txt");
  assert.equal(normalizeLeadingDotExtension(" .tar.gz "), ".tar.gz");
  assert.deepEqual(normalizeLeadingDotExtensions(["txt", ".txt", "md", ""] ), [".txt", ".md"]);
});

test("string map rows reject blank and duplicate keys", () => {
  const rows = stringMapDraftRowsFromValue({ one: "1", two: 2 });
  assert.deepEqual(rows.map((row) => [row.key, row.value]), [
    ["one", "1"],
    ["two", "2"],
  ]);
  assert.deepEqual(parseStringMapDraftRows(rows, "Labels"), {
    one: "1",
    two: "2",
  });
  assert.throws(
    () =>
      parseStringMapDraftRows(
        [
          { id: "one", key: "alpha", value: "1" },
          { id: "two", key: " alpha ", value: "2" },
        ],
        "Labels",
      ),
    /Labels contains duplicate key 'alpha'\./,
  );
  assert.throws(
    () =>
      parseStringMapDraftRows(
        [{ id: "blank", key: "", value: "value" }],
        "Labels",
      ),
    /Labels contains a value without a key\./,
  );
});

test("json object rows preserve nested values and round-trip untouched rows", () => {
  const source = {
    alpha: "text",
    beta: 2,
    delta: null,
    gamma: { nested: [true, null] },
  };
  const rows = jsonObjectDraftRowsFromValue(source);
  assert.deepEqual(rows.map((row) => row.key), ["alpha", "beta", "delta", "gamma"]);
  assert.equal(rows.find((row) => row.key === "delta")?.valueText, "null");
  assert.deepEqual(parseJsonObjectDraftRows(rows, "Metadata"), source);
  assert.throws(
    () =>
      parseJsonObjectDraftRows(
        [{ id: "dup-a", key: "alpha", valueText: "1" }, { id: "dup-b", key: "alpha", valueText: "2" }],
        "Metadata",
      ),
    /Metadata contains duplicate key 'alpha'\./,
  );
});

test("json text helpers format and validate generic json values", () => {
  assert.equal(formatJsonTextDraftValue(null), "");
  assert.equal(formatJsonTextDraftValue({ alpha: 1 }), '{\n  "alpha": 1\n}');
  assert.deepEqual(parseJsonTextDraftValue('{"alpha":1}', "Metadata"), { alpha: 1 });
  assert.equal(validateJsonTextDraftValue("{}", "Metadata"), null);
  assert.match(
    validateJsonTextDraftValue("{", "Metadata") ?? "",
    /Metadata must be valid JSON:/,
  );
  assert.throws(
    () => parseJsonTextDraftValue("", "Metadata"),
    /Metadata must be valid JSON\./,
  );
});

test("regex helpers validate patterns and drop blanks", () => {
  assert.equal(validateRegexPattern("^seed-.*$"), null);
  assert.match(validateRegexPattern("(") ?? "", /Invalid regular expression/);
  assert.deepEqual(
    parseRegexPatternDraftRows(
      [
        { id: "one", pattern: " ^seed-.*$ " },
        { id: "two", pattern: "" },
        { id: "three", pattern: "^seed-.*$" },
      ],
      "Patterns",
    ),
    ["^seed-.*$"],
  );
  assert.throws(
    () =>
      parseRegexPatternDraftRows(
        [{ id: "bad", pattern: "(" }],
        "Patterns",
      ),
    /Patterns row 1 invalid regular expression/,
  );
  assert.deepEqual(regexPatternDraftRowsFromValue(["^a$", "^b$"]).map((row) => row.pattern), ["^a$", "^b$"]);
});
