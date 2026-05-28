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

test("admin shell approvals detail renders intended effect and payload digest", async (t) => {
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
  const approvalRequest = {
    request_id: "approval-1",
    session_id: "session-1",
    action_type: "mcp_tool_invocation",
    tool_name: "email_send_approved",
    payload_digest: "digest-approval-1",
    human_summary: "Approve sending the email draft.",
    intended_effect: "Send the approved email payload to the recipient.",
    directory_path: "root/team",
    vm_id: "vm-1",
    status: "pending",
    created_at: "2026-05-18T10:00:00Z",
    expires_at: "2026-05-18T11:00:00Z",
    payload: {
      tool_name: "email_send_approved",
      session_id: "session-1",
    },
    decision_history: [],
  };
  const approvalIndexResponse = {
    items: [approvalRequest],
    health: {
      total: 1,
      pending: 1,
      approved: 0,
      denied: 0,
      expired: 0,
      oldest_pending_age_seconds: 90,
    },
  };
  const approvalDetailResponse = {
    request: approvalRequest,
    redacted_payload: {
      tool_name: "email_send_approved",
      session_id: "session-1",
      secret: "<redacted>",
    },
    audit_context: [],
    age_seconds: 90,
    expires_in_seconds: 3_600,
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

    if (pathname === "/api/admin/approvals") {
      assert.equal(url.searchParams.get("statuses"), "pending");
      assert.equal(url.searchParams.get("directory_path"), "root");
      assert.equal(url.searchParams.get("direct_only"), "true");
      await route.fulfill(jsonResponse(approvalIndexResponse));
      return;
    }

    if (pathname === "/api/admin/approvals/approval-1") {
      await route.fulfill(jsonResponse(approvalDetailResponse));
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

  await page.goto(`http://127.0.0.1:${address.port}/#approvals`, {
    waitUntil: "domcontentloaded",
  });

  const navEntry = page
    .locator('nav[aria-label="Admin sections"] .admin-shell-tab')
    .filter({ hasText: "Approvals" });
  await page.getByRole("button", { name: "Expand sections" }).click();
  await assert.doesNotReject(() => navEntry.waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByRole("heading", { name: "Approvals" }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByRole("heading", { name: "approval-1" }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByRole("heading", { name: "Summary" }).waitFor({ state: "visible" }));
  assert.equal(await page.getByRole("heading", { name: "Queue health" }).count(), 0);
  await assert.doesNotReject(() =>
    page.locator(".approval-review-summary p.muted-copy").filter({ hasText: "Intended effect:" }).waitFor({ state: "visible" }),
  );
  await assert.doesNotReject(() =>
    page.locator(".approval-review-summary").getByText("Send the approved email payload to the recipient.", { exact: true }).waitFor({ state: "visible" }),
  );
  await assert.doesNotReject(() =>
    page.locator(".approval-review-summary p.muted-copy").filter({ hasText: "Payload digest:" }).waitFor({ state: "visible" }),
  );
  await assert.doesNotReject(() =>
    page.locator(".approval-review-summary").getByText("digest-approval-1", { exact: true }).waitFor({ state: "visible" }),
  );

  assert.equal(await page.evaluate(() => window.location.hash), "#approvals");
  assert.equal(await navEntry.count(), 1);
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/admin/approvals/approval-1"));
  assert.deepEqual(pageErrors, []);
  assert.deepEqual(consoleErrors, []);
});
