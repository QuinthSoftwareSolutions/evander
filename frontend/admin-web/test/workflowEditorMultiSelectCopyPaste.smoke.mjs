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

function collectWorkflowNodes(config) {
  return [
    ...(config.triggers ?? []),
    ...(config.gates ?? []),
    ...(config.effects ?? []),
    ...(config.queries ?? []),
  ];
}

async function screenPointToFlowPosition(page, point) {
  return page.evaluate(({ clientX, clientY }) => {
    const wrapper = document.querySelector(".react-flow");
    const viewport = document.querySelector(".react-flow__viewport");
    if (!wrapper || !viewport) {
      throw new Error("expected React Flow wrapper and viewport");
    }
    const wrapperRect = wrapper.getBoundingClientRect();
    const transform = getComputedStyle(viewport).transform;
    const matrix = transform === "none" ? new DOMMatrixReadOnly() : new DOMMatrixReadOnly(transform);
    return {
      x: (clientX - wrapperRect.left - matrix.e) / matrix.a,
      y: (clientY - wrapperRect.top - matrix.f) / matrix.d,
    };
  }, point);
}

async function workflowNodeFlowBounds(page, nodeId) {
  return page.locator(`.react-flow__node[data-id="${nodeId}"]`).evaluate((element) => {
    const wrapper = document.querySelector(".react-flow");
    const viewport = document.querySelector(".react-flow__viewport");
    if (!wrapper || !viewport) {
      throw new Error("expected React Flow wrapper and viewport");
    }
    const wrapperRect = wrapper.getBoundingClientRect();
    const nodeRect = element.getBoundingClientRect();
    const transform = getComputedStyle(viewport).transform;
    const matrix = transform === "none" ? new DOMMatrixReadOnly() : new DOMMatrixReadOnly(transform);
    return {
      left: (nodeRect.left - wrapperRect.left - matrix.e) / matrix.a,
      top: (nodeRect.top - wrapperRect.top - matrix.f) / matrix.d,
      right: (nodeRect.right - wrapperRect.left - matrix.e) / matrix.a,
      bottom: (nodeRect.bottom - wrapperRect.top - matrix.f) / matrix.d,
    };
  });
}

function selectionCenter(bounds) {
  return {
    x: (Math.min(...bounds.map((bound) => bound.left)) + Math.max(...bounds.map((bound) => bound.right))) / 2,
    y: (Math.min(...bounds.map((bound) => bound.top)) + Math.max(...bounds.map((bound) => bound.bottom))) / 2,
  };
}

function assertNear(actual, expected, message, tolerance = 3) {
  assert.ok(
    Math.abs(actual - expected) <= tolerance,
    `${message}: expected ${expected}, got ${actual}`,
  );
}

