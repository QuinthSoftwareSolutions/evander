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

function createDirectoryTree() {
  return {
    directory: {
      id: "root",
      name: "root",
      parent_id: null,
      created_at: "2026-05-16T00:00:00Z",
      metadata: {},
      path: "root",
    },
    config_domains: [],
    children: [],
  };
}

test("workflow editor keeps read-only workflow users in inspect mode but disables edit and control actions", async (t) => {
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
  const context = await browser.newContext({ viewport: { width: 1440, height: 1200 } });
  const page = await context.newPage();

  const pageErrors = [];
  const consoleErrors = [];
  const runDetailRequests = [];

  await t.after(async () => {
    await page.close().catch(() => {});
    await context.close().catch(() => {});
    await browser.close().catch(() => {});
    await server.close().catch(() => {});
  });

  const permissions = ["workflows.read", "workflow_runs.read"];
  const sessionResponse = {
    auth_mode: "none",
    auth_required: false,
    authenticated: true,
    username: "viewer",
    display_name: "Workflow Viewer",
    can_manage_users: false,
    permissions,
    effective_permissions: permissions,
    session_id: "session-123",
    available_login_methods: [],
    azure_sso_login_url: null,
    privileged_operations: {
      unlocked: false,
    },
  };
  const adminStateResponse = {
    selected_directory_path: "root",
    directory_scoped_config_enabled: true,
    execution_backend: "libvirt_vm",
    can_manage_users: false,
    permissions,
    effective_permissions: permissions,
    privileged_operations: {
      unlocked: false,
    },
    assistant: {
      enabled: false,
      show_launcher_when_disabled: false,
    },
  };
  const configurationStateResponse = {
    directory_scoped_config_enabled: true,
    selected_directory_path: "root",
    execution_backend: "libvirt_vm",
    selected_directory: {
      path: "root",
      parent_path: null,
      can_create: false,
      can_delete: false,
    },
    tree: {
      ok: true,
      data: createDirectoryTree(),
      errors: [],
      validation_diagnostics: [],
    },
  };
  const workflowDocument = {
    config_path: "workflow.json",
    layout_path: "workflow-layout.json",
    config_exists: true,
    layout_exists: true,
    revision: "rev-1",
    config: {
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
          id: "effect-1",
          display_name: "Dispatch agent",
          type: "effect:agent",
          prompt: "Handle ticket",
        },
      ],
      queries: [],
      edges: [
        {
          source_id: "trigger-1",
          target_id: "effect-1",
          source_port: "completed",
        },
      ],
    },
    layout: {
      nodes: {
        "trigger-1": { x: 80, y: 120 },
        "effect-1": { x: 360, y: 120 },
      },
    },
    graphs: [
      {
        id: "graph-1",
        name: "Read-only graph",
        description: null,
        directory_path: "root",
        user_ids: [],
        node_ids: ["trigger-1", "effect-1"],
      },
    ],
    output_schemas: {},
    validation_diagnostics: [],
  };
  const workflowCatalog = {
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
        fields: [
          {
            key: "prompt",
            label: "Prompt",
            kind: "string",
            required: false,
            bindable: false,
            read_only: false,
            multiline: false,
            description: null,
            placeholder: null,
            options: [],
            item_fields: [],
          },
        ],
        input_ports: [],
        output_ports: [],
      },
    ],
  };
  const workflowRuns = {
    worker_id: "trigger-orchestrator",
    generated_at: "2026-05-16T12:00:00Z",
    runs: [
      {
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
            node_id: "effect-1",
            status: "failed",
            count: 1,
            error: "agent failed",
          },
        ],
        failed_node_id: "effect-1",
        failed_node_status: "failed",
        failed_at: "2026-05-16T12:00:03Z",
        error: "agent failed",
        resumable: true,
      },
    ],
    node_activity: {},
  };
  const workflowRunDetail = {
    worker_id: "trigger-orchestrator",
    generated_at: "2026-05-16T12:01:00Z",
    event_id: "evt-run-1",
    run_state: "errored",
    trace_id: "trace-1",
    source_node_id: "trigger-1",
    source_trigger_type: "trigger:manual",
    delivery_state: "failed",
    occurred_at: "2026-05-16T12:00:00Z",
    latest_activity_at: "2026-05-16T12:00:03Z",
    active_nodes: workflowRuns.runs[0].active_nodes,
    failed_node_id: "effect-1",
    failed_node_status: "failed",
    failed_at: "2026-05-16T12:00:03Z",
    error: "agent failed",
    resumable: true,
    matched_by: ["event_id"],
    lookup_candidates: [
      {
        event_id: "evt-run-1",
        trace_id: "trace-1",
        latest_activity_at: "2026-05-16T12:00:03Z",
        matched_by: ["event_id"],
      },
    ],
    recovery: {
      clear: { eligible: true, event_id: "evt-run-1" },
      resume: { eligible: true, event_id: "evt-run-1", target_node_id: "effect-1" },
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
      config: workflowDocument.config,
      snapshot: {
        revision: "rev-1",
        captured_at: "2026-05-16T12:00:00Z",
        snapshot_type: "run_start",
        source: "runtime",
        node_count: 2,
        edge_count: 1,
        triggers: workflowDocument.config.triggers,
        gates: [],
        effects: workflowDocument.config.effects,
        queries: [],
        edges: workflowDocument.config.edges,
      },
      data_availability: {
        graph_snapshot: "available",
        binding_trace: "available",
        approvals: "missing_legacy_or_unrecorded",
        agent_links: "missing_legacy_or_unrecorded",
        communication_artifacts: "missing_legacy_or_unrecorded",
        trigger_event: "available",
      },
    },
    nodes: [
      {
        node_id: "effect-1",
        node_type: "effect:agent",
        display_name: "Dispatch agent",
        source_kind: "effect",
        execution_id: "exec-1",
        event_id: "evt-run-1",
        trace_id: "trace-1",
        status: "failed",
        started_at: "2026-05-16T12:00:00Z",
        finished_at: "2026-05-16T12:00:03Z",
        duration_ms: 3000,
        occurred_at: null,
        delivery_state: null,
        resolved_config: {
          prompt: "Handle ticket",
        },
        binding_trace: null,
        binding_error: null,
        input_payload: {},
        outputs: {},
        inputs_by_source: {},
        error: "agent failed",
        published_event_id: null,
        completion_event_enqueued_at: null,
        config_revision: "rev-1",
        retry_resume_eligible: true,
        retry_resume_reason: null,
        missing_fields: [],
        tool_call_links: [],
        approval_links: [],
        agent_links: [],
        communication_artifact_links: [],
        records: [],
      },
    ],
    data_availability: {
      graph_snapshot: "available",
      binding_trace: "available",
      approvals: "missing_legacy_or_unrecorded",
      agent_links: "missing_legacy_or_unrecorded",
      communication_artifacts: "missing_legacy_or_unrecorded",
      trigger_event: "available",
    },
  };

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const { pathname } = url;

    if (pathname === "/api/auth/session") {
      await route.fulfill(jsonResponse(sessionResponse));
      return;
    }

    if (pathname === "/api/admin/deployment-status") {
      await route.fulfill(
        jsonResponse({
          active: false,
          message: null,
          source: null,
          started_at: null,
          updated_at: null,
          ttl_seconds: 0,
          stale: false,
        }),
      );
      return;
    }

    if (pathname === "/api/admin/state") {
      assert.equal(url.searchParams.get("directory_path"), "root");
      await route.fulfill(jsonResponse(adminStateResponse));
      return;
    }

    if (pathname === "/api/configuration/state") {
      assert.equal(url.searchParams.get("directory_path"), "root");
      await route.fulfill(jsonResponse(configurationStateResponse));
      return;
    }

    if (pathname === "/api/workflow-editor/document") {
      await route.fulfill(jsonResponse(workflowDocument));
      return;
    }

    if (pathname === "/api/workflow-editor/catalog") {
      assert.equal(url.searchParams.get("directory_path"), "root");
      await route.fulfill(jsonResponse(workflowCatalog));
      return;
    }

    if (pathname === "/api/workflow-editor/runs") {
      await route.fulfill(jsonResponse(workflowRuns));
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
      assert.equal(url.searchParams.get("event_id"), "evt-run-1");
      await route.fulfill(jsonResponse(workflowRunDetail));
      return;
    }

    if (pathname === "/favicon.ico") {
      await route.fulfill({
        status: 204,
        body: "",
      });
      return;
    }

    await route.fulfill(jsonResponse({ error: `Unhandled ${pathname}` }, 404));
  });

  page.on("pageerror", (error) => {
    pageErrors.push(error);
  });
  page.on("console", (message) => {
    if (message.type() === "error") {
      consoleErrors.push(message.text());
    }
  });
  page.on("request", (request) => {
    if (request.url().includes("/api/workflow-editor/run-detail")) {
      const url = new URL(request.url());
      runDetailRequests.push(`${url.pathname}${url.search}`);
    }
  });

  await page.goto(`http://127.0.0.1:${address.port}/#workflow`, {
    waitUntil: "domcontentloaded",
  });

  await page.waitForSelector(".canvas-shell .react-flow__pane");
  await page.getByText("Current runs").click();
  await page.locator(".runtime-run-card").waitFor({ state: "visible" });
  await assert.equal(await page.getByRole("button", { name: "Generate workflow" }).isDisabled(), true);
  await assert.equal(await page.getByRole("button", { name: "Validate" }).isDisabled(), false);
  await assert.equal(await page.getByRole("button", { name: "Save workflow" }).isDisabled(), true);
  await assert.equal(await page.locator(".runtime-run-card").getByRole("button", { name: "Inspect" }).isDisabled(), false);
  await assert.equal(await page.locator(".runtime-run-card").getByRole("button", { name: "Resume from failure" }).isDisabled(), true);
  await assert.equal(await page.locator(".runtime-run-card").getByRole("button", { name: "Dismiss run" }).isDisabled(), true);

  await page.locator(".runtime-run-card").getByRole("button", { name: "Inspect" }).click();
  await page.waitForTimeout(250);
  assert.deepEqual(runDetailRequests, ["/api/workflow-editor/run-detail?event_id=evt-run-1"]);

  assert.deepEqual(pageErrors, []);
});

