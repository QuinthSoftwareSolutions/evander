import assert from "node:assert/strict";
import test from "node:test";
import { sortTableRows, timestampSortValue } from "../src/features/admin/tableSorting.ts";

const sort = (rows, direction = "ascending") =>
  sortTableRows(rows, { column: "value", direction }, row => row.value);

test("table sorting uses natural, case-insensitive names and preserves ties", () => {
  const rows = [{ value: "Agent 10" }, { value: "agent 2" }, { value: "Agent 2" }, { value: "agent 1" }];
  assert.deepEqual(sort(rows), [rows[3], rows[1], rows[2], rows[0]]);
  assert.deepEqual(sort(rows, "descending"), [rows[0], rows[1], rows[2], rows[3]]);
});

test("table sorting compares numeric values independently of their display units", () => {
  const rows = [
    { value: 1024, content: "1 KiB" },
    { value: 512, content: "512 B" },
    { value: 10240, content: "10 KiB" },
    { value: 2, content: "2 B" },
  ];
  assert.deepEqual(sort(rows), [rows[3], rows[1], rows[0], rows[2]]);
  assert.deepEqual(sort(rows, "descending"), [rows[2], rows[0], rows[1], rows[3]]);
});

test("table sorting compares actual instants across offsets and calendar boundaries", () => {
  const dates = ["2026-10-01T00:30:00+02:00", "2026-09-30T23:00:00Z", "2026-09-29T23:00:00Z"];
  const rows = dates.map(date => ({ value: timestampSortValue(date) }));
  assert.deepEqual(sort(rows), [rows[2], rows[0], rows[1]]);
  assert.deepEqual(sort(rows, "descending"), [rows[1], rows[0], rows[2]]);
  for (const missing of [null, undefined, "", "invalid"]) assert.equal(timestampSortValue(missing), null);
});

test("missing values remain last in both directions", () => {
  const rows = [null, 2, undefined, NaN, 1, "", Infinity].map(value => ({ value }));
  const missing = [rows[0], rows[2], rows[3], rows[5], rows[6]];
  assert.deepEqual(sort(rows), [rows[4], rows[1], ...missing]);
  assert.deepEqual(sort(rows, "descending"), [rows[1], rows[4], ...missing]);
});

test("sorting leaves source and default page order intact", () => {
  const rows = Object.freeze([{ value: 2 }, { value: 1 }]);
  assert.equal(sortTableRows(rows, null, row => row.value), rows);
  assert.deepEqual(sort(rows), [rows[1], rows[0]]);
  assert.deepEqual(rows.map(row => row.value), [2, 1]);
});
