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

function createFieldDefinition(partial) {
  return {
    required: false,
    bindable: false,
    read_only: false,
    multiline: false,
    description: null,
    placeholder: null,
    options: [],
    item_fields: [],
    ...partial,
  };
}

function createWorkflowDocument() {
  return {
    config_path: "workflow.json",
    layout_path: "workflow-layout.json",
    config_exists: true,
    layout_exists: true,
    revision: "rev-1",
    config: {
      triggers: [
        {
          id: "trigger-old",
          display_name: "Incoming ticket",
          type: "trigger:manual",
        },
      ],
      gates: [
        {
          id: "gate-old",
          display_name: "Route ticket",
          type: "gate:ticket-route",
          condition: "${{trigger-old.ticket.title}}",
        },
      ],
      effects: [
        {
          id: "effect-old",
          display_name: "Summarize ticket",
          type: "effect:ticket-summary",
          payload: {
            summary: "Ticket ${{trigger-old.ticket.title}}",
            details: [
              {
                label: "Ticket id",
                value: "${{trigger-old.ticket.id}}",
              },
              {
                label: "System time",
                value: "${{system.now}}",
              },
              {
                label: "Malformed",
                value: "Review ${{trigger-old.ticket ?? {bad}}}",
              },
              {
                label: "External",
                value: "Review ${{outside-node.ticket.title}}",
              },
            ],
          },
        },
      ],
      queries: [
        {
          id: "query-old",
          display_name: "Lookup ticket",
          type: "query:ticket-filter",
          filter: "${{trigger-old.ticket.title ?? trigger-old.ticket.id}}",
        },
      ],
      edges: [
        {
          source_id: "trigger-old",
          target_id: "gate-old",
          source_port: "completed",
        },
        {
          source_id: "trigger-old",
          target_id: "effect-old",
          source_port: "completed",
        },
        {
          source_id: "trigger-old",
          target_id: "query-old",
          source_port: "completed",
        },
      ],
    },
    layout: {
      nodes: {
        "trigger-old": { x: 100, y: 120 },
        "gate-old": { x: 420, y: 60 },
        "effect-old": { x: 420, y: 220 },
        "query-old": { x: 420, y: 380 },
      },
    },
    graphs: [
      {
        id: "graph-1",
        name: "Rename bindings smoke",
        description: null,
        directory_paths: ["root"],
        user_ids: [],
        node_ids: ["trigger-old", "gate-old", "effect-old", "query-old"],
      },
    ],
    output_schemas: {
      "trigger-old": {
        ticket: {
          type: "object",
          properties: {
            title: { type: "string" },
            id: { type: "string" },
          },
        },
      },
    },
    validation_diagnostics: [],
  };
}

function createWorkflowCatalog() {
  return {
    worker_id: "trigger-orchestrator",
    nodes: [
      {
        kind: "trigger",
        type: "trigger:manual",
        label: "Manual trigger",
        fields: [],
        input_ports: [],
        output_ports: [{ key: "completed", label: "Completed" }],
      },
      {
        kind: "gate",
        type: "gate:ticket-route",
        label: "Ticket route",
        fields: [
          createFieldDefinition({
            key: "condition",
            label: "Condition",
            kind: "string",
            bindable: true,
          }),
        ],
        input_ports: [],
        output_ports: [],
      },
      {
        kind: "effect",
        type: "effect:ticket-summary",
        label: "Ticket summary",
        fields: [
          createFieldDefinition({
            key: "payload",
            label: "Payload",
            kind: "json",
            bindable: true,
            json_value_type: "object",
            default_value: {},
          }),
        ],
        input_ports: [],
        output_ports: [],
      },
      {
        kind: "query",
        type: "query:ticket-filter",
        label: "Ticket filter",
        fields: [
          createFieldDefinition({
            key: "filter",
            label: "Filter",
            kind: "string",
            bindable: true,
          }),
        ],
        input_ports: [],
        output_ports: [],
      },
    ],
  };
}

function createSampleState(active) {
  const tutorialSteps = [
    {
      step_id: "launch",
      title: "Launch the sample",
      body: "Create the guided in-memory sample workflow.",
      status: active ? "completed" : "active",
      node_id: null,
    },
  ];
  return {
    active,
    launched_at: active ? "2026-05-16T12:00:00Z" : null,
    run: null,
    detail: null,
    tutorial_steps: tutorialSteps,
    current_step: tutorialSteps[0],
  };
}

