import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import path from "node:path";
import test from "node:test";

import { chromium } from "playwright";
import { createServer } from "vite";

import { ensureSectionsExpanded } from "./adminShellHelpers.mjs";

function jsonResponse(payload, status = 200) {
  return {
    status,
    contentType: "application/json",
    body: JSON.stringify(payload),
  };
}

test("admin shell routes to approvals and renders an empty queue", async (t) => {
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
  const detailRequests = [];
  const approvalsIndexResponse = {
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
      unlocked: false,
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

    if (pathname === "/api/configuration/state") {
      assert.equal(url.searchParams.get("directory_path"), "root");
      await route.fulfill(jsonResponse(configurationStateResponse));
      return;
    }

    if (pathname === "/api/admin/approvals") {
      assert.equal(url.searchParams.get("statuses"), "pending");
      assert.equal(url.searchParams.get("directory_path"), "root");
      assert.equal(url.searchParams.get("direct_only"), "false");
      await route.fulfill(jsonResponse(approvalsIndexResponse));
      return;
    }

    if (pathname.startsWith("/api/admin/approvals/")) {
      detailRequests.push(`${pathname}${url.search}`);
      await route.fulfill(
        jsonResponse(
          {
            error: "detail requests should not be made for an empty approvals queue",
          },
          404,
        ),
      );
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
  await ensureSectionsExpanded(page);
  await assert.doesNotReject(() => navEntry.waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByRole("heading", { name: "Approvals" }).waitFor({ state: "visible" }));
  const sidebar = page.locator(".approvals-page .feature-sidebar");
  await sidebar.getByText("More filters", { exact: true }).click();
  assert.equal(await sidebar.getByLabel("Exclude sub-directories").isChecked(), false);
  await sidebar.getByText("Exclude sub-directories", { exact: true }).hover();
  await assert.doesNotReject(() =>
    page.getByRole("tooltip").getByText("Checked means only the selected directory is searched and child directories are excluded.", { exact: true }).waitFor({ state: "visible" }),
  );
  await sidebar.getByText("Session", { exact: true }).hover();
  await assert.doesNotReject(() =>
    page.getByRole("tooltip").getByText("Filter approvals by session ID.", { exact: true }).waitFor({ state: "visible" }),
  );
  await sidebar.getByText("VM", { exact: true }).hover();
  await assert.doesNotReject(() =>
    page.getByRole("tooltip").getByText("Filter approvals by VM ID.", { exact: true }).waitFor({ state: "visible" }),
  );
  await sidebar.getByText("Tool", { exact: true }).hover();
  await assert.doesNotReject(() =>
    page.getByRole("tooltip").getByText("Filter approvals by the requested tool name.", { exact: true }).waitFor({ state: "visible" }),
  );
  await sidebar.getByText("Older than (minutes)", { exact: true }).hover();
  await assert.doesNotReject(() =>
    page.getByRole("tooltip").getByText("Show requests older than the entered number of minutes.", { exact: true }).waitFor({ state: "visible" }),
  );
  await assert.doesNotReject(() => page.getByText("No approval requests matched the current filters.").waitFor({ state: "visible" }));

  assert.equal(await page.evaluate(() => window.location.hash), "#approvals");
  assert.equal(await navEntry.count(), 1);
  assert.equal(detailRequests.length, 0);
  assert.deepEqual(pageErrors, []);
  assert.deepEqual(consoleErrors, []);
  assert.ok(
    requestedEndpoints.some((endpoint) => endpoint.startsWith("/api/admin/approvals?")),
    "expected approvals index request",
  );
});
