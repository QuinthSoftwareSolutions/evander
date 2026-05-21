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

test("admin shell routes to tool calls and renders redacted audit entries", async (t) => {
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

  const requestedEndpoints = [];
  const toolCallResponses = [];
  const toolCallsResponse = {
    items: [
      {
        event_id: "tool-call-1",
        tool_name: "email_send_approved",
        created_at: "2026-05-18T10:00:00Z",
        status: "success",
        decision: "allowed",
        session_id: "session-1",
        directory_path: "root/team",
        duration_ms: 42,
        role: "automation",
        actor_user_id: "user-1",
        request_id: "request-1",
        correlation_id: "correlation-1",
        approval_request_id: "approval-1",
        workflow_run_id: "run-1",
        trace_id: "trace-1",
        agent_id: "agent-1",
        vm_id: "vm-1",
        error_summary: null,
        redacted_args: {
          session_id: "session-1",
          request_id: "request-1",
          token: "<redacted>",
        },
        redacted_result: {
          request_id: "request-1",
          status: "success",
        },
        metadata: {
          approval_request_id: "approval-1",
          trace_id: "trace-1",
        },
        related_links: [
          { tab: "sessions", label: "Session", value: "session-1" },
          { tab: "approvals", label: "Approval", value: "approval-1" },
          { tab: "workflow", label: "Workflow run", value: "run-1" },
          { tab: "agents", label: "Agent", value: "agent-1" },
          { tab: "vms", label: "VM", value: "vm-1" },
        ],
      },
      {
        tool_name: "make_phone_call",
        created_at: "2026-05-18T10:01:00Z",
        status: "pending_approval",
        decision: "pending_approval",
        session_id: "session-2",
        directory_path: "root/team/sub",
        duration_ms: 1050,
        role: "automation",
        actor_user_id: "user-2",
        correlation_id: "correlation-2",
        approval_request_id: "approval-2",
        workflow_run_id: "run-2",
        trace_id: "trace-2",
        agent_id: "agent-2",
        vm_id: "vm-2",
        error_summary: "Approval required",
        redacted_args: {
          session_id: "session-2",
          secret: "<redacted>",
        },
        redacted_result: {
          status: "pending_approval",
          message: "Approval required",
        },
        metadata: {
          correlation_id: "correlation-2",
          workflow_run_id: "run-2",
        },
      },
    ],
    counts: {
      scanned: 2,
      matched: 2,
      returned: 2,
      skipped_malformed: 0,
      skipped_non_tool_call: 0,
      status_counts: {
        success: 1,
        pending_approval: 1,
        denied: 0,
        error: 0,
      },
    },
    limit: 100,
    has_more: false,
  };
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
      unlocked: false,
    },
  };
  const adminStateResponse = {
    selected_directory_path: "root",
    directory_scoped_config_enabled: false,
    execution_backend: "libvirt_vm",
    role: "admin",
    can_manage_users: true,
    privileged_operations: {
      unlocked: false,
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

  await t.after(async () => {
    await page.close().catch(() => {});
    await context.close().catch(() => {});
    await browser.close().catch(() => {});
    await server.close().catch(() => {});
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

    if (pathname === "/api/admin/tool-calls") {
      assert.equal(url.searchParams.get("directory_path"), "root");
      assert.equal(url.searchParams.get("direct_only"), "false");
      assert.equal(url.searchParams.get("limit"), "100");
      assert.equal(url.searchParams.get("q"), "request-1");
      assert.equal(url.searchParams.get("session_id"), "session-1");
      assert.equal(url.searchParams.get("tool_name"), "email_send_approved");
      assert.equal(url.searchParams.get("status"), "success");
      toolCallResponses.push(url.search);
      await route.fulfill(jsonResponse(toolCallsResponse));
      return;
    }

    await route.continue();
  });

  const pageErrors = [];
  const consoleErrors = [];
  page.on("pageerror", (error) => {
    pageErrors.push(error);
  });
  page.on("console", (message) => {
    if (message.type() === "error") {
      consoleErrors.push(message.text());
    }
  });

  await page.goto(`http://127.0.0.1:${address.port}/#tool-calls?q=request-1&session_id=session-1&tool_name=email_send_approved&status=success&direct_only=false`, {
    waitUntil: "domcontentloaded",
  });

  const navEntry = page
    .locator('nav[aria-label="Admin sections"] .admin-shell-tab')
    .filter({ hasText: "Tool Calls" });
  const filterSidebar = page.locator(".feature-sidebar");
  await page.getByRole("button", { name: "Expand sections" }).click();
  await assert.doesNotReject(() => navEntry.waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByRole("heading", { name: "Tool Calls" }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => filterSidebar.getByLabel("Search").waitFor({ state: "visible" }));
  assert.equal(await filterSidebar.getByLabel("Search").inputValue(), "request-1");
  assert.equal(await filterSidebar.getByLabel("Session").inputValue(), "session-1");
  assert.equal(await filterSidebar.getByLabel("Tool").inputValue(), "email_send_approved");
  assert.equal(await filterSidebar.getByLabel("Status").inputValue(), "success");
  assert.equal(await filterSidebar.getByLabel("Direct directory only").isChecked(), false);
  const rows = page.locator(".tool-calls-table tbody tr");
  await assert.doesNotReject(() => page.getByRole("heading", { name: "Audit entries" }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => rows.nth(0).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => rows.nth(1).waitFor({ state: "visible" }));
  assert.match(await rows.nth(0).innerText(), /Success/i);
  assert.match(await rows.nth(1).innerText(), /Pending approval/i);

  await rows.nth(1).click();
  await assert.doesNotReject(() => page.getByRole("heading", { name: "make_phone_call" }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByRole("button", { name: "Session: session-2" }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByRole("button", { name: "Approval: approval-2" }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByRole("button", { name: "Workflow run: run-2" }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByRole("button", { name: "Agent: agent-2" }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByRole("button", { name: "VM: vm-2" }).waitFor({ state: "visible" }));
  const detailStatus = page.locator(".tool-call-detail-panel .status-pill");
  await assert.doesNotReject(() => detailStatus.waitFor({ state: "visible" }));
  assert.match(await detailStatus.innerText(), /pending approval/i);

  const argumentsValue = await page.getByLabel("Arguments").inputValue();
  const resultValue = await page.getByLabel("Result").inputValue();
  const bodyText = await page.locator("body").innerText();

  assert.equal(
    await page.evaluate(() => window.location.hash),
    "#tool-calls?q=request-1&session_id=session-1&tool_name=email_send_approved&status=success&direct_only=false",
  );
  assert.equal(await navEntry.count(), 1);
  assert.equal(await rows.count(), 2);
  assert.ok(toolCallResponses.length >= 1);
  assert.ok(
    requestedEndpoints.some((endpoint) => endpoint.startsWith("/api/admin/tool-calls?")),
    "expected tool calls index request",
  );
  assert.ok(await page.getByRole("button", { name: "Approval: approval-2" }).isVisible());
  assert.match(argumentsValue, /<redacted>/);
  assert.match(resultValue, /pending_approval/);
  assert.doesNotMatch(argumentsValue, /secret-token/i);
  assert.doesNotMatch(resultValue, /secret-token/i);
  assert.doesNotMatch(bodyText, /secret-token/i);
  assert.doesNotMatch(bodyText, /raw-secret/i);
  assert.deepEqual(pageErrors, []);
  assert.deepEqual(consoleErrors, []);
});
