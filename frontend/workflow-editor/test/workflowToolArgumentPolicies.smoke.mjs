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

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function createWorkflowDocument(toolArgumentPolicies) {
  return {
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
          type: "effect:agent:create",
          prompt: "Handle ticket",
          tool_argument_policies: toolArgumentPolicies,
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
        name: "Tool argument policies smoke",
        description: null,
        directory_paths: ["root"],
        user_ids: [],
        node_ids: ["trigger-1", "effect-1"],
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
        kind: "trigger",
        type: "trigger:manual",
        label: "Manual",
        fields: [],
        input_ports: [],
        output_ports: [{ key: "completed", label: "Completed" }],
      },
      {
        kind: "effect",
        type: "effect:agent:create",
        label: "Create agent",
        fields: [
          {
            key: "tool_argument_policies",
            label: "Tool argument policies",
            kind: "json",
            required: false,
            bindable: true,
            read_only: false,
            multiline: false,
            description: null,
            placeholder: null,
            json_value_type: "object",
            default_value: {},
            options: [
              { value: "run_script", label: "run_script" },
              { value: "create_ado_backlog_item", label: "create_ado_backlog_item" },
            ],
            item_fields: [],
          },
        ],
        input_ports: [],
        output_ports: [],
      },
    ],
  };
}

async function selectEffectNode(page) {
  const effectNode = page.locator('.react-flow__node[data-id="effect-1"]');
  await effectNode.waitFor({ state: "visible" });
  await effectNode.click();
}

async function openWorkflowEditor(page, address) {
  await page.goto(`http://127.0.0.1:${address.port}/#workflow`, {
    waitUntil: "domcontentloaded",
  });
  await page.waitForSelector(".canvas-shell .react-flow__pane");
  await selectEffectNode(page);
  await page.locator(".sidebar-section.inspector").filter({ hasText: "Tool argument policies" }).waitFor({ state: "visible" });
}

async function loadScenario(page, address, state) {
  state.saveRequests.length = 0;
  state.currentDocument = clone(state.nextDocument);
  state.currentCatalog = clone(state.nextCatalog);
  await openWorkflowEditor(page, address);
}

function attachPageListeners(page, pageErrors, consoleErrors) {
  page.on("pageerror", (error) => {
    pageErrors.push(error);
  });
  page.on("console", (message) => {
    if (message.type() === "error" && !message.text().includes("Encountered two children with the same key")) {
      consoleErrors.push(message.text());
    }
  });
}

