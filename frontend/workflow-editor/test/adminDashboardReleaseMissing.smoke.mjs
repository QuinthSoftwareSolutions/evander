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

test("admin shell release panel shows missing metadata without inventing release entries", async (t) => {
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
  const context = await browser.newContext();
  const page = await context.newPage();
  page.setDefaultTimeout(10000);

  const requestedEndpoints = [];
  const pageErrors = [];
  const consoleErrors = [];

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

  const rootDirectoryNode = {
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
      data: rootDirectoryNode,
      errors: [],
      validation_diagnostics: [],
    },
  };

  const deploymentStatusResponse = {
    active: false,
    message: null,
    source: null,
    started_at: null,
    updated_at: null,
    ttl_seconds: 0,
    stale: false,
  };

  const workersResponse = {
    workers: [],
  };

  const agentsResponse = {
    items: [],
    directory_path: "root",
    direct_only: true,
  };

  const workflowRunsResponse = {
    worker_id: "workflow-run-worker",
    generated_at: "2026-05-18T10:02:00Z",
    runs: [],
    node_activity: {},
  };

  const workflowActivityResponse = {
    worker_id: "graph-activity-worker",
    generated_at: "2026-05-18T10:05:00Z",
    period_start: "2026-05-18T09:00:00Z",
    period_end: "2026-05-18T10:05:00Z",
    graphs: [],
  };

  const approvalsResponse = {
    items: [],
    health: {
      total: 0,
      pending: 0,
      approved: 0,
      denied: 0,
      expired: 0,
      oldest_pending_age_seconds: null,
    },
  };

  const sessionsResponse = {
    items: [],
    active_items: [],
    expired_items: [],
    counts: {
      total: 0,
      active: 0,
      expired: 0,
    },
  };

  const sessionCatalogResponse = {
    tool_options: [],
    resume_mode_options: [],
    renew_extension_presets: [],
  };

  const toolCallsResponse = {
    items: [],
    counts: {
      returned: 0,
      status_counts: {
        success: 0,
        denied: 0,
        error: 0,
      },
    },
  };

  const releaseInfoResponse = {
    version: null,
    version_source: null,
    build: null,
    build_source: null,
    changelog: null,
    missing_reasons: ["Version unavailable", "Build unavailable", "Recent changelog unavailable"],
  };

  const workflowDocumentResponse = {
    config_path: "workflow.json",
    layout_path: "workflow-layout.json",
    config_exists: false,
    layout_exists: false,
    config: {
      triggers: [],
      gates: [],
      effects: [],
      queries: [],
      edges: [],
    },
    layout: {
      nodes: {},
    },
    graphs: [],
    output_schemas: {},
    validation_diagnostics: [],
    revision: "rev-1",
  };

  const workflowCatalogResponse = {
    worker_id: "trigger-orchestrator",
    nodes: [],
  };

  await t.after(async () => {
    await page.close().catch(() => {});
    await context.close().catch(() => {});
    await browser.close().catch(() => {});
    await server.close().catch(() => {});
  });

  page.on("pageerror", (error) => {
    pageErrors.push(error);
  });
  page.on("console", (message) => {
    if (message.type() === "error") {
      consoleErrors.push(message.text());
    }
  });

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const { pathname } = url;
    requestedEndpoints.push(`${pathname}${url.search}`);

    if (pathname === "/api/auth/session") {
      await route.fulfill(jsonResponse(sessionResponse));
      return;
    }

    if (pathname === "/api/admin/deployment-status") {
      await route.fulfill(jsonResponse(deploymentStatusResponse));
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

    if (pathname === "/api/workers") {
      await route.fulfill(jsonResponse(workersResponse));
      return;
    }

    if (pathname === "/api/admin/agents") {
      assert.equal(url.searchParams.get("directory_path"), "root");
      assert.equal(url.searchParams.get("direct_only"), "true");
      assert.equal(url.searchParams.get("include_inactive"), "true");
      assert.equal(url.searchParams.get("backend"), "all");
      await route.fulfill(jsonResponse(agentsResponse));
      return;
    }

    if (pathname === "/api/workflow-editor/runs") {
      await route.fulfill(jsonResponse(workflowRunsResponse));
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

    if (pathname === "/api/admin/reports/workflow-graph-activity") {
      assert.equal(url.searchParams.get("limit"), "5");
      await route.fulfill(jsonResponse(workflowActivityResponse));
      return;
    }

    if (pathname === "/api/admin/approvals") {
      assert.equal(url.searchParams.get("statuses"), "pending");
      assert.equal(url.searchParams.get("directory_path"), "root");
      assert.equal(url.searchParams.get("direct_only"), "true");
      await route.fulfill(jsonResponse(approvalsResponse));
      return;
    }

    if (pathname === "/api/admin/sessions/catalog") {
      await route.fulfill(jsonResponse(sessionCatalogResponse));
      return;
    }

    if (pathname === "/api/admin/sessions") {
      assert.equal(url.searchParams.get("include_expired"), "true");
      assert.equal(url.searchParams.get("directory_path"), "root");
      assert.equal(url.searchParams.get("direct_only"), "true");
      await route.fulfill(jsonResponse(sessionsResponse));
      return;
    }

    if (pathname === "/api/admin/tool-calls") {
      assert.equal(url.searchParams.get("directory_path"), "root");
      assert.equal(url.searchParams.get("direct_only"), "true");
      assert.equal(url.searchParams.get("limit"), "5");
      await route.fulfill(jsonResponse(toolCallsResponse));
      return;
    }

    if (pathname === "/api/admin/release-info") {
      await route.fulfill(jsonResponse(releaseInfoResponse));
      return;
    }

    if (pathname === "/api/workflow-editor/document") {
      await route.fulfill(jsonResponse(workflowDocumentResponse));
      return;
    }

    if (pathname === "/api/workflow-editor/catalog") {
      assert.equal(url.searchParams.get("directory_path"), "root");
      await route.fulfill(
        jsonResponse({
          worker_id: "trigger-orchestrator",
          nodes: workflowCatalogResponse.nodes,
        }),
      );
      return;
    }

    assert.fail(`unexpected API request: ${pathname}${url.search}`);
  });

  await page.goto(`http://127.0.0.1:${address.port}/`, {
    waitUntil: "domcontentloaded",
  });

  const releasePanel = page.locator("article.dashboard-release-panel");
  await assert.doesNotReject(() => releasePanel.getByRole("heading", { name: "Version and changelog" }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => releasePanel.locator(".release-missing-list strong").waitFor({ state: "visible" }));
  await assert.doesNotReject(() => releasePanel.getByText("Version unavailable").waitFor({ state: "visible" }));
  await assert.doesNotReject(() => releasePanel.getByText("Build unavailable").waitFor({ state: "visible" }));
  await assert.doesNotReject(() => releasePanel.getByText("Recent changelog unavailable").waitFor({ state: "visible" }));
  assert.match(await releasePanel.locator(".dashboard-release-summary").innerText(), /Version unavailable/);
  assert.match(await releasePanel.locator(".dashboard-release-summary").innerText(), /Build unavailable/);
  assert.equal(await releasePanel.locator(".release-entry-item").count(), 0);
  assert.equal(await releasePanel.locator(".release-kind-pill").count(), 0);
  assert.equal(await releasePanel.getByText("0.1.0").count(), 0);
  assert.equal(await releasePanel.getByText("agent-sandbox-build-20260521").count(), 0);

  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/admin/release-info"));
  assert.deepEqual(pageErrors, []);
  assert.deepEqual(consoleErrors, []);
});
