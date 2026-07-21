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

function catalogField(key, label, overrides = {}) {
  return {
    key,
    label,
    kind: "string",
    required: false,
    bindable: true,
    read_only: false,
    multiline: false,
    description: null,
    placeholder: null,
    json_value_type: null,
    default_value: null,
    options: [],
    item_fields: [],
    ...overrides,
  };
}

function workflowDocument() {
  return {
    config_path: "workflow.json",
    layout_path: "workflow-layout.json",
    config_exists: true,
    layout_exists: true,
    revision: "rev-1",
    config: {
      triggers: [],
      gates: [],
      effects: [{
        id: "prompt-1",
        display_name: "Prompt persistent agent",
        type: "effect:agent:prompt",
        target: "session-active",
        prompt: "Continue the task.",
      }],
      queries: [],
      edges: [],
    },
    layout: { nodes: { "prompt-1": { x: 180, y: 160 } } },
    graphs: [{
      id: "graph-1",
      name: "Target suggestions",
      description: null,
      directory_paths: ["root"],
      user_ids: [],
      node_ids: ["prompt-1"],
    }],
    output_schemas: {},
    validation_diagnostics: [],
  };
}

function workflowCatalog() {
  return {
    worker_id: "trigger-orchestrator",
    nodes: [{
      kind: "effect",
      type: "effect:agent:prompt",
      label: "Agent prompt",
      description: null,
      fields: [
        catalogField("target", "Target"),
        catalogField("session_id", "Session ID"),
        catalogField("vm_name", "VM name"),
        catalogField("prompt", "Prompt", { multiline: true }),
      ],
      input_ports: [],
      output_ports: [],
    }],
    internal_events: [],
  };
}

