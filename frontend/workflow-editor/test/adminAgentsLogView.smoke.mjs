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

function logResponse({
  view,
  limit,
  offset,
  query,
  text,
  hasMoreBefore,
  totalLines,
  startLine,
  endLine,
}) {
  return {
    agent_id: "session-1",
    backend: "container_worker_vm",
    view,
    lines: 1,
    text,
    returned_lines: 1,
    has_more_before: hasMoreBefore,
    total_lines: totalLines,
    start_line: startLine,
    end_line: endLine,
    limit,
    offset,
    query,
  };
}

test("admin shell agents page forwards the selected log view through executions, threads, search, and older paging", async (t) => {
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
  const logRequests = [];
  const agentsResponse = {
    items: [
      {
        agent_id: "session-1",
        backend: "container_worker_vm",
        target_name: "Worker session",
        session_id: "session-1",
        directory_path: "root/team",
        status: "running",
        started_at: "2026-05-18T09:55:00Z",
        finished_at: null,
        updated_at: "2026-05-18T10:01:00Z",
        host: {
          state: "running",
          latest_execution_id: "exec-2",
          executions: [
            {
              execution_id: "exec-1",
              session_id: "session-1",
              worker_job_id: "job-1",
              worker_url: "http://worker-1",
              target_name: "Worker session",
              status: "running",
              last_status: "running",
              created_at: "2026-05-18T09:55:00Z",
              started_at: "2026-05-18T09:55:30Z",
              finished_at: null,
              updated_at: "2026-05-18T09:56:00Z",
              image: "worker-image:1",
              metadata: {},
            },
            {
              execution_id: "exec-2",
              session_id: "session-1",
              worker_job_id: "job-2",
              worker_url: "http://worker-2",
              target_name: "Worker session",
              status: "running",
              last_status: "running",
              created_at: "2026-05-18T09:59:00Z",
              started_at: "2026-05-18T09:59:30Z",
              finished_at: null,
              updated_at: "2026-05-18T10:01:00Z",
              image: "worker-image:2",
              metadata: {},
            },
          ],
        },
        capabilities: {
          start: false,
          shutdown: false,
          resume: false,
          enable_disable: false,
          kill: false,
          logs: true,
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
    execution_backend: "container_worker_vm",
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
    execution_backend: "container_worker_vm",
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
      assert.equal(url.searchParams.get("summary_only"), "true");
      assert.equal(url.searchParams.get("reconcile_containers"), "false");
      assert.equal(url.searchParams.get("omit_vm_state"), "true");
      assert.equal(url.searchParams.get("_dashboard"), "1");
      await route.fulfill(jsonResponse(agentsResponse));
      return;
    }

    if (pathname === "/api/admin/agents/session-1/log") {
      const view = url.searchParams.get("view");
      const limit = url.searchParams.get("limit");
      const offset = url.searchParams.get("offset");
      const query = url.searchParams.get("query");
      assert.equal(limit, "300");
      logRequests.push({ view, limit, offset, query });

      if (view === "threads") {
        if (query) {
          await route.fulfill(
            jsonResponse(
              logResponse({
                view,
                limit,
                offset,
                query,
                text: "thread search\n",
                hasMoreBefore: true,
                totalLines: 6,
                startLine: 4,
                endLine: 4,
              }),
            ),
          );
          return;
        }

        if (offset && offset !== "0") {
          await route.fulfill(
            jsonResponse(
              logResponse({
                view,
                limit,
                offset,
                query,
                text: "thread older\n",
                hasMoreBefore: false,
                totalLines: 6,
                startLine: 1,
                endLine: 3,
              }),
            ),
          );
          return;
        }

        await route.fulfill(
          jsonResponse(
            logResponse({
              view,
              limit,
              offset,
              query,
              text: "thread grouped\n",
              hasMoreBefore: true,
              totalLines: 6,
              startLine: 4,
              endLine: 6,
            }),
          ),
        );
        return;
      }

      await route.fulfill(
        jsonResponse(
          logResponse({
            view: "executions",
            limit,
            offset,
            query,
            text: "execution default\n",
            hasMoreBefore: false,
            totalLines: 2,
            startLine: 1,
            endLine: 2,
          }),
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

  await page.goto(`http://127.0.0.1:${address.port}/#agents?agent_id=session-1&session_id=session-1`, {
    waitUntil: "domcontentloaded",
  });

  const navEntry = page
    .locator('nav[aria-label="Admin sections"] .admin-shell-tab')
    .filter({ hasText: "Agents" });
  await ensureSectionsExpanded(page);
  await assert.doesNotReject(() => navEntry.waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByRole("heading", { name: "Agents" }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.locator(".feature-toolbar h2").waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByRole("tab", { name: "Threads" }).waitFor({ state: "visible" }));

  await assert.doesNotReject(() => page.getByText("execution default").waitFor({ state: "visible" }));
  assert.ok(
    logRequests.some(
      (request) => request.view === "executions" && request.limit === "300" && request.offset === "0" && request.query === null,
    ),
  );

  await page.getByRole("tab", { name: "Threads" }).click();
  await assert.doesNotReject(() => page.getByText("thread grouped").waitFor({ state: "visible" }));
  assert.ok(
    logRequests.some(
      (request) => request.view === "threads" && request.limit === "300" && request.offset === "0" && request.query === null,
    ),
  );

  await page.getByLabel("Search logs").fill("thread");
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await assert.doesNotReject(() => page.getByText("thread search").waitFor({ state: "visible" }));
  assert.ok(
    logRequests.some(
      (request) => request.view === "threads" && request.limit === "300" && request.offset === "0" && request.query === "thread",
    ),
  );

  assert.equal(await page.getByRole("button", { name: "Load more" }).isEnabled(), true);
  await page.getByRole("button", { name: "Load more" }).click();
  await page.waitForTimeout(1000);
  assert.ok(
    logRequests.some(
      (request) => request.view === "threads" && request.limit === "300" && request.offset !== "0" && request.query === "thread",
    ),
  );

  assert.equal(await page.evaluate(() => window.location.hash), "#agents?agent_id=session-1&session_id=session-1");
  assert.deepEqual(pageErrors, []);
  assert.deepEqual(consoleErrors, []);
  assert.ok(requestedEndpoints.some((endpoint) => endpoint.startsWith("/api/admin/agents?")));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/admin/agents/session-1/log?backend=container_worker_vm&limit=300&offset=0&view=executions"));
});
