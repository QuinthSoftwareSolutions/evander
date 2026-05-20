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

test("admin shell agents page deep-links to a selected agent", async (t) => {
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
  const agentsResponse = {
    items: [
      {
        agent_id: "agent-2",
        backend: "libvirt_vm",
        target_name: "Unrelated VM",
        session_id: "session-2",
        directory_path: "root/team",
        status: "running",
        started_at: "2026-05-18T09:59:00Z",
        finished_at: null,
        updated_at: "2026-05-18T10:00:00Z",
        host: {
          state: "running",
        },
        capabilities: {
          start: false,
          shutdown: false,
          resume: false,
          enable_disable: false,
          kill: false,
          logs: false,
          stop: false,
          destroy: false,
        },
      },
      {
        agent_id: "agent-1",
        backend: "libvirt_vm",
        target_name: "Dispatch agent",
        session_id: "session-1",
        directory_path: "root/team",
        status: "running",
        started_at: "2026-05-18T09:55:00Z",
        finished_at: null,
        updated_at: "2026-05-18T10:01:00Z",
        host: {
          state: "running",
        },
        capabilities: {
          start: false,
          shutdown: false,
          resume: false,
          enable_disable: false,
          kill: false,
          logs: false,
          stop: false,
          destroy: false,
        },
      },
    ],
    directory_path: "root/team",
    direct_only: true,
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

    if (pathname === "/api/admin/skills") {
      await route.fulfill(jsonResponse({ items: [] }));
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

  await page.goto(`http://127.0.0.1:${address.port}/#agents?agent_id=agent-1&session_id=session-1`, {
    waitUntil: "domcontentloaded",
  });

  const navEntry = page
    .locator('nav[aria-label="Admin sections"] .admin-shell-tab')
    .filter({ hasText: "Agents" });
  await page.getByRole("button", { name: "Expand sections" }).click();
  await assert.doesNotReject(() => navEntry.waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByRole("heading", { name: "Agents" }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.locator(".agent-list-item.selected").waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByRole("heading", { name: "Dispatch agent" }).waitFor({ state: "visible" }));
  assert.equal(await page.evaluate(() => window.location.hash), "#agents?agent_id=agent-1&session_id=session-1");
  assert.match(await page.locator(".agent-list-item.selected").innerText(), /Dispatch agent/);
  assert.match(await page.locator(".feature-toolbar h2").innerText(), /Dispatch agent/);
  assert.ok(requestedEndpoints.some((endpoint) => endpoint.startsWith("/api/admin/agents?")));
  assert.deepEqual(pageErrors, []);
  assert.deepEqual(consoleErrors, []);
});
