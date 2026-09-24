import { readWorkflowEditorSource } from "./workflowEditorSource.mjs";
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

const app = readWorkflowEditorSource();
const styles = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");

test("workflow graph canvas labels stay single-line and scale with the canvas zoom", () => {
  assert.match(app, /"--workflow-graph-label-scale": Math\.max\(0\.45, Math\.min\(1, canvasViewport\.zoom\)\)/);
  const graphLabelStyles = styles.slice(
    styles.indexOf(".workflow-graph-canvas-label {"),
    styles.indexOf(".workflow-graph-canvas-label.selected {"),
  );
  assert.match(graphLabelStyles, /scale\(var\(--workflow-graph-label-scale, 1\)\)/);
  assert.match(graphLabelStyles, /transform-origin: right center/);
  assert.match(graphLabelStyles, /font-size: clamp\(0\.9rem, 1\.1vw, 1\.35rem\)/);
  assert.match(graphLabelStyles, /white-space: nowrap/);
});