test("workflow editor opens workflow definitions without workflow run read access", async (t) => {
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
  const context = await browser.newContext({ viewport: { width: 1440, height: 1200 } });
  const page = await context.newPage();

  const pageErrors = [];
  const runApiRequests = [];

  await t.after(async () => {
    await page.close().catch(() => {});
    await context.close().catch(() => {});
    await browser.close().catch(() => {});
    await server.close().catch(() => {});
  });

  const permissions = ["workflows.read"];
  const sessionResponse = {
    auth_mode: "none",
    auth_required: false,
    authenticated: true,
    username: "definition-viewer",
    display_name: "Definition Viewer",
    can_manage_users: false,
    permissions,
    effective_permissions: permissions,
    session_id: "session-definition-viewer",
    available_login_methods: [],
    azure_sso_login_url: null,
    privileged_operations: {
      unlocked: false,
    },
  };
  const adminStateResponse = {
    selected_directory_path: "root",
    directory_scoped_config_enabled: true,
    execution_backend: "libvirt_vm",
    can_manage_users: false,
    permissions,
    effective_permissions: permissions,
    privileged_operations: {
      unlocked: false,
    },
    assistant: {
      enabled: false,
      show_launcher_when_disabled: false,
    },
  };
  const configurationStateResponse = {
    directory_scoped_config_enabled: true,
    selected_directory_path: "root",
    execution_backend: "libvirt_vm",
    selected_directory: {
      path: "root",
      parent_path: null,
      can_create: false,
      can_delete: false,
    },
    tree: {
      ok: true,
      data: createDirectoryTree(),
      errors: [],
      validation_diagnostics: [],
    },
  };
  const workflowDocument = {
    config_path: "workflow.json",
    layout_path: "workflow-layout.json",
    config_exists: true,
    layout_exists: true,
    revision: "rev-definition-only",
    config: {
      triggers: [
        {
          id: "definition-trigger",
          display_name: "Definition trigger",
          type: "trigger:manual",
        },
      ],
      gates: [],
      effects: [
        {
          id: "definition-effect",
          display_name: "Definition effect",
          type: "effect:agent",
          prompt: "Inspect only",
        },
      ],
      queries: [],
      edges: [
        {
          source_id: "definition-trigger",
          target_id: "definition-effect",
          source_port: "completed",
        },
      ],
    },
    layout: {
      nodes: {
        "definition-trigger": { x: 80, y: 120 },
        "definition-effect": { x: 360, y: 120 },
      },
    },
    graphs: [
      {
        id: "graph-definition",
        name: "Definition-only graph",
        description: null,
        directory_path: "root",
        user_ids: [],
        node_ids: ["definition-trigger", "definition-effect"],
      },
    ],
    output_schemas: {},
    validation_diagnostics: [],
  };
  const workflowCatalog = {
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
        fields: [],
        input_ports: [],
        output_ports: [],
      },
    ],
  };

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const { pathname } = url;

    if (pathname === "/api/auth/session") {
      await route.fulfill(jsonResponse(sessionResponse));
      return;
    }

    if (pathname === "/api/admin/deployment-status") {
      await route.fulfill(
        jsonResponse({
          active: false,
          message: null,
          source: null,
          started_at: null,
          updated_at: null,
          ttl_seconds: 0,
          stale: false,
        }),
      );
      return;
    }

    if (pathname === "/api/admin/state") {
      assert.equal(url.searchParams.get("directory_path"), "root");
      await route.fulfill(jsonResponse(adminStateResponse));
      return;
    }

    if (pathname === "/api/configuration/state") {
      assert.equal(url.searchParams.get("directory_path"), "root");
      await route.fulfill(jsonResponse(configurationStateResponse));
      return;
    }

    if (pathname === "/api/admin/user-preferences/workflow-tutorial-progress") {
      await route.fulfill(jsonResponse({ completed_tutorial_ids: [], skipped_tutorial_ids: [] }));
      return;
    }

    if (pathname === "/api/workflow-editor/document") {
      await route.fulfill(jsonResponse(workflowDocument));
      return;
    }

    if (pathname === "/api/workflow-editor/catalog") {
      assert.equal(url.searchParams.get("directory_path"), "root");
      await route.fulfill(jsonResponse(workflowCatalog));
      return;
    }

    if (pathname === "/api/workflow-editor/runs" || pathname === "/api/workflow-editor/runs/stream") {
      runApiRequests.push(pathname);
      await route.fulfill(jsonResponse({ error: "Permission required: workflow_runs.read" }, 403));
      return;
    }

    await route.fulfill(jsonResponse({ error: `Unhandled ${pathname}` }, 404));
  });

  page.on("pageerror", (error) => {
    pageErrors.push(error);
  });

  await page.goto(`http://127.0.0.1:${address.port}/#workflow`, {
    waitUntil: "domcontentloaded",
  });

  await page.waitForSelector(".canvas-shell .react-flow__pane");
  await page.locator(".workflow-graphs-section summary").click();
  await page.locator(".workflow-graph-name", { hasText: "Definition-only graph" }).waitFor({ state: "visible" });

  assert.equal(await page.getByText("Current runs", { exact: true }).count(), 0);
  assert.deepEqual(runApiRequests, []);
  assert.deepEqual(pageErrors, []);
});
