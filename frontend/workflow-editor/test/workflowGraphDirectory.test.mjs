import assert from "node:assert/strict";
import test from "node:test";

import {
  graphVisibleInDirectory,
  normalizeWorkflowGraphDirectoryPath,
} from "../src/workflowGraphDirectory.ts";

test("workflow graph directory visibility is exact", () => {
  assert.equal(graphVisibleInDirectory({ directory_path: "root" }, "root"), true);
  assert.equal(graphVisibleInDirectory({ directory_path: "root/team-a" }, "root"), false);
  assert.equal(graphVisibleInDirectory({ directory_path: "root/team-a" }, "root/team-a"), true);
  assert.equal(graphVisibleInDirectory({ directory_path: "root/team-a/project" }, "root/team-a"), false);
});

test("workflow graph directory visibility preserves legacy directory_paths fallback", () => {
  assert.equal(graphVisibleInDirectory({ directory_paths: ["root"] }, "root"), true);
  assert.equal(graphVisibleInDirectory({ directory_paths: ["root/team-a"] }, "root"), false);
  assert.equal(graphVisibleInDirectory({ directory_paths: ["root/team-a"] }, "root/team-a"), true);
  assert.equal(graphVisibleInDirectory({ directory_path: "  ", directory_paths: ["root/team-a"] }, "root/team-a"), true);
});

test("workflow graph directory normalization falls back to root", () => {
  assert.equal(normalizeWorkflowGraphDirectoryPath(""), "root");
  assert.equal(normalizeWorkflowGraphDirectoryPath("  root/team-a  "), "root/team-a");
  assert.equal(graphVisibleInDirectory({}, "root"), true);
});
