import assert from "node:assert/strict";
import test from "node:test";

import { adminHash } from "../src/adminRouting.ts";
import {
  WORKFLOW_FAILURE_CLUSTERS_REPORT_ENDPOINT,
  selectWorkflowFailureCluster,
  stateClassName,
  stateLabel,
  workflowFailureClusterRepresentativeRunHref,
  workflowFailureClusterRepresentativeRunLabel,
} from "../src/features/reports/workflowFailureClusters.ts";

test("workflow failure cluster endpoint and state helpers stay stable", () => {
  assert.equal(
    WORKFLOW_FAILURE_CLUSTERS_REPORT_ENDPOINT,
    "/api/admin/reports/workflow-failure-clusters",
  );
  assert.equal(stateLabel("active"), "Active");
  assert.equal(stateLabel("recent"), "Recent");
  assert.equal(stateLabel("resolved"), "Resolved");
  assert.equal(stateLabel("historical"), "Historical");
  assert.equal(stateClassName("active"), "state-active");
  assert.equal(stateClassName("recent"), "state-recent");
  assert.equal(stateClassName("resolved"), "state-resolved");
  assert.equal(stateClassName("historical"), "state-historical");
});

test("workflow failure cluster selection keeps the chosen cluster or falls back to the first", () => {
  const clusters = [
    {
      cluster_id: "cluster-1",
      graph_id: "graph-1",
      graph_name: "Graph 1",
      graph_description: null,
      directory_path: "root",
      node_id: "node-1",
      node_display_name: "Node 1",
      node_type: "effect:agent:prompt",
      trigger_type: null,
      effect_type: "effect:agent:prompt",
      gate_type: null,
      source_kind: "effect",
      error_signature: "Rejected branch update",
      error_signature_key: "rejected branch update",
      count: 2,
      first_seen_at: "2026-06-12T08:00:00Z",
      last_seen_at: "2026-06-12T09:00:00Z",
      latest_error_summary: "Rejected branch update",
      state: "recent",
      event_ids: ["evt-2", "evt-1"],
      trace_ids: ["trace-1"],
      session_ids: ["session-1"],
      pr_ids: ["pr-1"],
      ado_item_ids: ["ado-1"],
      issue_ids: [],
      artifact_ids: ["artifact-1"],
      examples: [],
    },
    {
      cluster_id: "cluster-2",
      graph_id: "graph-2",
      graph_name: "Graph 2",
      graph_description: null,
      directory_path: "root",
      node_id: "node-2",
      node_display_name: "Node 2",
      node_type: "trigger:ticket:created",
      trigger_type: "trigger:ticket:created",
      effect_type: null,
      gate_type: null,
      source_kind: "trigger",
      error_signature: "Missing ticket body",
      error_signature_key: "missing ticket body",
      count: 1,
      first_seen_at: "2026-06-12T10:00:00Z",
      last_seen_at: "2026-06-12T10:00:00Z",
      latest_error_summary: "Missing ticket body",
      state: "active",
      event_ids: ["evt-3"],
      trace_ids: ["trace-2"],
      session_ids: ["session-2"],
      pr_ids: [],
      ado_item_ids: [],
      issue_ids: ["issue-1"],
      artifact_ids: [],
      examples: [],
    },
  ];

  assert.equal(selectWorkflowFailureCluster(clusters, "cluster-2")?.cluster_id, "cluster-2");
  assert.equal(selectWorkflowFailureCluster(clusters, "missing")?.cluster_id, "cluster-1");
  assert.equal(selectWorkflowFailureCluster([], "missing"), null);
});

test("workflow failure cluster representative run links prefer event ids and fall back to traces", () => {
  const eventExample = {
    event_id: "evt-123",
    trace_id: "trace-123",
    session_id: "session-123",
    occurred_at: "2026-06-12T08:00:00Z",
    started_at: "2026-06-12T07:59:00Z",
    finished_at: "2026-06-12T08:00:00Z",
    error_summary: "Rejected branch update",
    run_state: "errored",
    run_detail_href: "#workflow?event_id=evt-123",
    api_run_detail_href: "/api/workflow-editor/run-detail?event_id=evt-123",
    artifact_ids: ["artifact-1"],
  };
  const traceExample = {
    event_id: null,
    trace_id: "trace-456",
    session_id: null,
    occurred_at: "2026-06-12T09:00:00Z",
    started_at: "2026-06-12T08:59:00Z",
    finished_at: "2026-06-12T09:00:00Z",
    error_summary: "Missing ticket body",
    run_state: "errored",
    run_detail_href: "#workflow?trace_id=trace-456",
    api_run_detail_href: "/api/workflow-editor/run-detail?trace_id=trace-456",
    artifact_ids: [],
  };

  assert.equal(
    workflowFailureClusterRepresentativeRunHref(eventExample),
    adminHash("workflow", { event_id: "evt-123" }),
  );
  assert.equal(
    workflowFailureClusterRepresentativeRunHref(traceExample),
    adminHash("workflow", { trace_id: "trace-456" }),
  );
  assert.equal(workflowFailureClusterRepresentativeRunLabel(eventExample), "Event evt-123");
  assert.equal(workflowFailureClusterRepresentativeRunLabel(traceExample), "Trace trace-456");
});
