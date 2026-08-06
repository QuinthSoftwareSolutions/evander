import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import path from "node:path";
import test from "node:test";

import { chromium } from "playwright";
import { createServer } from "vite";

import { fulfillAdminShellBackgroundApiRequest } from "./adminShellHelpers.mjs";

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
    children: [
      {
        directory: {
          id: "team-a",
          name: "team-a",
          parent_id: "root",
          created_at: "2026-05-16T00:00:00Z",
          metadata: {},
          path: "root/team-a",
        },
        config_domains: [],
        children: [],
      },
    ],
  };
}

function attachPageListeners(page, pageErrors, consoleErrors) {
  page.on("pageerror", (error) => {
    pageErrors.push(error);
  });
  page.on("console", (message) => {
    if (message.type() === "error") {
      consoleErrors.push(message.text());
    }
  });
}

async function emitWorkflowRunSnapshot(page, payload) {
  await page.evaluate((snapshot) => {
    window.__emitWorkflowRunSnapshot(snapshot);
  }, payload);
}

async function waitForRuntimePulse(page, nodeId) {
  const selector = `.react-flow__node[data-id="${nodeId}"] .node-card.runtime-pulse`;
  await page.waitForFunction((currentSelector) => Boolean(document.querySelector(currentSelector)), selector);
}

async function waitForNoRuntimePulse(page, nodeId) {
  const selector = `.react-flow__node[data-id="${nodeId}"] .node-card.runtime-pulse`;
  await page.waitForFunction((currentSelector) => !document.querySelector(currentSelector), selector);
}

