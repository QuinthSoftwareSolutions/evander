import assert from "node:assert/strict";
import test from "node:test";

import {
  normalizeWorkflowSourcePort,
  workflowConnectionRouteOptions,
  workflowEdgeRouteLabel,
  workflowRouteClass,
} from "../src/workflowRoutes.ts";

test("default routes have no edge label", () => {
  assert.equal(workflowEdgeRouteLabel(null, []), undefined);
  assert.equal(workflowEdgeRouteLabel(undefined, []), undefined);
  assert.equal(workflowEdgeRouteLabel("", []), undefined);
});

test("multi-route edges use catalog labels", () => {
  const ports = [
    { key: "started", label: "Started" },
    { key: "completed", label: "Completed" },
  ];
  assert.equal(workflowEdgeRouteLabel("started", ports, ["started", "completed"]), "Started");
  assert.equal(workflowEdgeRouteLabel("completed", ports, ["started", "completed"]), "Completed");
});

test("single unambiguous non-error route omits a label", () => {
  const ports = [{ key: "completed", label: "Completed" }];
  assert.equal(workflowEdgeRouteLabel("completed", ports, ["completed"]), undefined);
});

test("error routes are always labeled", () => {
  const ports = [
    { key: "completed", label: "Completed" },
    { key: "error", label: "Error" },
  ];
  assert.equal(workflowEdgeRouteLabel("completed", ports, ["completed", "error"]), undefined);
  assert.equal(workflowEdgeRouteLabel("error", ports, ["completed", "error"]), "Error");
});

test("route options use named ports when present and default otherwise", () => {
  assert.deepEqual(workflowConnectionRouteOptions([]), [
    {
      sourcePort: null,
      label: "Default",
      description: "Standard downstream path.",
      routeClass: "default",
    },
  ]);
  assert.deepEqual(workflowConnectionRouteOptions([{ key: "body", label: "Body" }]), [
    {
      sourcePort: "body",
      label: "Body",
      description: undefined,
      routeClass: "loop",
    },
  ]);
});

test("route classes group known route semantics", () => {
  assert.equal(normalizeWorkflowSourcePort(" completed "), "completed");
  assert.equal(workflowRouteClass(null), "default");
  assert.equal(workflowRouteClass("started"), "started");
  assert.equal(workflowRouteClass("completed"), "completed");
  assert.equal(workflowRouteClass("body"), "loop");
  assert.equal(workflowRouteClass("done"), "loop");
  assert.equal(workflowRouteClass("error"), "error");
  assert.equal(workflowRouteClass("custom"), "named");
});