test("workflow editor structured tool argument policies support structured editing, raw fallback, and validation", async (t) => {
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
  let page = await context.newPage();

  const pageErrors = [];
  const consoleErrors = [];
  const saveRequests = [];
  const requestedEndpoints = [];

  const state = {
    nextDocument: createWorkflowDocument({}),
    nextCatalog: createWorkflowCatalog(),
    currentDocument: createWorkflowDocument({}),
    currentCatalog: createWorkflowCatalog(),
    saveRequests,
  };

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
  const workflowRuns = {
    worker_id: "trigger-orchestrator",
    generated_at: "2026-05-16T12:00:00Z",
    runs: [],
    node_activity: {},
  };

  await context.route("**/api/**", async (route) => {
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
        state.currentDocument = {
          ...state.currentDocument,
          config: body.config,
          layout: body.layout,
          graphs: body.graphs,
          output_schemas: body.output_schemas,
          validation_diagnostics: body.validation_diagnostics,
          revision: "rev-2",
        };
        await route.fulfill(
          jsonResponse({
            config: body.config,
            layout: body.layout,
            graphs: body.graphs,
            output_schemas: body.output_schemas,
            validation_diagnostics: body.validation_diagnostics,
            revision: "rev-2",
          }),
        );
        return;
      }

      await route.fulfill(jsonResponse(state.currentDocument));
      return;
    }

    if (pathname === "/api/workflow-editor/catalog") {
      assert.equal(url.searchParams.get("directory_path"), "root");
      await route.fulfill(jsonResponse(state.currentCatalog));
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

  attachPageListeners(page, pageErrors, consoleErrors);

  const scenario = {
    empty: createWorkflowDocument({}),
    pythonRegex: createWorkflowDocument({
      run_script: [
        {
          id: "python-regex",
          constraints: [
            {
              id: "named-group",
              path: "script_name",
              operator: "pattern",
              pattern: "(?P<script>^seed-[a-z]+$)",
              case_sensitive: true,
            },
          ],
        },
      ],
    }),
    unsupported: createWorkflowDocument({
      run_script: [
        {
          id: "policy",
          extra: "keep-me",
          constraints: [
            {
              id: "constraint",
              path: "script_name",
              operator: "unknown",
              value: "seed-demo",
            },
          ],
        },
      ],
    }),
  };

  state.nextCatalog = createWorkflowCatalog();

  state.nextDocument = scenario.empty;
  await loadScenario(page, address, state);

  const inspector = page.locator(".sidebar-section.inspector").filter({ hasText: "Tool argument policies" });
  await inspector.waitFor({ state: "visible" });
  await assert.equal(await page.getByLabel("JSON mode").inputValue(), "structured");
  await assert.equal(await page.getByText("No structured tool policies yet.").count(), 1);
  await assert.equal(await page.getByRole("button", { name: "Add policy tool" }).count(), 1);

  await page.getByRole("button", { name: "Add policy tool" }).click();
  const structuredPolicy = page.locator(".tool-argument-policy-tool").first();
  await assert.equal(await structuredPolicy.getByLabel("Tool name").inputValue(), "run_script");
  await structuredPolicy.getByRole("button", { name: "Add rule" }).click();
  const structuredRule = structuredPolicy.locator(".tool-argument-policy-rule").first();
  const structuredConstraint = structuredRule.locator(".tool-argument-policy-constraint").first();
  await structuredRule.getByLabel("Rule id").fill("seed-rule");
  await structuredConstraint.getByLabel("Constraint id").fill("script");
  await structuredConstraint.getByLabel("Path").fill("script_name");
  await structuredConstraint.getByLabel("Operator").selectOption("fixed");
  await structuredConstraint.getByLabel("Fixed value").fill("seed-demo");

  const graphsSection = page.locator(".workflow-graphs-section");
  await graphsSection.locator("summary").click();
  await graphsSection.locator(".workflow-graph-item").filter({ hasText: "Tool argument policies smoke" }).click();
  const graphNameInput = graphsSection.locator(".inspector-label").filter({ hasText: "Graph name" }).locator("input");
  await page.waitForFunction(
    () => document.querySelector(".workflow-graphs-section .workflow-graph-editor input")?.value === "Tool argument policies smoke",
  );
  await graphNameInput.fill("");
  await assert.equal(await graphNameInput.inputValue(), "");
  await graphsSection.getByRole("button", { name: "Apply graph details" }).click();
  await page.getByText("Graph name is required.").first().waitFor({ state: "visible" });

  await page.getByRole("button", { name: "Save workflow" }).click();
  await page.waitForFunction(() => document.querySelectorAll(".node-unsaved-badge").length === 0);
  assert.equal(saveRequests.length, 1);
  assert.deepEqual(saveRequests[0].config.effects[0].tool_argument_policies, {
    run_script: [
      {
        id: "seed-rule",
        name: null,
        constraints: [
          {
            id: "script",
            path: "script_name",
            operator: "fixed",
            value: "seed-demo",
            values: null,
            pattern: null,
            case_sensitive: true,
          },
        ],
      },
    ],
  });

  state.nextDocument = scenario.empty;
  await loadScenario(page, address, state);
  await inspector.waitFor({ state: "visible" });
  await page.getByRole("button", { name: "Add policy tool" }).click();
  const invalidPolicy = page.locator(".tool-argument-policy-tool").first();
  await invalidPolicy.getByRole("button", { name: "Add rule" }).click();
  const invalidRule = invalidPolicy.locator(".tool-argument-policy-rule").first();
  const invalidConstraint = invalidRule.locator(".tool-argument-policy-constraint").first();
  await invalidRule.getByLabel("Rule id").fill("seed-rule");
  await invalidConstraint.getByLabel("Constraint id").fill("script");
  await invalidConstraint.getByLabel("Operator").selectOption("fixed");
  await invalidConstraint.getByLabel("Fixed value").fill("seed-demo");

  saveRequests.length = 0;
  await page.getByRole("button", { name: "Save workflow" }).click();
  await assert.equal(saveRequests.length, 0);
  await page.waitForFunction(
    () => [...document.querySelectorAll(".error-text")].some((element) => element.textContent?.includes("path must be a non-empty string")),
  );

  state.nextDocument = scenario.empty;
  await loadScenario(page, address, state);
  await inspector.waitFor({ state: "visible" });
  await page.getByRole("button", { name: "Add policy tool" }).click();
  const rawSwitchPolicy = page.locator(".tool-argument-policy-tool").first();
  await rawSwitchPolicy.getByRole("button", { name: "Add rule" }).click();
  const rawSwitchRule = rawSwitchPolicy.locator(".tool-argument-policy-rule").first();
  const rawSwitchConstraint = rawSwitchRule.locator(".tool-argument-policy-constraint").first();
  await rawSwitchRule.getByLabel("Rule id").fill("seed-rule");
  await rawSwitchConstraint.getByLabel("Constraint id").fill("script");
  await rawSwitchConstraint.getByLabel("Path").fill("script_name");
  await rawSwitchConstraint.getByLabel("Operator").selectOption("allowlist");
  await rawSwitchConstraint.getByLabel("Values").fill("seed-structured\n2");
  await rawSwitchConstraint.locator('input[type="checkbox"]').setChecked(false);

  await page.getByLabel("JSON mode").selectOption("raw");
  const rawEditor = page.locator(".tool-argument-policy-editor textarea");
  const rawValue = {
    run_script: [
      {
        id: "seed-rule",
        constraints: [
          {
            id: "script",
            path: "script_name",
            operator: "allowlist",
            values: ["seed-raw", 2],
            case_sensitive: false,
          },
        ],
      },
    ],
  };
  await rawEditor.fill(JSON.stringify(rawValue, null, 2));
  await page.getByLabel("JSON mode").selectOption("structured");
  await assert.equal(await rawSwitchPolicy.getByLabel("Tool name").inputValue(), "run_script");
  await assert.equal(await rawSwitchRule.getByLabel("Rule id").inputValue(), "seed-rule");
  await assert.equal(await rawSwitchConstraint.getByLabel("Constraint id").inputValue(), "script");
  await assert.equal(await rawSwitchConstraint.getByLabel("Path").inputValue(), "script_name");
  await assert.equal(await rawSwitchConstraint.getByLabel("Operator").inputValue(), "allowlist");
  await assert.equal(await rawSwitchConstraint.getByLabel("Values").inputValue(), "seed-raw\n2");
  await assert.equal(await rawSwitchConstraint.locator('input[type="checkbox"]').isChecked(), false);

  saveRequests.length = 0;
  await page.getByRole("button", { name: "Save workflow" }).click();
  await page.waitForFunction(() => document.querySelectorAll(".node-unsaved-badge").length === 0);
  assert.equal(saveRequests.length, 1);
  assert.deepEqual(saveRequests[0].config.effects[0].tool_argument_policies, rawValue);

  await page.close();
  page = await context.newPage();
  attachPageListeners(page, pageErrors, consoleErrors);
  state.nextDocument = scenario.pythonRegex;
  await loadScenario(page, address, state);
  const pythonRegexInspector = page.locator(".sidebar-section.inspector").filter({ hasText: "Tool argument policies" });
  await pythonRegexInspector.waitFor({ state: "visible" });
  await assert.equal(await page.getByLabel("JSON mode").inputValue(), "structured");
  const pythonRegexConstraint = pythonRegexInspector.locator(".tool-argument-policy-constraint").first();
  await assert.equal(await pythonRegexConstraint.getByLabel("Operator").inputValue(), "pattern");
  await assert.equal(
    await pythonRegexConstraint.locator("label").filter({ hasText: "Pattern" }).locator("input").inputValue(),
    "(?P<script>^seed-[a-z]+$)",
  );

  saveRequests.length = 0;
  await page.getByRole("button", { name: "Save workflow" }).click();
  await page.waitForFunction(() => document.querySelectorAll(".node-unsaved-badge").length === 0);
  assert.equal(saveRequests.length, 1);
  assert.equal(
    saveRequests[0].config.effects[0].tool_argument_policies.run_script[0].constraints[0].pattern,
    "(?P<script>^seed-[a-z]+$)",
  );

  await page.close();
  page = await context.newPage();
  attachPageListeners(page, pageErrors, consoleErrors);
  state.nextDocument = scenario.unsupported;
  await loadScenario(page, address, state);
  const fallbackInspector = page.locator(".sidebar-section.inspector").filter({ hasText: "Tool argument policies" });
  await fallbackInspector.waitFor({ state: "visible" });
  await page.waitForFunction(
    () => document.querySelector(".tool-argument-policy-editor select")?.value === "raw",
  );
  await assert.equal(await page.getByLabel("JSON mode").inputValue(), "raw");
  const fallbackRawEditor = page.locator(".tool-argument-policy-editor textarea");
  await assert.equal(
    await fallbackRawEditor.inputValue(),
    JSON.stringify(scenario.unsupported.config.effects[0].tool_argument_policies, null, 2),
  );

  saveRequests.length = 0;
  await page.getByRole("button", { name: "Save workflow" }).click();
  await page.waitForFunction(() => document.querySelectorAll(".node-unsaved-badge").length === 0);
  assert.equal(saveRequests.length, 1);
  assert.deepEqual(saveRequests[0].config.effects[0].tool_argument_policies, scenario.unsupported.config.effects[0].tool_argument_policies);

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