test("workflow editor renames a node id and rewrites downstream bindings when saved", async (t) => {
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
  const saveRequests = [];

  await t.after(async () => {
    await page.close().catch(() => {});
    await context.close().catch(() => {});
    await browser.close().catch(() => {});
    await server.close().catch(() => {});
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
  const workflowDocument = createWorkflowDocument();
  const workflowCatalog = createWorkflowCatalog();
  const workflowRuns = {
    worker_id: "trigger-orchestrator",
    generated_at: "2026-05-16T12:00:00Z",
    runs: [],
    node_activity: {},
  };

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const { pathname } = url;
    requestedEndpoints.push(`${pathname}${url.search}`);

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

    if (pathname === "/api/workflow-editor/sample-workflow") {
      if (route.request().method() === "DELETE") {
        await route.fulfill(jsonResponse(createSampleState(false)));
        return;
      }
      await route.fulfill(jsonResponse(createSampleState(false)));
      return;
    }

    if (pathname === "/api/workflow-editor/sample-workflow/launch") {
      assert.equal(route.request().method(), "POST");
      await route.fulfill(jsonResponse(createSampleState(true)));
      return;
    }

    if (pathname === "/api/workflow-editor/document") {
      if (route.request().method() === "GET") {
        await route.fulfill(jsonResponse(workflowDocument));
        return;
      }

      assert.equal(route.request().method(), "PUT");
      const body = route.request().postDataJSON();
      saveRequests.push(body);
      assert.equal(body.expected_revision, "rev-1");
      assert.equal(body.config.triggers.length, 1);
      assert.equal(body.config.gates.length, 1);
      assert.equal(body.config.effects.length, 1);
      assert.equal(body.config.queries.length, 1);
      assert.equal(body.config.triggers[0].id, "trigger-new");
      assert.equal(body.config.triggers[0].display_name, "Incoming ticket");
      assert.equal(body.config.gates[0].condition, "${{trigger-new.ticket.title}}");
      assert.deepEqual(body.config.effects[0].payload, {
        summary: "Ticket ${{trigger-new.ticket.title}}",
        details: [
          {
            label: "Ticket id",
            value: "${{trigger-new.ticket.id}}",
          },
          {
            label: "System time",
            value: "${{system.now}}",
          },
          {
            label: "Malformed",
            value: "Review ${{trigger-old.ticket ?? {bad}}}",
          },
          {
            label: "External",
            value: "Review ${{outside-node.ticket.title}}",
          },
        ],
      });
      assert.equal(body.config.queries[0].filter, "${{trigger-new.ticket.title ?? trigger-new.ticket.id}}");
      assert.deepEqual(body.config.edges, [
        {
          source_id: "trigger-new",
          target_id: "gate-old",
          source_port: "completed",
        },
        {
          source_id: "trigger-new",
          target_id: "effect-old",
          source_port: "completed",
        },
        {
          source_id: "trigger-new",
          target_id: "query-old",
          source_port: "completed",
        },
      ]);
      assert.deepEqual(body.layout.nodes, {
        "gate-old": { x: 420, y: 60 },
        "effect-old": { x: 420, y: 220 },
        "query-old": { x: 420, y: 380 },
        "trigger-new": { x: 100, y: 120 },
      });
      assert.deepEqual(body.graphs[0].node_ids, ["trigger-new", "gate-old", "effect-old", "query-old"]);
      const rewrittenOutputSchemas = {
        "trigger-new": workflowDocument.output_schemas["trigger-old"],
      };
      await route.fulfill(
        jsonResponse({
          config: body.config,
          layout: body.layout,
          graphs: body.graphs,
          output_schemas: rewrittenOutputSchemas,
          validation_diagnostics: [],
          revision: "rev-2",
        }),
      );
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

  page.on("pageerror", (error) => {
    pageErrors.push(error);
  });
  page.on("console", (message) => {
    if (message.type() === "error") {
      consoleErrors.push(message.text());
    }
  });

  await page.goto(`http://127.0.0.1:${address.port}/#workflow`, {
    waitUntil: "domcontentloaded",
  });

  await page.waitForSelector(".canvas-shell .react-flow__pane");

  const triggerNode = page.locator('.react-flow__node[data-id="trigger-old"]');
  const triggerBox = await triggerNode.boundingBox();
  assert.ok(triggerBox, "expected trigger node bounds");
  await page.mouse.click(triggerBox.x + triggerBox.width / 2, triggerBox.y + triggerBox.height / 2);
  await page.waitForFunction(() => Boolean(document.querySelector('.react-flow__node[data-id="trigger-old"] .node-card.selected')));

  const inspector = page.locator(".sidebar-section.inspector").filter({ hasText: "Inspector" });
  await inspector.waitFor({ state: "visible" });
  const nodeIdInput = inspector.locator(".inspector-label").filter({ hasText: "Node id" }).locator("input");
  const modifierKey = process.platform === "darwin" ? "Meta" : "Control";
  await nodeIdInput.click();
  await page.keyboard.press(`${modifierKey}+A`);
  await page.keyboard.type("trigger-new");
  await page.waitForTimeout(250);
  assert.equal(await nodeIdInput.inputValue(), "trigger-new");
  assert.equal(await page.getByRole("button", { name: "Reset changes" }).count(), 1);

  const gateNode = page.locator('.react-flow__node[data-id="gate-old"]');
  const gateBox = await gateNode.boundingBox();
  assert.ok(gateBox, "expected gate node bounds");
  await gateNode.locator(".node-drag-surface").click({ force: true });
  await page.waitForTimeout(250);
  await page.waitForFunction(() => Boolean(document.querySelector('.react-flow__node[data-id="gate-old"] .node-card.selected')));
  await inspector.waitFor({ state: "visible" });
  assert.equal(await nodeIdInput.inputValue(), "gate-old");

  const problemsPanel = inspector.locator(".problems-panel");
  assert.equal(await problemsPanel.filter({ hasText: "trigger-old" }).count(), 0);
  assert.equal(await problemsPanel.filter({ hasText: "trigger-new.ticket" }).count(), 0);

  const saveRequest = page.waitForRequest(
    (request) => request.url().includes("/api/workflow-editor/document") && request.method() === "PUT",
  );
  await Promise.all([
    saveRequest,
    page.getByRole("button", { name: "Save workflow" }).click(),
  ]);

  assert.equal(saveRequests.length, 1);
  assert.deepEqual(pageErrors, []);
  assert.deepEqual(consoleErrors, []);
  assert.deepEqual(unhandledApiRoutes, []);
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/auth/session"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/admin/deployment-status"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/admin/state?directory_path=root"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/configuration/state?directory_path=root"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/workflow-editor/document"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/workflow-editor/catalog?directory_path=root"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/workflow-editor/runs"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/workflow-editor/runs/stream"));
});
