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

test("workflow editor stages unsaved changes, resets them, and guards tab and directory navigation", async (t) => {
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
  const saveRequests = [];
  const tabConfirmMessages = [];
  const directoryConfirmMessages = [];

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
        name: "Unsaved changes smoke",
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
      if (route.request().method() === "PUT") {
        const body = JSON.parse(route.request().postData() ?? "{}");
        saveRequests.push(body);
        assert.equal(body.expected_revision, "rev-1");
        assert.equal(body.config.effects[0].display_name, "Dispatch agent v2");
        assert.equal(body.config.effects[0].prompt, "Handle escalations");
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
  page.on("dialog", async (dialog) => {
    if (dialog.message() === "Workflow has unsaved changes. Discard them and continue?") {
      if (tabConfirmMessages.length === 0) {
        tabConfirmMessages.push(dialog.message());
        await dialog.dismiss();
        return;
      }
      directoryConfirmMessages.push(dialog.message());
      await dialog.dismiss();
      return;
    }
    await dialog.dismiss();
  });

  await page.goto(`http://127.0.0.1:${address.port}/#workflow`, {
    waitUntil: "domcontentloaded",
  });

  await page.waitForSelector(".canvas-shell .react-flow__pane");
  await assert.equal(await page.getByRole("button", { name: "Apply node changes" }).count(), 0);
  await assert.equal(await page.getByRole("button", { name: "Reset changes" }).count(), 0);
  const saveStatus = page.locator(".save-status");
  await assert.equal(await saveStatus.filter({ hasText: "No unsaved workflow changes" }).count(), 1);

  const effectNode = page.locator('.react-flow__node[data-id="effect-1"]');
  const effectBox = await effectNode.boundingBox();
  assert.ok(effectBox, "expected effect node bounds");
  await page.mouse.click(effectBox.x + effectBox.width / 2, effectBox.y + effectBox.height / 2);

  const nodeInspector = page.locator(".sidebar-section.inspector").filter({ hasText: "Display name" });
  const displayNameInput = nodeInspector.locator(".inspector-label").filter({ hasText: "Display name" }).locator("input");
  await nodeInspector.waitFor({ state: "visible" });
  await displayNameInput.fill("Dispatch agent v2");
  const promptInput = nodeInspector.locator(".inspector-field").filter({ hasText: "Prompt" }).locator("input");
  await promptInput.fill("Handle escalations");

  await assert.doesNotReject(
    () => page.locator(".node-title").filter({ hasText: "Dispatch agent v2" }).first().waitFor({ state: "visible", timeout: 60000 }),
  );
  assert.equal(await promptInput.inputValue(), "Handle escalations");
  await assert.equal(saveRequests.length, 0);
  await assert.equal(await page.getByLabel("Unsaved changes").count(), 1);
  await assert.equal(await saveStatus.filter({ hasText: "Unsaved workflow changes" }).count(), 1);
  await assert.equal(await saveStatus.filter({ hasText: "1 edited node: Dispatch agent v2" }).count(), 1);
  await assert.equal(await effectNode.locator(".node-card.unsaved").count(), 1);
  await assert.equal(await page.getByRole("button", { name: "Reset changes" }).count(), 1);

  const triggerNode = page.locator('.react-flow__node[data-id="trigger-1"]');
  const triggerBox = await triggerNode.boundingBox();
  assert.ok(triggerBox, "expected trigger node bounds");
  await page.mouse.click(triggerBox.x + triggerBox.width / 2, triggerBox.y + triggerBox.height / 2);
  await page.waitForTimeout(250);
  await nodeInspector.waitFor({ state: "visible" });
  assert.equal(await displayNameInput.inputValue(), "Incoming command");
  await page.mouse.click(effectBox.x + effectBox.width / 2, effectBox.y + effectBox.height / 2);
  await page.waitForTimeout(250);
  await nodeInspector.waitFor({ state: "visible" });
  await assert.equal(await displayNameInput.inputValue(), "Dispatch agent v2");
  await assert.equal(await promptInput.inputValue(), "Handle escalations");

  await page.getByRole("button", { name: "Reset changes" }).click();
  await page.waitForFunction(
    () => [...document.querySelectorAll(".node-title")].some((element) => element.textContent?.trim() === "Dispatch agent"),
  );
  await page.waitForTimeout(250);
  await nodeInspector.waitFor({ state: "visible" });
  assert.equal(await displayNameInput.inputValue(), "Dispatch agent");
  assert.equal(await promptInput.inputValue(), "Handle ticket");
  await assert.equal(await page.getByLabel("Unsaved changes").count(), 0);
  await assert.equal(await saveStatus.filter({ hasText: "No unsaved workflow changes" }).count(), 1);
  await assert.equal(await effectNode.locator(".node-card.unsaved").count(), 0);
  await assert.equal(await page.getByRole("button", { name: "Reset changes" }).count(), 0);
  await assert.equal(saveRequests.length, 0);

  await page.mouse.click(effectBox.x + effectBox.width / 2, effectBox.y + effectBox.height / 2);
  await page.waitForTimeout(250);
  await displayNameInput.fill("Dispatch agent v2");
  await promptInput.fill("Handle escalations");
  await page.waitForFunction(
    (nextTitle) => [...document.querySelectorAll(".node-title")].some((element) => element.textContent?.trim() === nextTitle),
    "Dispatch agent v2",
  );

  const expandSectionsButton = page.getByRole("button", { name: "Expand sections" });
  if (await expandSectionsButton.count()) {
    await expandSectionsButton.click();
  }
  const usersTab = page.locator(".admin-shell-tab").filter({ hasText: "Users" });
  await usersTab.click();
  assert.deepEqual(tabConfirmMessages, ["Workflow has unsaved changes. Discard them and continue?"]);
  await page.waitForFunction(() => window.location.hash === "#workflow");
  await assert.equal(await page.locator(".admin-shell-tab.active").filter({ hasText: "Workflow" }).count(), 1);
  await assert.equal(await page.getByLabel("Unsaved changes").count(), 1);

  await page.getByRole("button", { name: "Choose directory" }).click();
  await page.getByRole("button", { name: "root/team-a", exact: true }).click();
  assert.deepEqual(directoryConfirmMessages, ["Workflow has unsaved changes. Discard them and continue?"]);
  await assert.equal(await page.locator(".modal-card.directory-modal").count(), 1);
  await page.getByRole("button", { name: "Close" }).click();
  await assert.equal(await page.locator(".modal-card.directory-modal").count(), 0);

  await page.getByRole("button", { name: "Save workflow" }).click();
  await page.waitForFunction(() => [...document.querySelectorAll(".node-title")].some((element) => element.textContent?.trim() === "Dispatch agent v2"));
  await page.waitForFunction(() => document.querySelectorAll(".node-unsaved-badge").length === 0);

  assert.equal(saveRequests.length, 1);
  assert.equal(await page.getByLabel("Unsaved changes").count(), 0);
  assert.equal(await saveStatus.filter({ hasText: "Unsaved workflow changes" }).count(), 0);
  assert.equal(await saveStatus.filter({ hasText: "Workflow saved" }).count(), 1);
  assert.equal(await effectNode.locator(".node-card.unsaved").count(), 0);
  assert.equal(await page.getByRole("button", { name: "Reset changes" }).count(), 0);
  assert.equal(await displayNameInput.inputValue(), "Dispatch agent v2");
  assert.equal(await promptInput.inputValue(), "Handle escalations");

  assert.deepEqual(pageErrors, []);
  assert.deepEqual(consoleErrors, []);
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/auth/session"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/admin/deployment-status"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/admin/state?directory_path=root"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/configuration/state?directory_path=root"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/workflow-editor/document"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/workflow-editor/catalog?directory_path=root"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/workflow-editor/runs"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/workflow-editor/runs/stream"));
});