test("workflow editor pulses live nodes from recent activity and suppresses inspect mode", async (t) => {
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

  const requestedEndpoints = [];
  const pageErrors = [];
  const consoleErrors = [];
  const unhandledApiRoutes = [];

  await t.after(async () => {
    await page.close().catch(() => {});
    await context.close().catch(() => {});
    await browser.close().catch(() => {});
    await server.close().catch(() => {});
  });

  await page.addInitScript(() => {
    const eventSources = [];

    class FakeEventSource {
      constructor(url) {
        this.url = url;
        this.readyState = 1;
        this.closed = false;
        this.listeners = new Map();
        eventSources.push(this);
      }

      addEventListener(type, listener) {
        const listeners = this.listeners.get(type) ?? [];
        listeners.push(listener);
        this.listeners.set(type, listeners);
      }

      removeEventListener(type, listener) {
        const listeners = this.listeners.get(type) ?? [];
        this.listeners.set(
          type,
          listeners.filter((candidate) => candidate !== listener),
        );
      }

      dispatchEvent(event) {
        const listeners = this.listeners.get(event.type) ?? [];
        for (const listener of listeners) {
          listener.call(this, event);
        }
        const handler = this[`on${event.type}`];
        if (typeof handler === "function") {
          handler.call(this, event);
        }
        return true;
      }

      close() {
        this.closed = true;
      }
    }

    Object.defineProperty(window, "EventSource", {
      value: FakeEventSource,
      configurable: true,
      writable: true,
    });

    Object.defineProperty(window, "__emitWorkflowRunSnapshot", {
      value: (payload) => {
        const event = new MessageEvent("snapshot", {
          data: JSON.stringify(payload),
        });
        for (const eventSource of eventSources) {
          if (!eventSource.closed) {
            eventSource.dispatchEvent(event);
          }
        }
      },
      configurable: true,
      writable: true,
    });
  });

  const sessionResponse = {
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
    privileged_operations: {
      unlocked: true,
    },
  };
  const adminStateResponse = {
    selected_directory_path: "root",
    directory_scoped_config_enabled: true,
    execution_backend: "libvirt_vm",
    role: "admin",
    can_manage_users: true,
    privileged_operations: {
      unlocked: true,
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
      can_create: true,
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
          display_name: "Incoming ticket",
          type: "trigger:manual",
        },
      ],
      gates: [
        {
          id: "gate-1",
          display_name: "Hidden filter",
          type: "gate:filter",
        },
      ],
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
        "gate-1": { x: 360, y: 250 },
        "effect-1": { x: 360, y: 120 },
      },
    },
    graphs: [
      {
        id: "graph-root",
        name: "Live workflow",
        description: null,
        directory_paths: ["root"],
        user_ids: [],
        node_ids: ["trigger-1", "effect-1"],
      },
      {
        id: "graph-hidden",
        name: "Hidden support",
        description: null,
        directory_paths: ["root/team-a"],
        user_ids: [],
        node_ids: ["gate-1"],
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
        kind: "gate",
        type: "gate:filter",
        label: "Filter",
        fields: [],
        input_ports: [],
        output_ports: [],
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
    runs: [],
    node_activity: {},
  };
  const workflowRunDetail = {
    event_id: "evt-run-1",
    run_state: "succeeded",
    trace_id: "trace-run-1",
    source_node_id: "trigger-1",
    source_trigger_type: "trigger:manual",
    delivery_state: "delivered",
    occurred_at: "2026-05-16T12:00:00Z",
    latest_activity_at: "2026-05-16T12:00:03Z",
    active_nodes: [],
    error: null,
    resumable: false,
    worker_id: "trigger-orchestrator",
    generated_at: "2026-05-16T12:00:04Z",
    matched_by: ["event_id"],
    lookup_candidates: [
      {
        event_id: "evt-run-1",
        trace_id: "trace-run-1",
        latest_activity_at: "2026-05-16T12:00:03Z",
        matched_by: ["event_id"],
      },
    ],
    recovery: {
      clear: { eligible: true, event_id: "evt-run-1" },
      resume: { eligible: false, event_id: "evt-run-1", target_node_id: "effect-1" },
    },
    trigger_event: {
      event_id: "evt-run-1",
      trigger_id: "trigger-1",
      trigger_type: "trigger:manual",
      trace_id: "trace-run-1",
      occurred_at: "2026-05-16T12:00:00Z",
      delivery_state: "delivered",
      payload: {
        ticket_id: "TCK-1",
      },
    },
    graph: {
      config_revision: "rev-1",
      config: {
        triggers: [
          {
            id: "trigger-1",
            display_name: "Incoming ticket",
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
      snapshot: {
        revision: "rev-1",
        captured_at: "2026-05-16T12:00:00Z",
        snapshot_type: "run_start",
        source: "runtime",
        node_count: 2,
        edge_count: 1,
        triggers: [
          {
            id: "trigger-1",
            display_name: "Incoming ticket",
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
      data_availability: {
        graph_snapshot: "available",
        binding_trace: "available",
        approvals: "available",
        agent_links: "missing_legacy_or_unrecorded",
        communication_artifacts: "missing_legacy_or_unrecorded",
        tool_calls: "missing_legacy_or_unrecorded",
        trigger_event: "available",
      },
    },
    tool_call_links: [],
    nodes: [
      {
        node_id: "trigger-1",
        node_type: "trigger:manual",
        display_name: "Incoming ticket",
        source_kind: "trigger",
        execution_id: null,
        event_id: "evt-run-1",
        trace_id: "trace-run-1",
        status: "completed",
        started_at: "2026-05-16T12:00:00Z",
        finished_at: "2026-05-16T12:00:00Z",
        duration_ms: 0,
        occurred_at: "2026-05-16T12:00:00Z",
        delivery_state: "delivered",
        resolved_config: {},
        binding_trace: null,
        binding_error: null,
        input_payload: {},
        outputs: {},
        inputs_by_source: {},
        error: null,
        published_event_id: null,
        completion_event_enqueued_at: null,
        config_revision: "rev-1",
        retry_resume_eligible: false,
        retry_resume_reason: null,
        missing_fields: [],
        tool_call_links: [],
        approval_links: [],
        agent_links: [],
        communication_artifact_links: [],
        records: [],
      },
      {
        node_id: "effect-1",
        node_type: "effect:agent",
        display_name: "Dispatch agent",
        source_kind: "effect",
        execution_id: "exec-1",
        event_id: "evt-run-1",
        trace_id: "trace-run-1",
        status: "succeeded",
        started_at: "2026-05-16T12:00:01Z",
        finished_at: "2026-05-16T12:00:03Z",
        duration_ms: 2000,
        occurred_at: null,
        delivery_state: null,
        resolved_config: {
          prompt: "Handle ticket",
        },
        binding_trace: null,
        binding_error: null,
        input_payload: {
          ticket_id: "TCK-1",
        },
        outputs: {},
        inputs_by_source: {},
        error: null,
        published_event_id: null,
        completion_event_enqueued_at: null,
        config_revision: "rev-1",
        retry_resume_eligible: false,
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
      approvals: "available",
      agent_links: "missing_legacy_or_unrecorded",
      communication_artifacts: "missing_legacy_or_unrecorded",
      tool_calls: "missing_legacy_or_unrecorded",
      trigger_event: "available",
    },
  };

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const { pathname } = url;
    requestedEndpoints.push(`${pathname}${url.search}`);

    if (await fulfillAdminShellBackgroundApiRequest(route, pathname)) {
      return;
    }

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

    if (pathname === "/api/admin/user-preferences/workflow-tutorial-progress") {
      await route.fulfill(jsonResponse({
        completed_tutorial_ids: [],
        skipped_tutorial_ids: [],
      }));
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

    unhandledApiRoutes.push(`${pathname}${url.search}`);
    await route.fulfill(jsonResponse({ error: `Unhandled ${pathname}` }, 404));
  });

  attachPageListeners(page, pageErrors, consoleErrors);

  await page.goto(`http://127.0.0.1:${address.port}/#workflow`, {
    waitUntil: "domcontentloaded",
  });

  await page.locator('.react-flow__node[data-id="trigger-1"]').waitFor({ state: "visible" });
  await page.locator('.react-flow__node[data-id="effect-1"]').waitFor({ state: "visible" });
  assert.equal(await page.locator('.react-flow__node[data-id="gate-1"]').count(), 0);

  const liveEffectActivity = {
    activity_key: "effect-1:exec-1:2026-05-16T12:00:03Z",
    event_id: "evt-run-1",
    node_id: "effect-1",
    status: "succeeded",
    source: "effect_execution",
    activity_at: "2026-05-16T12:00:03Z",
    started_at: "2026-05-16T12:00:01Z",
    finished_at: "2026-05-16T12:00:03Z",
    trace_id: "trace-run-1",
    error: null,
  };
  const hiddenGateActivity = {
    activity_key: "gate-1:exec-1:2026-05-16T12:00:04Z",
    event_id: "evt-run-1",
    node_id: "gate-1",
    status: "succeeded",
    source: "gate_execution",
    activity_at: "2026-05-16T12:00:04Z",
    started_at: "2026-05-16T12:00:03Z",
    finished_at: "2026-05-16T12:00:04Z",
    trace_id: "trace-run-1",
    error: null,
  };

  await emitWorkflowRunSnapshot(page, {
    worker_id: "trigger-orchestrator",
    generated_at: "2026-05-16T12:00:05Z",
    runs: [],
    node_activity: {},
    recent_node_activity: [liveEffectActivity, hiddenGateActivity],
  });

  await waitForRuntimePulse(page, "effect-1");
  assert.equal(await page.locator('.react-flow__node[data-id="gate-1"] .node-card.runtime-pulse').count(), 0);

  await page.waitForTimeout(1600);
  await waitForNoRuntimePulse(page, "effect-1");

  await emitWorkflowRunSnapshot(page, {
    worker_id: "trigger-orchestrator",
    generated_at: "2026-05-16T12:00:06Z",
    runs: [],
    node_activity: {},
    recent_node_activity: [liveEffectActivity, hiddenGateActivity],
  });

  await page.waitForTimeout(300);
  assert.equal(await page.locator('.react-flow__node[data-id="effect-1"] .node-card.runtime-pulse').count(), 0);

  const refreshedEffectActivity = {
    ...liveEffectActivity,
    activity_key: "effect-1:exec-1:2026-05-16T12:00:07Z",
    activity_at: "2026-05-16T12:00:07Z",
    finished_at: "2026-05-16T12:00:07Z",
  };
  await emitWorkflowRunSnapshot(page, {
    worker_id: "trigger-orchestrator",
    generated_at: "2026-05-16T12:00:08Z",
    runs: [],
    node_activity: {},
    recent_node_activity: [refreshedEffectActivity],
  });

  await waitForRuntimePulse(page, "effect-1");
  await page.waitForTimeout(1600);
  await waitForNoRuntimePulse(page, "effect-1");

  // Tutorial mode shares the same runtime-pulse guard in App.tsx; this smoke keeps the harness focused on
  // live graph and inspect-mode suppression rather than adding a separate tutorial fixture.

  await page.goto(`http://127.0.0.1:${address.port}/#workflow?event_id=evt-run-1`, {
    waitUntil: "domcontentloaded",
  });

  await page.getByRole("button", { name: "Exit inspect mode" }).waitFor({ state: "visible" });
  await page.locator('.react-flow__node[data-id="effect-1"]').waitFor({ state: "visible" });

  await emitWorkflowRunSnapshot(page, {
    worker_id: "trigger-orchestrator",
    generated_at: "2026-05-16T12:00:09Z",
    runs: [],
    node_activity: {},
    recent_node_activity: [
      {
        activity_key: "effect-1:exec-1:2026-05-16T12:00:09Z",
        event_id: "evt-run-1",
        node_id: "effect-1",
        status: "succeeded",
        source: "effect_execution",
        activity_at: "2026-05-16T12:00:09Z",
        started_at: "2026-05-16T12:00:08Z",
        finished_at: "2026-05-16T12:00:09Z",
        trace_id: "trace-run-1",
        error: null,
      },
    ],
  });

  await page.waitForTimeout(300);
  assert.equal(await page.locator('.react-flow__node[data-id="effect-1"] .node-card.runtime-pulse').count(), 0);
  assert.equal(await page.locator('.react-flow__node[data-id="gate-1"] .node-card.runtime-pulse').count(), 0);

  assert.deepEqual(pageErrors, []);
  assert.deepEqual(consoleErrors, []);
  assert.deepEqual(unhandledApiRoutes, []);
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/auth/session"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/admin/deployment-status"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/admin/state?directory_path=root"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/admin/user-preferences/workflow-tutorial-progress"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/configuration/state?directory_path=root"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/workflow-editor/document"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/workflow-editor/catalog?directory_path=root"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/workflow-editor/runs"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/workflow-editor/run-detail?event_id=evt-run-1"));
});
