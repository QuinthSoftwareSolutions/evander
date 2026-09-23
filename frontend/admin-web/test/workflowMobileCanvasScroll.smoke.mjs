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

function touchPoint(id, x, y) {
  return {
    id,
    x,
    y,
    radiusX: 8,
    radiusY: 8,
    force: 1,
  };
}

async function dispatchTouch(client, type, points) {
  await client.send("Input.dispatchTouchEvent", {
    type,
    touchPoints: points.map((point) => touchPoint(point.id, point.x, point.y)),
  });
}

async function dragTouch(client, startPoints, delta, steps = 6) {
  await dispatchTouch(client, "touchStart", startPoints);
  for (let step = 1; step <= steps; step += 1) {
    const fraction = step / steps;
    await dispatchTouch(
      client,
      "touchMove",
      startPoints.map((point) => ({
        id: point.id,
        x: point.x + delta.x * fraction,
        y: point.y + delta.y * fraction,
      })),
    );
  }
  await dispatchTouch(client, "touchEnd", []);
}

async function readCanvasState(page) {
  return page.evaluate(() => {
    const viewport = document.querySelector(".react-flow__viewport");
    return {
      scrollY: window.scrollY,
      transform: viewport ? getComputedStyle(viewport).transform : null,
    };
  });
}

async function positionCanvasForTouch(page) {
  await page.locator(".canvas-shell").scrollIntoViewIfNeeded();
  await page.evaluate(() => {
    window.scrollBy(0, 160);
  });
}

async function canvasBackgroundTouchPoint(page) {
  const point = await page.locator(".canvas-shell").evaluate((canvas) => {
    const rect = canvas.getBoundingClientRect();
    // The fitted nodes move when the toolbar changes height. Start the page-scroll
    // gesture on background, keeping its full path inside the visible viewport.
    for (let y = Math.max(rect.top + 30, 30); y < Math.min(rect.bottom - 200, innerHeight - 200); y += 30) {
      for (const fraction of [0.9, 0.1, 0.5]) {
        const x = rect.left + rect.width * fraction;
        if (document.elementFromPoint(x, y)?.classList.contains("react-flow__pane")) return { x, y };
      }
    }
    return null;
  });
  assert.ok(point, "expected visible canvas background for a page-scroll gesture");
  return point;
}

