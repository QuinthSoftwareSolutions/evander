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
    directory_scoped_config_enabled: false,
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
        },
      ],
      gates: [],
      effects: [
        {
          id: "effect-1",
          display_name: "Dispatch agent",
          type: "effect:notify",
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
        directory_paths: ["root"],
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
  await positionCanvasForTouch(page);

  const canvasBox = await page.locator(".canvas-shell").boundingBox();
  assert.ok(canvasBox, "expected canvas shell bounds");
  assert.ok(canvasBox.width > 0 && canvasBox.height > 0, "expected canvas shell to have size");

  const oneFingerStart = {
    x: canvasBox.x + canvasBox.width * 0.78,
    y: canvasBox.y + canvasBox.height * 0.58,
  };
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
    Math.abs(twoFingerAfter.scrollY - twoFingerBefore.scrollY) <= 4,
    `expected page scroll to stay stable, before=${twoFingerBefore.scrollY}, after=${twoFingerAfter.scrollY}`,
  );
  assert.notEqual(
    twoFingerAfter.transform,
    twoFingerBefore.transform,
    "two-finger touch should change the ReactFlow viewport transform",
  );

  assert.deepEqual(pageErrors, []);
  assert.deepEqual(consoleErrors, []);
  assert.deepEqual(notFoundResponses, []);
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/auth/session"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/admin/deployment-status"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/admin/state?directory_path=root"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/configuration/state?directory_path=root"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/workflow-editor/document"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/workflow-editor/catalog?directory_path=root"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/workflow-editor/runs"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/workflow-editor/runs/stream"));
});
