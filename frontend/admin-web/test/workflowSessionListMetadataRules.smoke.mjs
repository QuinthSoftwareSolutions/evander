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
      created_at: "2026-05-28T00:00:00Z",
      metadata: {},
      path: "root",
    },
    config_domains: [],
    children: [],
  };
}

function catalogField(key, label, kind, overrides = {}) {
  return {
    key,
    label,
    kind,
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

function createWorkflowDocument() {
  return {
    config_path: "workflow.json",
    layout_path: "workflow-layout.json",
    config_exists: true,
    layout_exists: true,
    revision: "rev-1",
    config: {
      triggers: [],
      gates: [],
      effects: [],
      queries: [
        {
          id: "query-1",
          display_name: "Find sessions",
          type: "query:session:list",
          metadata_equals: {
            "task.pr.id": 42,
            "task.code": "42",
            user: {
              name: "Jesper",
              age: 42,
            },
          },
          metadata_exists: ["user.name"],
          metadata_prefix: {
            "task.branch": "feature/",
          },
          state: "active",
        },
      ],
      edges: [],
    },
    layout: {
      nodes: {
        "query-1": { x: 160, y: 140 },
      },
    },
    graphs: [
      {
        id: "graph-1",
        name: "Session list rules",
        description: null,
        directory_path: "root",
        user_ids: [],
        node_ids: ["query-1"],
      },
    ],
    output_schemas: {},
    validation_diagnostics: [],
  };
}

function createWorkflowCatalog() {
  return {
    worker_id: "trigger-orchestrator",
    nodes: [
      {
        kind: "query",
        type: "query:session:list",
        label: "Session list",
        description: "List sessions by generic manifest metadata.",
        fields: [
          catalogField("directory_path", "Directory path", "string"),
          catalogField("metadata_equals", "Metadata equals", "json", {
            json_value_type: "object",
          }),
          catalogField("metadata_exists", "Metadata exists", "json"),
          catalogField("metadata_prefix", "Metadata prefix", "json", {
            json_value_type: "object",
          }),
          catalogField("state", "State", "enum", {
            options: [
              { value: "active", label: "Active" },
              { value: "expired", label: "Expired" },
              { value: "inactive", label: "Inactive" },
              { value: "all", label: "All" },
              { value: "any", label: "Any" },
            ],
          }),
          catalogField("active_only", "Active only", "boolean"),
          catalogField("limit", "Limit", "number"),
        ],
        input_ports: [],
        output_ports: [],
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

test("workflow editor exposes query session metadata filters as metadata rules", async (t) => {
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
  assert.ok(address && typeof address === "object" && "port" in address);

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
  const page = await context.newPage();
  page.setDefaultTimeout(5000);
  const pageErrors = [];
  const consoleErrors = [];
  const saveRequests = [];
  const state = {
    currentDocument: createWorkflowDocument(),
    currentCatalog: createWorkflowCatalog(),
  };

  await t.after(async () => {
    await page.close().catch(() => {});
    await context.close().catch(() => {});
    await browser.close().catch(() => {});
    await server.close().catch(() => {});
  });

  await context.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const { pathname } = url;

    if (await fulfillAdminShellBackgroundApiRequest(route, pathname)) {
      return;
    }

    if (pathname === "/api/auth/session") {
      await route.fulfill(jsonResponse({
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
        privileged_operations: { unlocked: true },
      }));
      return;
    }

    if (pathname === "/api/admin/deployment-status") {
      await route.fulfill(jsonResponse({
        active: false,
        message: null,
        source: null,
        started_at: null,
        updated_at: null,
        ttl_seconds: 0,
        stale: false,
      }));
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
        assistant: {
          enabled: false,
          show_launcher_when_disabled: false,
        },
      }));
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
      await route.fulfill(jsonResponse({
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
      }));
      return;
    }

    if (pathname === "/api/workflow-editor/document") {
      if (route.request().method() === "PUT") {
        const body = JSON.parse(route.request().postData() ?? "{}");
        saveRequests.push(body);
        state.currentDocument = {
          ...state.currentDocument,
          config: body.config,
          layout: body.layout,
          graphs: body.graphs,
          output_schemas: body.output_schemas,
          validation_diagnostics: body.validation_diagnostics,
          revision: "rev-2",
        };
        await route.fulfill(jsonResponse({
          config: body.config,
          layout: body.layout,
          graphs: body.graphs,
          output_schemas: body.output_schemas,
          validation_diagnostics: body.validation_diagnostics,
          revision: "rev-2",
        }));
        return;
      }
      await route.fulfill(jsonResponse(state.currentDocument));
      return;
    }

    if (pathname === "/api/workflow-editor/catalog") {
      await route.fulfill(jsonResponse(state.currentCatalog));
      return;
    }

    if (pathname === "/api/workflow-editor/runs") {
      await route.fulfill(jsonResponse({
        worker_id: "trigger-orchestrator",
        generated_at: "2026-05-28T12:00:00Z",
        runs: [],
        node_activity: {},
      }));
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

    await route.fulfill(jsonResponse({ error: `Unhandled ${pathname}` }, 404));
  });

  attachPageListeners(page, pageErrors, consoleErrors);

  await page.goto(`http://127.0.0.1:${address.port}/#workflow`, {
    waitUntil: "domcontentloaded",
  });
  await page.waitForSelector(".canvas-shell .react-flow__pane");
  await page.locator('.react-flow__node[data-id="query-1"]').click();

  const inspector = page.locator(".sidebar-section.inspector").filter({ hasText: "query:session:list" });
  await inspector.waitFor({ state: "visible" });
  await inspector.getByText("Metadata rules").waitFor({ state: "visible" });
  assert.equal(await inspector.locator(".metadata-rule-raw-fields").count(), 0);
  assert.equal(await inspector.locator(".metadata-rule-field-mode").count(), 0);
  await inspector.getByRole("button", { name: "Raw filters" }).waitFor({ state: "visible" });

  const rows = inspector.locator(".metadata-rule-row");
  await rows.first().waitFor({ state: "visible" });
  await assert.equal(await rows.count(), 6);
  await assert.equal(await rows.nth(0).locator("select").inputValue(), "equals");
  await assert.equal(await rows.nth(0).getByLabel("Metadata key").inputValue(), "task.pr.id");
  await assert.equal(await rows.nth(0).getByLabel("Exact value").inputValue(), "42");
  await assert.equal(await rows.nth(1).locator("select").inputValue(), "equals");
  await assert.equal(await rows.nth(1).getByLabel("Metadata key").inputValue(), "task.code");
  await assert.equal(await rows.nth(1).getByLabel("Exact value").inputValue(), "\"42\"");
  await assert.equal(await rows.nth(2).locator("select").inputValue(), "equals");
  await assert.equal(await rows.nth(2).getByLabel("Metadata key").inputValue(), "user.name");
  await assert.equal(await rows.nth(2).getByLabel("Exact value").inputValue(), "Jesper");
  await assert.equal(await rows.nth(3).locator("select").inputValue(), "equals");
  await assert.equal(await rows.nth(3).getByLabel("Metadata key").inputValue(), "user.age");
  await assert.equal(await rows.nth(3).getByLabel("Exact value").inputValue(), "42");
  await assert.equal(await rows.nth(4).locator("select").inputValue(), "exists");
  await assert.equal(await rows.nth(4).getByLabel("Metadata key").inputValue(), "user.name");
  await assert.equal(await rows.nth(4).getByLabel("Exact value").count(), 0);
  await assert.equal(await rows.nth(5).locator("select").inputValue(), "prefix");
  await assert.equal(await rows.nth(5).getByLabel("Metadata key").inputValue(), "task.branch");
  await assert.equal(
    await rows.nth(5).getByRole("textbox", { name: "Prefix" }).inputValue(),
    "feature/",
  );

  await inspector.getByRole("button", { name: "Add metadata rule" }).click();
  const newRule = rows.nth(6);
  await assert.equal(await newRule.locator("select").inputValue(), "equals");
  await newRule.getByLabel("Metadata key").fill("task.owner");
  await newRule.getByLabel("Exact value").fill("Jesper");

  await page.getByRole("button", { name: "Save workflow" }).click();
  await page.waitForFunction(() => document.querySelectorAll(".node-unsaved-badge").length === 0);
  assert.equal(saveRequests.length, 1);
  assert.deepEqual(saveRequests[0].config.queries[0].metadata_equals, {
    "task.pr.id": 42,
    "task.code": "42",
    "user.name": "Jesper",
    "user.age": 42,
    "task.owner": "Jesper",
  });
  assert.deepEqual(saveRequests[0].config.queries[0].metadata_exists, ["user.name"]);
  assert.deepEqual(saveRequests[0].config.queries[0].metadata_prefix, {
    "task.branch": "feature/",
  });

  await inspector.getByRole("button", { name: "Raw filters" }).click();
  const rawPrefixField = inspector.locator(".metadata-rule-raw-fields .inspector-field").filter({ hasText: "Metadata prefix" });
  await rawPrefixField.waitFor({ state: "visible" });
  await rawPrefixField.locator(".mode-select select").selectOption("bind");
  await rawPrefixField.locator(".binding-editor input").fill("${{trigger-1.feedback.metadata.prefix_filter}}");

  const stateField = inspector.locator(".inspector-field").filter({ hasText: "State" }).last();
  await assert.equal(await stateField.locator(".mode-select select").inputValue(), "literal");
  await stateField.locator(".mode-select select").selectOption("bind");
  await stateField.locator(".binding-editor input").fill("${{trigger-1.session_state}}");

  await page.getByRole("button", { name: "Save workflow" }).click();
  await page.waitForFunction(() => document.querySelectorAll(".node-unsaved-badge").length === 0);
  assert.equal(saveRequests.length, 2);
  assert.equal(
    saveRequests[1].config.queries[0].metadata_prefix,
    "${{trigger-1.feedback.metadata.prefix_filter}}",
  );
  assert.equal(saveRequests[1].config.queries[0].state, "${{trigger-1.session_state}}");

  assert.deepEqual(pageErrors, []);
  assert.deepEqual(consoleErrors, []);
});