test("workflow editor compact canvas lets one-finger touch scroll the page and two-finger touch pan the graph", async (t) => {
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
  const context = await browser.newContext({
    viewport: { width: 390, height: 740 },
    isMobile: true,
    hasTouch: true,
    deviceScaleFactor: 2,
  });
  const page = await context.newPage();
  const cdp = await context.newCDPSession(page);

  const requestedEndpoints = [];
  const pageErrors = [];
  const consoleErrors = [];
  const notFoundResponses = [];

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
    directory_scoped_config_enabled: false,
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
    directory_scoped_config_enabled: false,
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
      data: {
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
      },
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
          type: "effect:notify",
          config: {},
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
        name: "Mobile canvas scroll",
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
        type: "effect:notify",
        label: "Notify",
        fields: [],
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
  let savedWorkflowPayload = null;
  let validatedWorkflowPayload = null;

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

      assert.equal(route.request().method(), "PUT");
      savedWorkflowPayload = route.request().postDataJSON();
      assert.ok(savedWorkflowPayload, "expected a workflow save payload");
      assert.equal(savedWorkflowPayload.expected_revision, "rev-1");
      await route.fulfill(
        jsonResponse({
          config: savedWorkflowPayload.config,
          layout: savedWorkflowPayload.layout,
          graphs: savedWorkflowPayload.graphs,
          revision: "rev-2",
          validation_diagnostics: [],
          output_schemas: {},
        }),
      );
      return;
    }

    if (pathname === "/api/workflow-editor/validate") {
      assert.equal(route.request().method(), "POST");
      validatedWorkflowPayload = route.request().postDataJSON();
      assert.ok(validatedWorkflowPayload, "expected a workflow validation payload");
      await route.fulfill(
        jsonResponse({
          config: validatedWorkflowPayload.config,
          layout: validatedWorkflowPayload.layout,
          graphs: validatedWorkflowPayload.graphs,
          validation_diagnostics: [],
          output_schemas: {},
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

    await route.fulfill(jsonResponse({ error: `Unhandled ${pathname}` }, 404));
  });

  page.on("pageerror", (error) => {
    pageErrors.push(error);
  });
  page.on("console", (message) => {
    if (message.text().includes("Ignored attempt to cancel a touchmove event with cancelable=false")) {
      return;
    }
    if (message.text().includes("Failed to load resource: the server responded with a status of 404")) {
      return;
    }
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

  const canvasPane = page.locator(".canvas-shell .react-flow__pane");
  await assert.doesNotReject(() => canvasPane.waitFor({ state: "visible" }));
  const workflowTools = page.getByLabel("Workflow tools");
  await assert.doesNotReject(() => workflowTools.waitFor({ state: "visible" }));
  assert.equal(
    await page.locator(".sidebar").evaluate((element) => getComputedStyle(element).display),
    "none",
    "compact workflow layout should hide the desktop sidebar instead of stacking it above the canvas",
  );
  await positionCanvasForTouch(page);

  const canvasBox = await page.locator(".canvas-shell").boundingBox();
  assert.ok(canvasBox, "expected canvas shell bounds");
  assert.ok(canvasBox.width > 0 && canvasBox.height > 0, "expected canvas shell to have size");

  const oneFingerStart = await canvasBackgroundTouchPoint(page);
  const oneFingerBefore = await readCanvasState(page);
  await dragTouch(
    cdp,
    [
      {
        id: 1,
        x: oneFingerStart.x,
        y: oneFingerStart.y,
      },
    ],
    { x: 0, y: 180 },
  );
  await page.waitForFunction(
    (beforeScrollY) => window.scrollY < beforeScrollY,
    oneFingerBefore.scrollY,
  );
  const oneFingerAfter = await readCanvasState(page);

  assert.ok(
    oneFingerAfter.scrollY < oneFingerBefore.scrollY,
    `expected page scroll to move toward the top, before=${oneFingerBefore.scrollY}, after=${oneFingerAfter.scrollY}`,
  );
  assert.equal(
    oneFingerAfter.transform,
    oneFingerBefore.transform,
    "one-finger touch should not change the ReactFlow viewport transform",
  );

  await workflowTools.getByRole("button", { name: "Pan", exact: true }).click();
  await page.locator(".workflow-editor-shell.compact-mode-pan").waitFor({ state: "visible" });
  await positionCanvasForTouch(page);
  const panModeBox = await page.locator(".canvas-shell").boundingBox();
  assert.ok(panModeBox, "expected canvas shell bounds for pan mode");
  const panModeStart = {
    x: panModeBox.x + panModeBox.width * 0.66,
    y: panModeBox.y + panModeBox.height * 0.56,
  };
  const panModeBefore = await readCanvasState(page);
  await dragTouch(
    cdp,
    [
      {
        id: 1,
        x: panModeStart.x,
        y: panModeStart.y,
      },
    ],
    { x: -130, y: 0 },
  );
  await page.waitForFunction(
    (beforeTransform) => {
      const viewport = document.querySelector(".react-flow__viewport");
      return Boolean(viewport) && getComputedStyle(viewport).transform !== beforeTransform;
    },
    panModeBefore.transform,
  );
  const panModeAfter = await readCanvasState(page);
  assert.ok(
    Math.abs(panModeAfter.scrollY - panModeBefore.scrollY) <= 24,
    `expected page scroll to stay stable in pan mode, before=${panModeBefore.scrollY}, after=${panModeAfter.scrollY}`,
  );
  assert.notEqual(
    panModeAfter.transform,
    panModeBefore.transform,
    "one-finger touch should pan the graph when Pan mode is active",
  );

  await workflowTools.getByRole("button", { name: "Select", exact: true }).click();
  await page.locator(".workflow-editor-shell.compact-mode-select").waitFor({ state: "visible" });
  await positionCanvasForTouch(page);
  const twoFingerBox = await page.locator(".canvas-shell").boundingBox();
  assert.ok(twoFingerBox, "expected canvas shell bounds for two-finger gesture");

  const twoFingerStart = {
    x: twoFingerBox.x + twoFingerBox.width * 0.66,
    y: twoFingerBox.y + twoFingerBox.height * 0.62,
  };
  const twoFingerBefore = await readCanvasState(page);
  await dragTouch(
    cdp,
    [
      {
        id: 1,
        x: twoFingerStart.x - 28,
        y: twoFingerStart.y,
      },
      {
        id: 2,
        x: twoFingerStart.x + 28,
        y: twoFingerStart.y,
      },
    ],
    { x: -140, y: 0 },
  );
  await page.waitForFunction(
    (beforeTransform) => {
      const viewport = document.querySelector(".react-flow__viewport");
      return Boolean(viewport) && getComputedStyle(viewport).transform !== beforeTransform;
    },
    twoFingerBefore.transform,
  );
  const twoFingerAfter = await readCanvasState(page);

  assert.ok(
    Math.abs(twoFingerAfter.scrollY - twoFingerBefore.scrollY) <= 24,
    `expected page scroll to stay stable, before=${twoFingerBefore.scrollY}, after=${twoFingerAfter.scrollY}`,
  );
  assert.notEqual(
    twoFingerAfter.transform,
    twoFingerBefore.transform,
    "two-finger touch should change the ReactFlow viewport transform",
  );

  await page.reload({ waitUntil: "domcontentloaded" });
  await assert.doesNotReject(() => page.locator(".canvas-shell .react-flow__pane").waitFor({ state: "visible" }));
  await positionCanvasForTouch(page);

  const workflowNode = page.locator(".react-flow__node", { hasText: "Incoming command" });
  await assert.doesNotReject(() => workflowNode.waitFor({ state: "visible" }));
  const nodeBefore = await workflowNode.boundingBox();
  assert.ok(nodeBefore, "expected the trigger node to have bounds");

  const nodeDragStart = {
    x: nodeBefore.x + nodeBefore.width / 2,
    y: nodeBefore.y + nodeBefore.height / 2,
  };
  await dragTouch(
    cdp,
    [
      {
        id: 1,
        x: nodeDragStart.x,
        y: nodeDragStart.y,
      },
    ],
    { x: 90, y: 70 },
  );

  await page.waitForFunction(
    ({ beforeLeft, beforeTop, beforeWidth, beforeHeight }) => {
      const node = Array.from(document.querySelectorAll(".react-flow__node")).find((element) =>
        element.textContent?.includes("Incoming command"),
      );
      if (!node) return false;
      const rect = node.getBoundingClientRect();
      return (
        Math.abs(rect.left - beforeLeft) > 1 ||
        Math.abs(rect.top - beforeTop) > 1 ||
        Math.abs(rect.width - beforeWidth) > 1 ||
        Math.abs(rect.height - beforeHeight) > 1
      );
    },
    {
      beforeLeft: nodeBefore.x,
      beforeTop: nodeBefore.y,
      beforeWidth: nodeBefore.width,
      beforeHeight: nodeBefore.height,
    },
    { timeout: 5000 },
  );

  const nodeAfter = await workflowNode.boundingBox();
  assert.ok(nodeAfter, "expected the trigger node to keep bounds after drag");
  assert.notDeepEqual(
    {
      x: nodeAfter.x,
      y: nodeAfter.y,
    },
    {
      x: nodeBefore.x,
      y: nodeBefore.y,
    },
    "single-touch drag on an unlocked node should move that node",
  );

  await workflowTools.getByRole("button", { name: "Create", exact: true }).click();
  const createSheet = page.locator(".compact-workflow-sheet.panel-create");
  await assert.doesNotReject(() => createSheet.waitFor({ state: "visible" }));
  await createSheet.getByLabel("Search nodes").fill("notify");
  await createSheet.getByRole("button", { name: "Notify" }).click();
  const createdNode = page.locator('.react-flow__node[data-id="effect-notify"]');
  await assert.doesNotReject(() => createdNode.waitFor({ state: "visible" }));

  await workflowTools.getByRole("button", { name: "Inspector", exact: true }).click();
  const inspectorSheet = page.locator(".compact-workflow-sheet.panel-inspector");
  await assert.doesNotReject(() => inspectorSheet.waitFor({ state: "visible" }));
  await inspectorSheet.getByLabel("Display name").fill("Mobile notify");
  await assert.doesNotReject(() => page.locator(".react-flow__node", { hasText: "Mobile notify" }).waitFor({ state: "visible" }));
  await inspectorSheet.getByRole("button", { name: "Close" }).click();
  await assert.doesNotReject(() => inspectorSheet.waitFor({ state: "hidden" }));

  await workflowTools.getByRole("button", { name: "Connect", exact: true }).click();
  await page.locator(".react-flow__node", { hasText: "Incoming command" }).click();
  await assert.doesNotReject(() => page.locator(".pending-connection-layer line").waitFor({ state: "attached" }));
  await page.locator(".react-flow__node", { hasText: "Mobile notify" }).click();
  await page.waitForFunction(() => !document.querySelector(".pending-connection-layer line"));

  await workflowTools.getByRole("button", { name: "Save", exact: true }).click();
  const saveSheet = page.locator(".compact-workflow-sheet.panel-save");
  await assert.doesNotReject(() => saveSheet.waitFor({ state: "visible" }));
  await assert.doesNotReject(() => saveSheet.getByText("Unsaved workflow changes").waitFor({ state: "visible" }));

  const validateRequest = page.waitForRequest(
    (request) => request.url().includes("/api/workflow-editor/validate") && request.method() === "POST",
  );
  await saveSheet.getByRole("button", { name: "Validate" }).click();
  await validateRequest;

  const saveRequest = page.waitForRequest(
    (request) => request.url().includes("/api/workflow-editor/document") && request.method() === "PUT",
  );
  await saveSheet.getByRole("button", { name: "Save workflow" }).click();
  await saveRequest;

  assert.ok(validatedWorkflowPayload, "expected workflow validation request to be captured");
  assert.ok(savedWorkflowPayload, "expected workflow save request to be captured");
  assert.notDeepEqual(
    savedWorkflowPayload.layout.nodes["trigger-1"],
    workflowDocument.layout.nodes["trigger-1"],
    "saving after a node drag should send the updated node position",
  );
  assert.equal(typeof savedWorkflowPayload.layout.nodes["trigger-1"].x, "number");
  assert.equal(typeof savedWorkflowPayload.layout.nodes["trigger-1"].y, "number");
  assert.ok(savedWorkflowPayload.layout.nodes["effect-notify"], "expected the created node layout to be saved");
  assert.ok(
    savedWorkflowPayload.config.effects.some(
      (effect) =>
        effect.id === "effect-notify" &&
        effect.type === "effect:notify" &&
        effect.display_name === "Mobile notify",
    ),
    "expected the inspector edit for the created node to be saved",
  );
  assert.ok(
    savedWorkflowPayload.config.edges.some(
      (edge) =>
        edge.source_id === "trigger-1" &&
        edge.target_id === "effect-notify" &&
        edge.source_port === "completed",
    ),
    "expected compact Connect mode to save an edge from the trigger to the created node",
  );

  assert.deepEqual(pageErrors, []);
  assert.deepEqual(consoleErrors, []);
  assert.deepEqual(notFoundResponses, []);
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/admin/branding"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/auth/session"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/admin/deployment-status"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/admin/state?directory_path=root"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/admin/user-preferences/workflow-tutorial-progress"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/configuration/state?directory_path=root"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/workflow-editor/document"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/workflow-editor/validate"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/workflow-editor/catalog?directory_path=root"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/workflow-editor/runs"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/workflow-editor/runs/stream"));
});
