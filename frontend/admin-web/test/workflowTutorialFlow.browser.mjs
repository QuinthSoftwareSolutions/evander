import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import path from "node:path";
import test from "node:test";

import { createServer } from "vite";
import {
  browserHookTimeout, browserTestTimeout, suiteBrowser, withTimeout,
} from "./support/browserHarness.mjs";

const browser = suiteBrowser();

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

async function dispatchWorkflowNodeMouseEvent(page, nodeId, eventType) {
  await page.locator(`.react-flow__node[data-id="${nodeId}"]`).evaluate((element, type) => {
    const canvas = element.closest(".canvas-shell") ?? document.body;
    const rect = canvas.getBoundingClientRect();
    const clientX = Math.max(rect.left + 24, Math.min(rect.right - 24, rect.left + rect.width / 2));
    const clientY = Math.max(rect.top + 24, Math.min(rect.bottom - 24, rect.top + rect.height / 2));
    const button = type === "contextmenu" ? 2 : 0;
    element.dispatchEvent(new MouseEvent(type, {
      bubbles: true,
      cancelable: true,
      button,
      buttons: button === 2 ? 2 : 1,
      clientX,
      clientY,
      view: window,
    }));
  }, eventType);
}

async function clickNodeCenter(page, nodeLocator, options = {}) {
  const box = await nodeLocator.boundingBox();
  assert.ok(box);
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2, options);
}

async function waitForCondition(predicate, message) {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  assert.fail(message);
}