test("workflow target suggestions preserve literal, binding, and template entry", async (t) => {
  const projectRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
  const server = await createServer({
    root: projectRoot,
    server: { host: "127.0.0.1", port: 0 },
    logLevel: "error",
  });
  await server.listen();
  const address = server.httpServer?.address();
  assert.ok(address && typeof address === "object" && "port" in address);

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
  const page = await context.newPage();
  page.setDefaultTimeout(5000);
  const pageErrors = [];
  const consoleErrors = [];
  const inventoryRequests = [];

  await t.after(async () => {
    await page.close().catch(() => {});
    await context.close().catch(() => {});
    await browser.close().catch(() => {});
    await server.close().catch(() => {});
  });

  page.on("pageerror", (error) => pageErrors.push(error));
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });

  await context.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const { pathname } = url;
    if (await fulfillAdminShellBackgroundApiRequest(route, pathname)) return;

    if (pathname === "/api/auth/session") {
      await route.fulfill(jsonResponse({
        auth_mode: "none",
        auth_required: false,
        authenticated: true,
        username: "admin",
        display_name: "Admin Operator",
        role: "admin",
        can_manage_users: true,
        session_id: "admin-session",
        available_login_methods: [],
        azure_sso_login_url: null,
        privileged_operations: { unlocked: true },
      }));
      return;
    }
    if (pathname === "/api/admin/deployment-status") {
      await route.fulfill(jsonResponse({ active: false, stale: false, ttl_seconds: 0 }));
      return;
    }
    if (pathname === "/api/admin/state") {
      await route.fulfill(jsonResponse({
        selected_directory_path: "root",
        directory_scoped_config_enabled: true,
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
        directory_scoped_config_enabled: true,
        selected_directory_path: "root",
        execution_backend: "libvirt_vm",
        selected_directory: { path: "root", parent_path: null, can_create: true, can_delete: false },
        tree: {
          ok: true,
          data: {
            directory: { id: "root", name: "root", parent_id: null, metadata: {}, path: "root" },
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
      await route.fulfill(jsonResponse(workflowDocument()));
      return;
    }
    if (pathname === "/api/workflow-editor/catalog") {
      await route.fulfill(jsonResponse(workflowCatalog()));
      return;
    }
    if (pathname === "/api/workflow-editor/runs") {
      await route.fulfill(jsonResponse({
        worker_id: "trigger-orchestrator",
        generated_at: "2026-07-21T12:00:00Z",
        runs: [],
        node_activity: {},
      }));
      return;
    }
    if (pathname === "/api/workflow-editor/runs/stream") {
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        headers: { "Cache-Control": "no-cache", Connection: "keep-alive" },
        body: ": workflow run stream\n\n",
      });
      return;
    }
    if (pathname === "/api/workflow-editor/validate") {
      const body = JSON.parse(route.request().postData() ?? "{}");
      await route.fulfill(jsonResponse({ ...body, validation_diagnostics: [] }));
      return;
    }
    if (pathname === "/api/admin/sessions") {
      inventoryRequests.push(url);
      await route.fulfill(jsonResponse({
        items: [
          { session_id: "session-active", vm_id: "vm-active", is_expired: false },
          { session_id: "session-expired", vm_id: "vm-expired", is_expired: true },
        ],
        counts: { total: 2, active: 1, expired: 1 },
        status_ids: { active: ["session-active"], expired: ["session-expired"] },
      }));
      return;
    }
    if (pathname === "/api/admin/vms") {
      inventoryRequests.push(url);
      await route.fulfill(jsonResponse({
        items: [{ vm_name: "vm-active" }, { vm_name: "vm-stopped" }],
        directory_path: "root",
        direct_only: false,
      }));
      return;
    }
    await route.fulfill(jsonResponse({ error: `Unhandled ${pathname}` }, 404));
  });

  await page.goto(`http://127.0.0.1:${address.port}/#workflow`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector(".canvas-shell .react-flow__pane");
  await page.locator('.react-flow__node[data-id="prompt-1"]').click();

  const inspector = page.locator(".sidebar-section.inspector").filter({ hasText: "effect:agent:prompt" });
  await inspector.waitFor({ state: "visible" });
  const targetInput = inspector.locator('input[list^="workflow-target-options-"]');
  await targetInput.waitFor({ state: "visible" });
  const targetListId = await targetInput.getAttribute("list");
  assert.equal(targetListId, "workflow-target-options-target");
  const genericOptions = inspector.locator(`#${targetListId} option`);
  await assert.equal(await genericOptions.count(), 4);
  assert.equal(
    await inspector.locator(`#${targetListId} option[value="session-expired"]`).getAttribute("label"),
    "session-expired (expired)",
  );

  await targetInput.fill("session-expired");
  assert.equal(await targetInput.inputValue(), "session-expired");
  await targetInput.fill("future-session-id");
  assert.equal(await targetInput.inputValue(), "future-session-id");

  const targetType = inspector.locator("label.inspector-field").filter({ hasText: "Prompt target type" }).locator("select");
  await targetType.selectOption("session_id");
  const sessionInput = inspector.locator('input[list="workflow-target-options-session_id"]');
  await sessionInput.waitFor({ state: "visible" });
  assert.equal(await inspector.locator("#workflow-target-options-session_id option").count(), 2);
  assert.equal(await inspector.locator('#workflow-target-options-session_id option[value="vm-stopped"]').count(), 0);

  const modeSelect = inspector.locator(".mode-select select").first();
  await modeSelect.selectOption("bind");
  const bindingInput = inspector.locator(".binding-editor input");
  await bindingInput.fill("${{create-agent.session_id}}");
  assert.equal(await bindingInput.inputValue(), "${{create-agent.session_id}}");

  await modeSelect.selectOption("template");
  const templateInput = inspector.locator(".template-editor input");
  await templateInput.fill("session-${{create-agent.session_id}}");
  assert.equal(await templateInput.inputValue(), "session-${{create-agent.session_id}}");

  assert.equal(inventoryRequests.length, 2);
  for (const requestUrl of inventoryRequests) {
    assert.equal(requestUrl.searchParams.get("directory_path"), "root");
    assert.equal(requestUrl.searchParams.get("direct_only"), "false");
  }
  assert.equal(inventoryRequests.find((url) => url.pathname.endsWith("/sessions"))?.searchParams.get("include_expired"), "true");
  assert.equal(inventoryRequests.find((url) => url.pathname.endsWith("/vms"))?.searchParams.get("include_inactive"), "true");
  assert.deepEqual(pageErrors, []);
  assert.deepEqual(consoleErrors, []);
});
