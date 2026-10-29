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
    children: [],
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

test("workflow editor shows selected-node output contracts and copies binding expressions", async (t) => {
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
    Object.defineProperty(window, "__copiedBindingText", {
      value: null,
      writable: true,
      configurable: true,
    });
    const clipboard = {
      writeText: async (text) => {
        window.__copiedBindingText = text;
      },
    };
    try {
      Object.defineProperty(window.navigator, "clipboard", {
        value: clipboard,
        configurable: true,
      });
    } catch {
      try {
        window.navigator.clipboard = clipboard;
      } catch {
        if (window.navigator.clipboard) {
          window.navigator.clipboard.writeText = clipboard.writeText;
        }
      }
    }
  });

  const sessionResponse = {
    auth_mode: "none",
    auth_required: false,
    authenticated: true,
    username: "admin",
    display_name: "Admin Operator",
    permissions: ["admin.owner"],
    effective_permissions: ["admin.owner"],
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
    permissions: ["admin.owner"],
    effective_permissions: ["admin.owner"],
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
          config: { source: "support inbox" },
        },
      ],
      gates: [
        {
          id: "gate-filter-1",
          display_name: "Filter ticket",
          type: "gate:filter",
          config: {},
        },
      ],
      effects: [
        {
          id: "effect-1",
          display_name: "Dispatch agent",
          type: "effect:agent",
          config: { prompt: "Handle ticket" },
        },
      ],
      queries: [],
      edges: [
        {
          source_id: "trigger-1",
          target_id: "gate-filter-1",
          source_port: "completed",
        },
        {
          source_id: "gate-filter-1",
          target_id: "effect-1",
          source_port: "completed",
        },
      ],
    },
    layout: {
      nodes: {
        "trigger-1": { x: 80, y: 120 },
        "gate-filter-1": { x: 360, y: 120 },
        "effect-1": { x: 640, y: 120 },
      },
    },
    graphs: [
      {
        id: "graph-1",
        name: "Selected node outputs smoke",
        description: null,
        directory_path: "root",
        user_ids: [],
        node_ids: ["trigger-1", "gate-filter-1", "effect-1"],
      },
    ],
    output_schemas: {
      "trigger-1": {
        ticket: {
          type: "object",
          properties: {
            issue_number: { type: "integer" },
            labels: {
              type: "array",
              items: {
                type: "object",
                properties: {
                  priority: { type: "string" },
                },
              },
            },
          },
        },
        status: { type: "string", enum: ["open", "closed"] },
      },
    },
    validation_diagnostics: [],
  };
  const workflowCatalog = {
    worker_id: "trigger-orchestrator",
    nodes: [
      {
        kind: "trigger",
        type: "trigger:manual",
        label: "Manual",
        fields: [
          {
            key: "source",
            label: "Source",
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
        output_ports: [{ key: "completed", label: "Completed" }],
      },
      {
        kind: "gate",
        type: "gate:filter",
        label: "Filter",
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
    runs: [],
    node_activity: {},
  };
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const { pathname } = url;
    requestedEndpoints.push(`${pathname}${url.search}`);

    if (await fulfillAdminShellBackgroundApiRequest(route, pathname)) {
      return;
    }

    if (pathname === "/api/admin/branding") {
      await route.fulfill(jsonResponse({}));
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
      if (route.request().method() === "GET") {
        await route.fulfill(jsonResponse(workflowDocument));
        return;
      }
      await route.fulfill(jsonResponse({ error: `Unexpected ${route.request().method()} ${pathname}` }, 405));
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

  attachPageListeners(page, pageErrors, consoleErrors);

  await page.goto(`http://127.0.0.1:${address.port}/#workflow`, {
    waitUntil: "domcontentloaded",
  });

  await page.waitForSelector(".canvas-shell .react-flow__pane");

  const triggerNode = page.locator('.react-flow__node[data-id="trigger-1"]');
  await triggerNode.click();

  await page.getByRole("tab", { name: "Outputs", exact: true }).click();
  const triggerOutputPanel = page.locator(".output-contract-panel");
  await triggerOutputPanel.waitFor({ state: "visible" });
  await assert.equal(
    await triggerOutputPanel.getByText("Pass-through gate: outputs are derived from trigger-1.").count(),
    0,
  );
  await assert.equal(
    await triggerOutputPanel.getByText("Outputs are declared by backend schema or node.outputs.").count(),
    1,
  );
  await assert.equal(await triggerOutputPanel.getByText("trigger-1.ticket.issue_number").count(), 1);
  await assert.equal(await triggerOutputPanel.getByText("ticket.labels.0.priority").count(), 1);
  await assert.equal(await triggerOutputPanel.getByText("integer").count(), 1);

  const triggerCopyButton = triggerOutputPanel.getByRole("button", {
    name: "Copy binding expression for trigger-1.ticket.issue_number",
  });
  await triggerCopyButton.click();
  await page.waitForFunction(
    (expectedBindingExpression) => window.__copiedBindingText === expectedBindingExpression,
    "${{trigger-1.ticket.issue_number}}",
  );
  assert.equal(
    await page.evaluate(() => window.__copiedBindingText),
    "${{trigger-1.ticket.issue_number}}",
  );
  await assert.equal(
    await triggerOutputPanel.getByRole("status").filter({ hasText: "Copied ${{trigger-1.ticket.issue_number}}." }).count(),
    1,
  );

  const filterNode = page.locator('.react-flow__node[data-id="gate-filter-1"]');
  await filterNode.click();

  const filterOutputPanel = page.locator(".output-contract-panel");
  await filterOutputPanel.waitFor({ state: "visible" });
  await assert.equal(
    await filterOutputPanel.getByText("Pass-through gate: outputs are derived from trigger-1.").count(),
    1,
  );
  await assert.equal(await filterOutputPanel.getByText("gate-filter-1.ticket.issue_number").count(), 1);
  await assert.equal(await filterOutputPanel.getByText("gate-filter-1.ticket.labels.0.priority").count(), 1);

  const filterCopyButton = filterOutputPanel.getByRole("button", {
    name: "Copy binding expression for gate-filter-1.ticket.issue_number",
  });
  await filterCopyButton.click();
  await page.waitForFunction(
    (expectedBindingExpression) => window.__copiedBindingText === expectedBindingExpression,
    "${{gate-filter-1.ticket.issue_number}}",
  );
  assert.equal(
    await page.evaluate(() => window.__copiedBindingText),
    "${{gate-filter-1.ticket.issue_number}}",
  );

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
