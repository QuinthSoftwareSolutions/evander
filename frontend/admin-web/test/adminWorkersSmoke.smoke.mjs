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

function workerRecord(overrides = {}) {
  return {
    worker_id: "trigger-orchestrator",
    name: "Trigger Orchestrator",
    unit: "evander-trigger-orchestrator.service",
    active_state: "active",
    sub_state: "running",
    result: "success",
    unit_file_state: "enabled",
    description: "Routes workflow triggers.",
    main_pid: 4101,
    running: true,
    failed: false,
    detail: "Healthy; last event 18 seconds ago.",
    ...overrides,
  };
}

test("workers page supports compact navigation, non-overlapping polling, and action refresh", { timeout: 30000 }, async (t) => {
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
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  page.setDefaultTimeout(5000);
  await page.clock.install();
  const requests = { list: 0, detail: 0, log: 0, action: 0 };
  let nextLogGate = null;

  function blockNextLog() {
    let release;
    let entered;
    const gate = {
      waiting: new Promise((resolve) => { release = resolve; }),
      entered: new Promise((resolve) => { entered = resolve; }),
      release: () => release(),
      markEntered: () => entered(),
    };
    nextLogGate = gate;
    return gate;
  }

  const workers = [
    workerRecord(),
    workerRecord({
      worker_id: "effect-orchestrator",
      name: "Effect Orchestrator",
      unit: "evander-effect-orchestrator.service",
      main_pid: 4102,
      description: "Executes workflow effects.",
      detail: "Healthy; queue depth 1.",
    }),
    workerRecord({
      worker_id: "failed-worker",
      name: "Failed Worker",
      unit: "evander-failed-worker.service",
      active_state: "failed",
      sub_state: "failed",
      result: "exit-code",
      unit_file_state: "enabled",
      main_pid: null,
      running: false,
      failed: true,
      detail: "Exited with status 1.",
    }),
    workerRecord({
      worker_id: "stopped-worker",
      name: "Stopped Worker",
      unit: "evander-stopped-worker.service",
      active_state: "inactive",
      sub_state: "dead",
      result: "success",
      unit_file_state: "disabled",
      main_pid: null,
      running: false,
      detail: "Disabled by operator policy.",
    }),
  ];
  for (let index = 5; index <= 28; index += 1) {
    workers.push(workerRecord({
      worker_id: `worker-${index}`,
      name: `Background Worker ${String(index).padStart(2, "0")}`,
      unit: `evander-background-${index}.service`,
      main_pid: 4200 + index,
      detail: `Healthy background worker ${index}.`,
    }));
  }

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
    privileged_operations: { unlocked: true },
  };
  const adminStateResponse = {
    selected_directory_path: "root",
    directory_scoped_config_enabled: false,
    execution_backend: "libvirt_vm",
    permissions: ["admin.owner"],
    effective_permissions: ["admin.owner"],
    can_manage_users: true,
    privileged_operations: { unlocked: true },
    assistant: {
      enabled: false,
      show_launcher_when_disabled: false,
    },
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
      data: {
        directory: {
          id: "root",
          name: "root",
          parent_id: null,
          created_at: "2026-07-23T00:00:00Z",
          metadata: {},
          path: "root",
        },
        config_domains: [],
        children: [],
      },
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

    if (pathname === "/api/auth/session") {
      await route.fulfill(jsonResponse(sessionResponse));
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
      await route.fulfill(jsonResponse(adminStateResponse));
      return;
    }
    if (pathname === "/api/configuration/state") {
      await route.fulfill(jsonResponse(configurationStateResponse));
      return;
    }
    if (pathname === "/api/admin/skills") {
      await route.fulfill(jsonResponse({ items: [] }));
      return;
    }
    if (pathname === "/api/workers") {
      requests.list += 1;
      await route.fulfill(jsonResponse({ workers }));
      return;
    }
    const workerActionMatch = pathname.match(/^\/api\/workers\/([^/]+)\/actions\/start$/);
    if (workerActionMatch && route.request().method() === "POST") {
      requests.action += 1;
      const worker = workers.find((candidate) => candidate.worker_id === decodeURIComponent(workerActionMatch[1]));
      Object.assign(worker, { active_state: "active", sub_state: "running", running: true, detail: "Started successfully." });
      await route.fulfill(jsonResponse({ result: { message: "Worker started successfully." } }));
      return;
    }
    const workerLogMatch = pathname.match(/^\/api\/workers\/([^/]+)\/log$/);
    if (workerLogMatch) {
      const logRequest = ++requests.log;
      const gate = nextLogGate;
      nextLogGate = null;
      if (gate) {
        gate.markEntered();
        await gate.waiting;
      }
      const workerId = decodeURIComponent(workerLogMatch[1]);
      const worker = workers.find((candidate) => candidate.worker_id === workerId) ?? workers[0];
      await route.fulfill(jsonResponse({
        worker_id: worker.worker_id,
        worker_name: worker.name,
        unit: worker.unit,
        lines: 200,
        text: `${worker.name} log is healthy. Request ${logRequest}.\n`,
      }));
      return;
    }
    const workerMatch = pathname.match(/^\/api\/workers\/([^/]+)$/);
    if (workerMatch) {
      requests.detail += 1;
      const workerId = decodeURIComponent(workerMatch[1]);
      const worker = workers.find((candidate) => candidate.worker_id === workerId) ?? workers[0];
      await route.fulfill(jsonResponse({
        summary: worker,
        config: {
          worker_id: worker.worker_id,
          path: `/tmp/${worker.worker_id}.json`,
          exists: false,
          config: {},
          revision: `${worker.worker_id}-revision-1`,
        },
        config_schema: {
          worker_id: worker.worker_id,
          fields: [
            { field_name: "enabled", label: "Enable polling", kind: "bool", options: [], tooltip: "Poll for new events." },
            { field_name: "poll_interval", label: "Polling interval", kind: "int", options: [], tooltip: "Seconds between polls." },
          ],
        },
      }));
      return;
    }
    await route.continue();
  });

  const pageErrors = [];
  const consoleErrors = [];
  page.on("pageerror", (error) => pageErrors.push(error));
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });

  await page.goto(`http://127.0.0.1:${address.port}/#workers`, { waitUntil: "domcontentloaded" });
  await ensureSectionsExpanded(page);
  await page.getByRole("heading", { name: "Workers", exact: true }).waitFor({ state: "visible" });
  await page.getByRole("heading", { name: "Select a worker to view details", exact: true }).waitFor();
  assert.equal(await page.getByRole("heading", { name: "Global settings", exact: true }).isVisible(), false);
  assert.equal(requests.detail, 0);
  await page.locator(".worker-list-item").filter({ hasText: "Trigger Orchestrator" }).click();
  await page.locator(".worker-list-item.selected").waitFor({ state: "visible" });

  const pollingControl = page.getByRole("combobox", { name: "Enable polling", exact: true });
  await pollingControl.waitFor();
  assert.deepEqual(await pollingControl.locator("option").allTextContents(), ["No", "Yes"]);
  assert.equal(await page.getByRole("spinbutton", { name: "Polling interval", exact: true }).isVisible(), true);

  const selectedListText = await page.locator(".worker-list-item.selected").innerText();
  assert.match(selectedListText, /Trigger Orchestrator/);
  assert.match(selectedListText, /running/i);
  assert.doesNotMatch(selectedListText, /evander-trigger-orchestrator/);
  assert.doesNotMatch(selectedListText, /pid 4101/i);

  const selectedTooltip = page.locator(".worker-list-item-tooltip").filter({
    has: page.locator(".worker-list-item.selected"),
  });
  await selectedTooltip.hover();
  const tooltip = page.getByRole("tooltip").filter({ hasText: "Worker id: trigger-orchestrator" });
  await tooltip.waitFor({ state: "visible" });
  assert.match(await tooltip.innerText(), /Unit: evander-trigger-orchestrator\.service/);
  assert.match(await tooltip.innerText(), /Main PID: 4101/);

  const workerList = page.locator(".worker-list");
  const mainScrollTopBefore = await page.locator(".admin-shell-main").evaluate((element) => element.scrollTop);
  assert.equal(await workerList.evaluate((element) => getComputedStyle(element).overflowY), "auto");
  assert.equal(await workerList.evaluate((element) => element.scrollHeight > element.clientHeight), true);
  await workerList.evaluate((element) => {
    element.scrollTop = element.scrollHeight;
  });
  assert.ok(await workerList.evaluate((element) => element.scrollTop > 0));
  assert.equal(await page.locator(".admin-shell-main").evaluate((element) => element.scrollTop), mainScrollTopBefore);

  const search = page.getByRole("searchbox", { name: "Find worker" });
  await search.fill("queue depth");
  await page.locator(".worker-list-item").filter({ hasText: "Effect Orchestrator" }).waitFor({ state: "visible" });
  assert.equal(await page.locator(".worker-list-item").count(), 1);
  assert.match(await page.locator(".workers-list-summary").innerText(), /1 of 28 workers/);

  await page.getByRole("button", { name: "Clear filters" }).click();
  await page.getByRole("combobox", { name: "Status" }).selectOption("failed");
  assert.equal(await page.locator(".worker-list-item").count(), 1);
  assert.match(await page.locator(".worker-list-item").innerText(), /Failed Worker/);

  await page.getByRole("button", { name: "Clear filters" }).click();
  await page.getByRole("button", { name: "Stopped Worker, inactive" }).click();
  await page.getByRole("heading", { name: "Stopped Worker", exact: true }).waitFor({ state: "visible" });
  const overview = page.locator(".worker-overview-panel");
  assert.match(await overview.innerText(), /Unit file\s+disabled/i);
  assert.match(await overview.innerText(), /Detail\s+Disabled by operator policy\./i);

  // A slow poll must finish before the next polling delay begins.
  await page.getByRole("button", { name: "Reload worker", exact: true }).waitFor({ state: "visible" });
  await page.clock.pauseAt(await page.evaluate(() => Date.now() + 1000));
  const slowPoll = blockNextLog();
  await page.clock.runFor(5000);
  await slowPoll.entered;
  const pendingRequests = { ...requests };
  await page.clock.runFor(15000);
  assert.deepEqual(requests, pendingRequests, "a pending poll must not overlap later polls");
  slowPoll.release();
  await page.locator(".worker-log-view").filter({ hasText: `Request ${pendingRequests.log}.` }).waitFor({ state: "visible" });

  // Starting a worker refreshes its data immediately, while polling time is paused.
  const beforeAction = { ...requests };
  await page.getByRole("button", { name: "Start worker", exact: true }).click();
  await page.getByText("Worker started successfully.", { exact: true }).waitFor({ state: "visible" });
  assert.equal(requests.action, beforeAction.action + 1);
  assert.ok(requests.list > beforeAction.list);
  assert.ok(requests.detail > beforeAction.detail);
  assert.ok(requests.log > beforeAction.log);
  assert.match(await overview.innerText(), /Started successfully\./);
  await page.getByRole("button", { name: "Stop worker", exact: true }).waitFor({ state: "visible" });

  // Unmounting clears a scheduled timer.
  await page.evaluate(() => { window.location.hash = "#skills"; });
  await page.getByRole("heading", { name: "Skills", exact: true }).waitFor({ state: "visible" });
  const afterUnmount = { ...requests };
  await page.clock.runFor(15000);
  assert.deepEqual(requests, afterUnmount, "leaving Workers must clear its polling timer");

  // Completing an in-flight request after unmount must not recreate a timer.
  await page.evaluate(() => { window.location.hash = "#workers"; });
  await page.locator(".worker-list-item").filter({ hasText: "Trigger Orchestrator" }).click();
  await page.getByRole("button", { name: "Reload worker", exact: true }).waitFor({ state: "visible" });
  const unmountedPoll = blockNextLog();
  await page.clock.runFor(5000);
  await unmountedPoll.entered;
  await page.evaluate(() => { window.location.hash = "#skills"; });
  await page.getByRole("heading", { name: "Skills", exact: true }).waitFor({ state: "visible" });
  const duringUnmount = { ...requests };
  const responseFinished = page.waitForResponse((response) => new URL(response.url()).pathname.endsWith("/log"));
  unmountedPoll.release();
  await (await responseFinished).finished();
  await page.clock.runFor(15000);
  assert.deepEqual(requests, duringUnmount, "a completed request must not restart polling after unmount");

  assert.deepEqual(pageErrors, []);
  assert.deepEqual(consoleErrors, []);
});
