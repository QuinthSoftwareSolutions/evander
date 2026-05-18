import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import path from "node:path";
import test from "node:test";

import { chromium } from "playwright";
import { createServer } from "vite";

function jsonResponse(payload, status = 200) {
  return {
    status,
    contentType: "application/json",
    body: JSON.stringify(payload),
  };
}

const fieldDefaults = {
  required: false,
  bindable: false,
  read_only: false,
  multiline: false,
  options: [],
  item_fields: [],
};

test("workflow editor inspects a stored workflow run detail", async (t) => {
  const projectRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
  const server = await createServer({
    root: projectRoot,
    server: {
      host: "127.0.0.1",
      port: 0,
    },
    logLevel: "error",
  });
  await server.listen();

  const address = server.httpServer?.address();
  assert.ok(address && typeof address === "object" && "port" in address, "expected Vite to bind an ephemeral port");

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext();
  const page = await context.newPage();
  const detailRequests = [];

  await t.after(async () => {
    await page.close().catch(() => {});
    await context.close().catch(() => {});
    await browser.close().catch(() => {});
    await server.close().catch(() => {});
  });

  const config = {
    triggers: [
      {
        id: "trigger-1",
        display_name: "Incoming command",
        type: "trigger:manual",
      },
    ],
    gates: [],
    effects: [
      {
        id: "agent-1",
        display_name: "Dispatch agent",
        type: "effect:agent",
      },
      {
        id: "notify-1",
        display_name: "Notify operator",
        type: "effect:notify",
      },
    ],
    queries: [],
    edges: [
      { source_id: "trigger-1", target_id: "agent-1", source_port: "completed" },
      { source_id: "agent-1", target_id: "notify-1", source_port: "completed" },
    ],
  };
  const graph = {
    id: "graph-1",
    name: "Support flow",
    description: null,
    directory_paths: ["root"],
    user_ids: [],
    node_ids: ["trigger-1", "agent-1", "notify-1"],
  };
  const workflowDocument = {
    config_path: "workflow.json",
    layout_path: "workflow-layout.json",
    config_exists: true,
    layout_exists: true,
    config,
    layout: {
      nodes: {
        "trigger-1": { x: 80, y: 80 },
        "agent-1": { x: 360, y: 80 },
        "notify-1": { x: 640, y: 80 },
      },
    },
    graphs: [graph],
    output_schemas: {},
    validation_diagnostics: [],
  };
  const catalog = {
    worker_id: "trigger-orchestrator",
    nodes: [
      {
        kind: "trigger",
        type: "trigger:manual",
        label: "Manual",
        fields: [],
        input_ports: [],
        output_ports: [{ key: "completed", label: "Completed" }],
      },
      {
        kind: "effect",
        type: "effect:agent",
        label: "Agent",
        fields: [{ ...fieldDefaults, key: "prompt", label: "Prompt", kind: "string" }],
        input_ports: [],
        output_ports: [],
      },
      {
        kind: "effect",
        type: "effect:notify",
        label: "Notify",
        fields: [],
        input_ports: [],
        output_ports: [],
      },
    ],
  };
  const runSummary = {
    event_id: "evt-run-1",
    run_state: "errored",
    trace_id: "trace-1",
    source_node_id: "trigger-1",
    source_trigger_type: "trigger:manual",
    delivery_state: "failed",
    occurred_at: "2026-05-16T12:00:00Z",
    latest_activity_at: "2026-05-16T12:00:03Z",
    active_nodes: [
      {
        node_id: "agent-1",
        status: "failed",
        count: 1,
        error: "agent failed",
      },
    ],
    failed_node_id: "agent-1",
    failed_node_status: "failed",
    failed_at: "2026-05-16T12:00:03Z",
    error: "agent failed",
    resumable: true,
  };
  const runDetail = {
    ...runSummary,
    worker_id: "trigger-orchestrator",
    generated_at: "2026-05-16T12:05:00Z",
    matched_by: ["event_id"],
    lookup_candidates: [{ event_id: "evt-run-1", trace_id: "trace-1", latest_activity_at: "2026-05-16T12:00:03Z", matched_by: ["event_id"] }],
    recovery: {
      clear: { eligible: true, event_id: "evt-run-1" },
      resume: { eligible: true, event_id: "evt-run-1", target_node_id: "agent-1" },
    },
    trigger_event: {
      event_id: "evt-run-1",
      trigger_id: "trigger-1",
      trigger_type: "trigger:manual",
      trace_id: "trace-1",
      occurred_at: "2026-05-16T12:00:00Z",
      delivery_state: "failed",
      payload: { command: "inspect" },
    },
    graph: {
      config_revision: "rev-1",
      config,
      snapshot: {
        revision: "rev-1",
        captured_at: "2026-05-16T12:00:00Z",
        snapshot_type: "run_start",
        source: "runtime",
        node_count: 3,
        edge_count: 2,
        triggers: config.triggers,
        gates: [],
        effects: config.effects,
        queries: [],
        edges: config.edges,
        workflow_graphs: [graph],
      },
      data_availability: {
        graph_snapshot: "available",
        binding_trace: "available",
        approvals: "available",
        agent_links: "available",
        communication_artifacts: "available",
        trigger_event: "available",
      },
    },
    nodes: [
      {
        node_id: "agent-1",
        node_type: "effect:agent",
        display_name: "Dispatch agent",
        source_kind: "effect",
        execution_id: "exec-1",
        event_id: "evt-run-1",
        trace_id: "trace-1",
        status: "failed",
        started_at: "2026-05-16T12:00:01Z",
        finished_at: "2026-05-16T12:00:03Z",
        duration_ms: 2000,
        occurred_at: null,
        delivery_state: null,
        resolved_config: { prompt: "Handle ticket" },
        binding_trace: { prompt: { source: "trigger-1.payload.command" } },
        binding_error: null,
        input_payload: { command: "inspect" },
        outputs: {},
        inputs_by_source: { "trigger-1": { command: "inspect" } },
        error: "agent failed",
        published_event_id: null,
        completion_event_enqueued_at: null,
        config_revision: "rev-1",
        retry_resume_eligible: true,
        retry_resume_reason: "failed effect can resume",
        missing_fields: [],
        approval_links: [
          {
            request_id: "appr-1",
            session_id: "session-1",
            action_type: "prompt_agent",
            tool_name: "prompt_agent",
            directory_path: "root",
            payload_digest: "sha256:abc",
            status: "approved",
            approver: "ops",
            decision_at: "2026-05-16T12:00:02Z",
            decision_reason: null,
            expires_at: null,
            human_summary: "Dispatch agent",
          },
        ],
        agent_links: [
          {
            node_id: "agent-1",
            node_type: "effect:agent",
            session_id: "session-1",
            vm_name: "vm-1",
            prompt: "Handle ticket",
            dispatch_id: "dispatch-1",
            thread_id: "thread-1",
            execution_id: "exec-1",
            artifact_ids: ["comm_1"],
            status: "failed",
            waiting: false,
            completed: true,
            log_reference: { vm_name: "vm-1" },
          },
        ],
        communication_artifact_links: [
          {
            artifact_id: "comm_1",
            session_id: "session-1",
            provider: "teams",
            original_filename: "result.txt",
            filename: "result.txt",
            mime_type: "text/plain",
            byte_count: 128,
            sha256: "abc",
            status: "stored",
            metadata: {},
          },
        ],
        records: [],
      },
    ],
    data_availability: {
      graph_snapshot: "available",
      binding_trace: "available",
      approvals: "available",
      agent_links: "available",
      communication_artifacts: "available",
      trigger_event: "available",
    },
  };

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const { pathname } = url;

    if (pathname === "/api/auth/session") {
      await route.fulfill(jsonResponse({
        auth_mode: "none",
        auth_required: false,
        authenticated: true,
        username: "admin",
        display_name: "Admin Operator",
        role: "admin",
        can_manage_users: true,
        session_id: "session-123",
        available_login_methods: [],
        azure_sso_login_url: null,
        privileged_operations: { unlocked: true },
      }));
      return;
    }
    if (pathname === "/api/admin/deployment-status") {
      await route.fulfill(jsonResponse({ active: false, message: null, source: null, started_at: null, updated_at: null, ttl_seconds: 0, stale: false }));
      return;
    }
    if (pathname === "/api/admin/state") {
      await route.fulfill(jsonResponse({
        selected_directory_path: "root",
        directory_scoped_config_enabled: false,
        execution_backend: "libvirt_vm",
        role: "admin",
        can_manage_users: true,
        privileged_operations: { unlocked: true },
        assistant: { enabled: false, show_launcher_when_disabled: false },
      }));
      return;
    }
    if (pathname === "/api/configuration/state") {
      await route.fulfill(jsonResponse({
        directory_scoped_config_enabled: false,
        selected_directory_path: "root",
        execution_backend: "libvirt_vm",
        selected_directory: { path: "root", parent_path: null, can_create: false, can_delete: false },
        tree: {
          ok: true,
          data: {
            directory: { id: "root", name: "root", parent_id: null, created_at: "2026-05-16T00:00:00Z", metadata: {}, path: "root" },
            config_domains: [],
            children: [],
          },
          errors: [],
          validation_diagnostics: [],
        },
      }));
      return;
    }
    if (pathname === "/api/workflow-editor/document") {
      await route.fulfill(jsonResponse(workflowDocument));
      return;
    }
    if (pathname === "/api/workflow-editor/catalog") {
      await route.fulfill(jsonResponse(catalog));
      return;
    }
    if (pathname === "/api/workflow-editor/runs") {
      await route.fulfill(jsonResponse({
        worker_id: "trigger-orchestrator",
        generated_at: "2026-05-16T12:05:00Z",
        runs: [runSummary],
        node_activity: { "agent-1": { failed: 1 } },
      }));
      return;
    }
    if (pathname === "/api/workflow-editor/runs/stream") {
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        headers: {
          "Cache-Control": "no-cache",
          Connection: "keep-alive",
        },
        body: ": workflow run stream\n\n",
      });
      return;
    }
    if (pathname === "/api/workflow-editor/run-detail") {
      detailRequests.push(`${pathname}${url.search}`);
      assert.equal(url.searchParams.get("event_id"), "evt-run-1");
      await route.fulfill(jsonResponse(runDetail));
      return;
    }

    await route.fulfill(jsonResponse({ error: `Unhandled ${pathname}` }, 404));
  });

  await page.route("**/favicon.ico", async (route) => {
    await route.fulfill({
      status: 204,
      body: "",
    });
  });

  const pageErrors = [];
  const consoleErrors = [];
  const notFoundResponses = [];
  page.on("pageerror", (error) => {
    pageErrors.push(error);
  });
  page.on("console", (message) => {
    if (message.type() === "error") {
      consoleErrors.push(message.text());
    }
  });
  page.on("response", (response) => {
    if (response.status() === 404) {
      notFoundResponses.push(response.url());
    }
  });

  await page.goto(`http://127.0.0.1:${address.port}/#workflow`, {
    waitUntil: "domcontentloaded",
  });

  await page.getByText("Current runs").click();
  await page.locator(".runtime-run-card").getByRole("button", { name: "Inspect" }).click();

  await assert.doesNotReject(() => page.getByText("Inspected run").waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByText("Historical graph snapshot").waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByRole("button", { name: "Exit inspect mode" }).waitFor({ state: "visible" }));
  assert.equal(await page.getByText("Current runs").count(), 0);
  assert.equal(await page.getByRole("button", { name: "Save workflow" }).count(), 0);
  assert.equal(await page.getByText("Create").count(), 0);
  await assert.doesNotReject(() => page.locator(".node-card.inspect-ghost", { hasText: "Notify operator" }).waitFor({ state: "visible" }));

  await page.locator(".react-flow__node", { hasText: "Dispatch agent" }).click();
  assert.equal(
    await page.locator(".inspect-node-panel .read-only-inspector-field", { hasText: "Prompt" }).locator("input").inputValue(),
    "Handle ticket",
  );
  await assert.doesNotReject(() => page.getByText("Approval appr-1").waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByText("Boundary root").waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByText("Agent session-1").waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByText("Artifact comm_1").waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.locator(".run-detail-json > summary").getByText("Binding trace", { exact: true }).waitFor({ state: "visible" }));

  await page.locator(".react-flow__node", { hasText: "Notify operator" }).click();
  await assert.doesNotReject(() => page.getByText("Not visited in this run").waitFor({ state: "visible" }));

  assert.deepEqual(detailRequests, ["/api/workflow-editor/run-detail?event_id=evt-run-1"]);
  assert.deepEqual(pageErrors, []);
  assert.deepEqual(notFoundResponses, []);
  assert.deepEqual(consoleErrors, []);
});
