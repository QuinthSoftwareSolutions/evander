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
  assert.equal(workflowEdgeRouteLabel("started", ports), "Started");
  assert.equal(workflowEdgeRouteLabel("completed", ports), "Completed");
});

test("multi-route edges stay labeled even when only one edge is connected", () => {
  const ports = [
    { key: "started", label: "Started" },
    { key: "completed", label: "Completed" },
  ];
  assert.equal(workflowEdgeRouteLabel("completed", ports), "Completed");
});

test("single unambiguous non-error route omits a label", () => {
  const ports = [{ key: "completed", label: "Completed" }];
  assert.equal(workflowEdgeRouteLabel("completed", ports), undefined);
});

test("error routes are always labeled", () => {
  const ports = [
    { key: "completed", label: "Completed" },
    { key: "error", label: "Error" },
  ];
  assert.equal(workflowEdgeRouteLabel("completed", ports), undefined);
  assert.equal(workflowEdgeRouteLabel("error", ports), "Error");
});

test("unknown non-default routes fall back to a humanized label", () => {
  assert.equal(workflowEdgeRouteLabel("custom-route", []), "Custom Route");
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