test("workflow editor tutorial chooser starts, skips, and exits tutorials", { timeout: browserTestTimeout }, async (t) => {
  const projectRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
  const server = await createServer({
    root: projectRoot,
    server: {
      host: "127.0.0.1",
      port: 0,
    },
    logLevel: "error",
  });
  t.after(() => server.close(), { timeout: browserHookTimeout });
  await withTimeout(server.listen(), 15_000, "Vite did not start within 15 seconds");
  const address = server.httpServer?.address();
  assert.ok(address && typeof address === "object" && "port" in address, "expected Vite to bind an ephemeral port");
  const context = await browser.newContext(t, { viewport: { width: 1440, height: 1200 } });
  const page = await context.newPage();

  const requestedEndpoints = [];
  const documentPutRequests = [];
  const tutorialProgressPutRequests = [];
  let tutorialProgress = {
    completed_tutorial_ids: [],
    skipped_tutorial_ids: [],
  };
  const pageErrors = [];
  const consoleErrors = [];

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
          config: {}
        },
      ],
      gates: [],
      effects: [
        {
          id: "effect-1",
          display_name: "Dispatch agent",
          type: "effect:agent",
          config: {
            prompt: "Handle ticket"
          }
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
        name: "Workflow smoke",
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

    if (pathname === "/api/auth/session") {
      await route.fulfill(jsonResponse(sessionResponse));
      return;
    }

    if (pathname.startsWith("/api/admin/branding/assets/")) {
      await route.fulfill({ status: 204, body: "" });
      return;
    }

    if (pathname === "/api/admin/branding") {
      await route.fulfill(jsonResponse({}));
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

    if (pathname === "/api/admin/user-notifications") {
      await route.fulfill(
        jsonResponse({
          user_id: "admin",
          unread_count: 0,
          notifications: [],
        }),
      );
      return;
    }

    if (pathname === "/api/admin/user-preferences/workflow-tutorial-progress") {
      if (route.request().method() === "PUT") {
        const payload = JSON.parse(route.request().postData() ?? "{}");
        tutorialProgressPutRequests.push(payload);
        tutorialProgress = {
          completed_tutorial_ids: Array.isArray(payload.completed_tutorial_ids) ? payload.completed_tutorial_ids : [],
          skipped_tutorial_ids: Array.isArray(payload.skipped_tutorial_ids) ? payload.skipped_tutorial_ids : [],
        };
      }
      await route.fulfill(jsonResponse(tutorialProgress));
      return;
    }

    if (pathname === "/api/configuration/state") {
      assert.equal(url.searchParams.get("directory_path"), "root");
      await route.fulfill(jsonResponse(configurationStateResponse));
      return;
    }

    if (pathname === "/api/workflow-editor/document") {
      if (route.request().method() === "PUT") {
        documentPutRequests.push({
          method: route.request().method(),
          body: route.request().postData() ?? "",
        });
      }
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

  await page.goto(`http://127.0.0.1:${address.port}/#workflow`, {
    waitUntil: "domcontentloaded",
  });

  await page.waitForSelector(".workflow-tutorial-launcher");
  await assert.equal(await page.getByRole("button", { name: "Tutorials" }).count(), 1);
  await assert.equal(await page.getByRole("button", { name: ["Sample", "run"].join(" ") }).count(), 0);

  const tutorialsButton = page.getByRole("button", { name: "Tutorials" });
  const initialTutorialsButtonClass = (await tutorialsButton.getAttribute("class")) ?? "";
  assert.match(initialTutorialsButtonClass, /needs-attention/);
  assert.doesNotMatch(initialTutorialsButtonClass, /is-complete/);
  await tutorialsButton.click();
  const tutorialChooser = page.getByRole("dialog", { name: "Tutorials" });
  await tutorialChooser.waitFor({ state: "visible" });
  await page.getByText("First steps", { exact: true }).waitFor({ state: "visible" });
  await page.getByText("Learn the basics of selecting, adding, connecting, and binding workflow nodes.", { exact: true }).waitFor({
    state: "visible",
  });
  await page.getByRole("button", { name: "Start" }).waitFor({ state: "visible" });
  await page.getByRole("button", { name: "Skip" }).click();
  await page.waitForFunction(() => !document.querySelector(".workflow-tutorial-chooser-backdrop"));
  await page.waitForFunction(() => !document.querySelector(".workflow-tutorial-bubble"));
  await page.waitForFunction(() => document.querySelector(".workflow-tutorial-launcher-button.is-complete") !== null);
  await waitForCondition(
    () => tutorialProgressPutRequests.some((payload) =>
      Array.isArray(payload.skipped_tutorial_ids) &&
      payload.skipped_tutorial_ids.includes("first-steps") &&
      Array.isArray(payload.completed_tutorial_ids) &&
      payload.completed_tutorial_ids.length === 0,
    ),
    "expected skipped tutorial progress to be saved",
  );
  assert.match((await tutorialsButton.getAttribute("class")) ?? "", /is-complete/);
  assert.doesNotMatch((await tutorialsButton.getAttribute("class")) ?? "", /needs-attention/);

  await tutorialsButton.click();
  await tutorialChooser.waitFor({ state: "visible" });
  await page.getByText("Skipped", { exact: true }).waitFor({ state: "visible" });
  await page.getByRole("button", { name: "Restart" }).click();
  await page.getByRole("dialog", { name: "Welcome" }).waitFor({ state: "visible" });
  await page.getByRole("button", { name: "Next" }).click();
  await page.getByRole("dialog", { name: "Select the trigger" }).waitFor({ state: "visible" });
  await dispatchWorkflowNodeMouseEvent(page, "tutorial-trigger", "click");
  await page.getByRole("dialog", { name: "Trigger entrypoints" }).waitFor({ state: "visible" });
  await page.getByRole("button", { name: "Next" }).click();
  await page.getByRole("dialog", { name: "Add a downstream node" }).waitFor({ state: "visible" });
  await page.getByRole("button", { name: "Prompt agent" }).click();
  await page.getByRole("dialog", { name: "Connect the nodes" }).waitFor({ state: "visible" });
  const triggerNode = page.locator('.react-flow__node[data-id="tutorial-trigger"]');
  const nodeContextMenu = page.locator(".node-context-menu");
  await clickNodeCenter(page, triggerNode, { button: "right" });
  await nodeContextMenu.waitFor({ state: "visible" });
  await page.getByRole("menuitem", { name: "Completed" }).click();
  await page.waitForFunction(() => document.querySelector(".pending-connection-line") !== null);
  const effectNode = page.locator('.react-flow__node[data-id="tutorial-effect"]');
  await effectNode.click();
  await page.getByRole("dialog", { name: "Bind the prompt" }).waitFor({ state: "visible" });
  const tutorialInspector = page.locator(".sidebar-section.inspector").filter({ hasText: "tutorial-effect" });
  await tutorialInspector.getByText("Prompt", { exact: true }).waitFor({ state: "visible" });
  assert.equal(await tutorialInspector.locator("textarea").first().isEnabled(), true);
  const promptField = tutorialInspector.locator(".inspector-field").filter({
    has: page.locator(".field-header").getByText("Prompt", { exact: true }),
  }).first();
  await promptField.locator(".mode-select select").selectOption("bind");
  const finishDialog = page.getByRole("dialog", { name: "Finish" });
  if (await finishDialog.isVisible().catch(() => false)) {
    await finishDialog.waitFor({ state: "visible" });
  } else {
    const bindingSelect = promptField.locator(".binding-editor select");
    await bindingSelect.waitFor({ state: "visible" });
    await bindingSelect.selectOption("${{tutorial-trigger.completed}}");
    await finishDialog.waitFor({ state: "visible" });
  }
  tutorialProgressPutRequests.length = 0;
  await page.getByRole("button", { name: "Exit" }).click();
  await page.waitForFunction(() => !document.querySelector(".workflow-tutorial-bubble"));
  await page.waitForFunction(() => document.querySelector(".workflow-tutorial-launcher-button.is-complete") !== null);
  await waitForCondition(
    () => tutorialProgressPutRequests.some((payload) =>
      Array.isArray(payload.completed_tutorial_ids) &&
      payload.completed_tutorial_ids.includes("first-steps") &&
      Array.isArray(payload.skipped_tutorial_ids) &&
      payload.skipped_tutorial_ids.length === 0,
    ),
    "expected completed tutorial progress to be saved",
  );
  await page.locator('.react-flow__node[data-id="trigger-1"]').waitFor({ state: "visible" });
  await page.locator('.react-flow__node[data-id="effect-1"]').waitFor({ state: "visible" });
  assert.equal(await page.locator('.react-flow__node[data-id="tutorial-trigger"]').count(), 0);
  assert.equal(await page.locator('.react-flow__node[data-id="tutorial-effect"]').count(), 0);
  assert.equal(documentPutRequests.length, 0);

  assert.deepEqual(pageErrors, []);
  assert.deepEqual(consoleErrors, []);
  assert.deepEqual(documentPutRequests, []);
  assert.equal(await page.evaluate(() => window.localStorage.getItem("workflow-editor:tutorial-progress:v1")), null);
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/auth/session"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/admin/deployment-status"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/admin/state?directory_path=root"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/admin/user-preferences/workflow-tutorial-progress"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/configuration/state?directory_path=root"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/workflow-editor/document"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/workflow-editor/catalog?directory_path=root"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/workflow-editor/runs"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/workflow-editor/runs/stream"));
  assert.equal(requestedEndpoints.some((endpoint) => endpoint.startsWith(`/api/workflow-editor/${["sample", "workflow"].join("-")}`)), false);
});