test("workflow editor modifier-click multi-selects nodes and copy/paste preserves the selected edge", async (t) => {
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
  let expectedPastedPositions = null;

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
          display_name: "Incoming command",
          type: "trigger:manual",
          config: {},
        },
      ],
      gates: [],
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
        name: "Selection smoke",
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

    if (pathname === "/api/admin/branding") {
      await route.fulfill(jsonResponse({}));
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
      if (route.request().method() === "GET") {
        await route.fulfill(jsonResponse(workflowDocument));
        return;
      }

      assert.equal(route.request().method(), "PUT");
      const body = route.request().postDataJSON();
      const nodes = collectWorkflowNodes(body.config);
      assert.equal(body.expected_revision, "rev-1");
      assert.equal(nodes.length, 4);
      assert.equal(body.config.triggers.length, 2);
      assert.equal(body.config.effects.length, 2);
      assert.equal(body.config.edges.length, 2);
      assert.ok(nodes.some((node) => node.id === "trigger-1"));
      assert.ok(nodes.some((node) => node.id === "effect-1"));

      const copiedTrigger = nodes.find(
        (node) => node.type === "trigger:manual" && node.display_name === "Incoming command Copy",
      );
      const copiedEffect = nodes.find(
        (node) => node.type === "effect:agent" && node.display_name === "Dispatch agent Copy",
      );
      assert.ok(copiedTrigger, "expected a copied trigger node");
      assert.ok(copiedEffect, "expected a copied effect node");
      assert.ok(
        body.config.edges.some(
          (edge) =>
            edge.source_id === copiedTrigger.id &&
            edge.target_id === copiedEffect.id &&
            edge.source_port === "completed",
        ),
        "expected the copied edge to connect the copied trigger and copied effect",
      );
      assert.ok(expectedPastedPositions, "expected cursor-centered paste positions to be computed");
      const copiedTriggerPosition = body.layout.nodes[copiedTrigger.id];
      const copiedEffectPosition = body.layout.nodes[copiedEffect.id];
      assert.ok(copiedTriggerPosition, "expected copied trigger layout position");
      assert.ok(copiedEffectPosition, "expected copied effect layout position");
      assertNear(copiedTriggerPosition.x, expectedPastedPositions.trigger.x, "copied trigger x should follow cursor paste");
      assertNear(copiedTriggerPosition.y, expectedPastedPositions.trigger.y, "copied trigger y should follow cursor paste");
      assertNear(copiedEffectPosition.x, expectedPastedPositions.effect.x, "copied effect x should follow cursor paste");
      assertNear(copiedEffectPosition.y, expectedPastedPositions.effect.y, "copied effect y should follow cursor paste");

      await route.fulfill(
        jsonResponse({
          config: body.config,
          layout: body.layout,
          graphs: body.graphs,
          output_schemas: {},
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

  const modifierKey = process.platform === "darwin" ? "Meta" : "Control";
  const triggerNode = page.locator('.react-flow__node[data-id="trigger-1"]');
  const effectNode = page.locator('.react-flow__node[data-id="effect-1"]');
  await triggerNode.waitFor({ state: "visible" });
  await effectNode.waitFor({ state: "visible" });
  await triggerNode.click();
  await page.waitForFunction(() =>
    Boolean(document.querySelector('.react-flow__node[data-id="trigger-1"] .node-card.selected')),
  );
  await effectNode.click({ modifiers: [modifierKey] });

  await page.waitForFunction(() => {
    const triggerSelected = document.querySelector('.react-flow__node[data-id="trigger-1"] .node-card.selected');
    const effectSelected = document.querySelector('.react-flow__node[data-id="effect-1"] .node-card.selected');
    return Boolean(triggerSelected && effectSelected);
  });

  const selectionPanel = page.locator(".sidebar-section.inspector").filter({ hasText: "Selection" });
  await selectionPanel.waitFor({ state: "visible" });
  assert.equal(await selectionPanel.getByText("2 nodes selected").count(), 1);

  await page.locator(".app-shell.workflow-editor-shell").focus();
  await page.keyboard.press(`${modifierKey}+C`);
  const canvasBox = await page.locator(".canvas-shell").boundingBox();
  assert.ok(canvasBox, "expected canvas shell bounds");
  const pasteCursor = {
    clientX: canvasBox.x + canvasBox.width * 0.68,
    clientY: canvasBox.y + canvasBox.height * 0.57,
  };
  const [pasteTargetFlow, triggerFlowBounds, effectFlowBounds] = await Promise.all([
    screenPointToFlowPosition(page, pasteCursor),
    workflowNodeFlowBounds(page, "trigger-1"),
    workflowNodeFlowBounds(page, "effect-1"),
  ]);
  const copiedCenter = selectionCenter([triggerFlowBounds, effectFlowBounds]);
  const pasteDelta = {
    x: pasteTargetFlow.x - copiedCenter.x,
    y: pasteTargetFlow.y - copiedCenter.y,
  };
  expectedPastedPositions = {
    trigger: {
      x: workflowDocument.layout.nodes["trigger-1"].x + pasteDelta.x,
      y: workflowDocument.layout.nodes["trigger-1"].y + pasteDelta.y,
    },
    effect: {
      x: workflowDocument.layout.nodes["effect-1"].x + pasteDelta.x,
      y: workflowDocument.layout.nodes["effect-1"].y + pasteDelta.y,
    },
  };
  await page.mouse.move(pasteCursor.clientX, pasteCursor.clientY);
  await page.keyboard.press(`${modifierKey}+V`);

  await page.waitForFunction(() => {
    const titles = Array.from(document.querySelectorAll(".node-title")).map((element) => element.textContent?.trim());
    return titles.includes("Incoming command Copy") && titles.includes("Dispatch agent Copy");
  });

  const saveRequest = page.waitForRequest(
    (request) => request.url().includes("/api/workflow-editor/document") && request.method() === "PUT",
  );
  await Promise.all([
    saveRequest,
    page.getByRole("button", { name: "Save workflow" }).click(),
  ]);

  assert.deepEqual(pageErrors, []);
  assert.deepEqual(consoleErrors, []);
  assert.deepEqual(unhandledApiRoutes, []);
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/auth/session"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/admin/deployment-status"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/admin/state?directory_path=root"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/configuration/state?directory_path=root"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/admin/user-preferences/workflow-tutorial-progress"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/workflow-editor/document"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/workflow-editor/catalog?directory_path=root"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/workflow-editor/runs"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/workflow-editor/runs/stream"));
});
