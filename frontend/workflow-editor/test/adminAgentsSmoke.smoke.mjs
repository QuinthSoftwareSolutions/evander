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

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
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
  page.setDefaultTimeout(5000);

  const requestedEndpoints = [];
  const agentListRequests = [];
  let manualRefreshRequested = false;
  let manualRefreshRequest = null;
  let manualSummaryRefreshRequest = null;
  let delayNextOperatorSteeringContext = false;
  const agentsResponse = {
    items: [
      {
        agent_id: "agent-2",
        backend: "container_worker_vm",
        target_name: "Container worker",
        session_id: "session-2",
        directory_path: "root/team",
        status: "running",
        started_at: "2026-05-18T09:59:00Z",
        finished_at: null,
        updated_at: "2026-05-18T10:00:00Z",
        host: {
          image: "runner:latest",
          image_alias: "default",
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
          labels: {
            "vm-type": "issue",
          },
          runtime_image: {
            backend: "libvirt_vm",
            kind: "libvirt_vm_template",
            label: "Libvirt VM template",
            template_alias: "dotnet",
            image_ref: "dotnet-agent.qcow2",
            image_ref_sanitized: true,
            missing_reason: null,
          },
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

    if (pathname === "/api/operator-steering/context") {
      const sessionId = url.searchParams.get("session_id");
      if (delayNextOperatorSteeringContext) {
        delayNextOperatorSteeringContext = false;
        await delay(500);
      }
      await route.fulfill(
        jsonResponse({
          session_id: sessionId,
          directory_path: "root/team",
          context: sessionId === "session-1"
            ? {
                session_id: "session-1",
                dispatch_id: "dispatch-1",
                workflow_run_id: "workflow-1",
                effect_id: "effect-1",
                node_id: "node-1",
                agent_harness: "codex",
                execution_backend: "libvirt_vm",
                directory_path: "root/team",
                model: "gpt-5.2-codex",
                trace_id: "trace-1",
                started_at: "2026-05-18T10:00:00Z",
                expires_at: "2026-05-18T10:30:00Z",
              }
            : null,
        }),
      );
      return;
    }

    if (pathname === "/api/operator-steering/notes") {
      await route.fulfill(
        jsonResponse({
          session_id: url.searchParams.get("session_id"),
          dispatch_id: url.searchParams.get("dispatch_id"),
          directory_path: "root/team",
          notes: [],
        }),
      );
      return;
    }

    if (pathname === "/api/admin/agents") {
      const requestIndex = agentListRequests.length;
      agentListRequests.push(`${pathname}${url.search}`);
      assert.equal(url.searchParams.get("directory_path"), "root");
      assert.equal(url.searchParams.get("direct_only"), "true");
      assert.equal(url.searchParams.get("include_inactive"), "true");
      assert.equal(url.searchParams.get("backend"), "all");
      if (requestIndex === 0) {
        assert.equal(url.searchParams.get("summary_only"), "true");
        assert.equal(url.searchParams.get("reconcile_containers"), "false");
        assert.equal(url.searchParams.get("omit_vm_state"), "true");
        assert.equal(url.searchParams.get("_dashboard"), "1");
      } else if (
        manualRefreshRequested &&
        manualRefreshRequest == null &&
        url.searchParams.get("summary_only") === "false"
      ) {
        assert.equal(url.searchParams.get("summary_only"), "false");
        assert.equal(url.searchParams.get("reconcile_containers"), "true");
        assert.equal(url.searchParams.get("omit_vm_state"), "false");
        assert.equal(url.searchParams.get("_dashboard"), null);
        manualRefreshRequest = `${pathname}${url.search}`;
      } else if (
        manualRefreshRequested &&
        manualSummaryRefreshRequest == null &&
        url.searchParams.get("_dashboard_refresh") === "1"
      ) {
        assert.equal(url.searchParams.get("summary_only"), "true");
        assert.equal(url.searchParams.get("reconcile_containers"), "false");
        assert.equal(url.searchParams.get("omit_vm_state"), "true");
        assert.equal(url.searchParams.get("_dashboard"), "1");
        assert.equal(url.searchParams.get("_dashboard_refresh"), "1");
        manualSummaryRefreshRequest = `${pathname}${url.search}`;
      } else {
        assert.equal(url.searchParams.get("summary_only"), "true");
        assert.equal(url.searchParams.get("reconcile_containers"), "false");
        assert.equal(url.searchParams.get("omit_vm_state"), "true");
        assert.equal(url.searchParams.get("_dashboard"), "1");
        assert.equal(url.searchParams.get("_dashboard_refresh"), null);
      }
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
  await ensureSectionsExpanded(page);
  await assert.doesNotReject(() => navEntry.waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByRole("heading", { name: "Agents" }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.locator(".agent-list-item.selected").waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByRole("heading", { name: "Dispatch agent" }).waitFor({ state: "visible" }));
  await assert.match(await page.locator(".agent-list-item.selected").innerText(), /VM template dotnet \/ dotnet-agent\.qcow2/);
  await assert.match(await page.locator(".feature-content").innerText(), /VM template/i);
  await assert.match(await page.locator(".feature-content").innerText(), /VM image ref/i);
  await assert.match(await page.locator(".feature-content").innerText(), /dotnet-agent\.qcow2/);
  await assert.match(await page.locator(".agent-inline-label").filter({ hasText: "vm-type" }).innerText(), /issue/);
  assert.equal(await page.locator(".panel-header h3").filter({ hasText: /^Labels$/ }).count(), 0);
  const steeringButton = page.getByRole("button", { name: "Operator steering" });
  await assert.doesNotReject(() => steeringButton.waitFor({ state: "visible", timeout: 5000 }));

  await assert.match(
    await page.locator(".agent-list-item").filter({ hasText: "Container worker" }).innerText(),
    /Container image runner:latest \(default\)/,
  );
  await page.getByRole("button", { name: /Container worker/ }).click();
  await assert.match(await page.locator(".feature-content").innerText(), /Container image/i);
  await assert.match(await page.locator(".feature-content").innerText(), /runner:latest/);

  await page.locator(".agent-list-item").filter({ hasText: "Dispatch agent" }).click();
  await assert.doesNotReject(() => page.getByRole("heading", { name: "Dispatch agent" }).waitFor({ state: "visible" }));
  await steeringButton.click();
  const steeringDialog = page.getByRole("dialog", { name: "Operator steering" });
  await assert.doesNotReject(() => steeringDialog.getByText("dispatch-1").first().waitFor({ state: "visible", timeout: 5000 }));
  await assert.match(await steeringDialog.innerText(), /dispatch-1/);

  const steeringDraft = "Please keep the current fix focused.";
  const steeringTextarea = steeringDialog.locator("textarea");
  await steeringTextarea.fill(steeringDraft);

  delayNextOperatorSteeringContext = true;
  const steeringContextRefreshRequest = page.waitForRequest((request) => {
    const url = new URL(request.url());
    return url.pathname === "/api/operator-steering/context";
  }, { timeout: 5000 });
  const steeringContextRefreshResponse = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === "/api/operator-steering/context";
  }, { timeout: 5000 });
  await steeringDialog.getByRole("button", { name: "Refresh operator steering" }).click();
  await steeringContextRefreshRequest;
  await page.waitForTimeout(50);
  assert.equal(await page.getByText("Loading steering context.").count(), 0);
  assert.equal(await steeringTextarea.inputValue(), steeringDraft);
  assert.match(await steeringDialog.innerText(), /dispatch-1/);
  await steeringContextRefreshResponse;
  await steeringDialog.getByRole("button", { name: "Close" }).click();

  manualRefreshRequested = true;
  const liveRefreshRequest = page.waitForRequest((request) => {
    if (!request.url().includes("/api/admin/agents?")) return false;
    const url = new URL(request.url());
    return (
      url.searchParams.get("summary_only") === "false" &&
      url.searchParams.get("reconcile_containers") === "true" &&
      url.searchParams.get("omit_vm_state") === "false" &&
      url.searchParams.get("_dashboard") === null
    );
  }, { timeout: 5000 });
  const summaryRefreshRequest = page.waitForRequest((request) => {
    if (!request.url().includes("/api/admin/agents?")) return false;
    const url = new URL(request.url());
    return (
      url.searchParams.get("summary_only") === "true" &&
      url.searchParams.get("reconcile_containers") === "false" &&
      url.searchParams.get("omit_vm_state") === "true" &&
      url.searchParams.get("_dashboard") === "1" &&
      url.searchParams.get("_dashboard_refresh") === "1"
    );
  }, { timeout: 5000 });
  await Promise.all([
    liveRefreshRequest,
    summaryRefreshRequest,
    page.locator(".feature-sidebar-header").getByRole("button", { name: "Refresh", exact: true }).click(),
  ]);
  assert.equal(await page.evaluate(() => window.location.hash), "#agents?agent_id=agent-1&session_id=session-1");
  assert.match(await page.locator(".agent-list-item.selected").innerText(), /Dispatch agent/);
  assert.match(await page.locator(".feature-toolbar h2").innerText(), /Dispatch agent/);
  await steeringButton.click();
  const reopenedSteeringDialog = page.getByRole("dialog", { name: "Operator steering" });
  await assert.doesNotReject(() => reopenedSteeringDialog.getByText("dispatch-1").first().waitFor({ state: "visible", timeout: 5000 }));
  assert.equal(await reopenedSteeringDialog.locator("textarea").inputValue(), steeringDraft);
  assert.ok(requestedEndpoints.some((endpoint) => endpoint.startsWith("/api/admin/agents?")));
  assert.ok(agentListRequests.length >= 1);
  assert.match(agentListRequests[0], /summary_only=true/);
  assert.match(agentListRequests[0], /reconcile_containers=false/);
  assert.match(agentListRequests[0], /omit_vm_state=true/);
  assert.match(agentListRequests[0], /_dashboard=1/);
  assert.ok(manualRefreshRequest);
  assert.match(manualRefreshRequest, /summary_only=false/);
  assert.match(manualRefreshRequest, /reconcile_containers=true/);
  assert.match(manualRefreshRequest, /omit_vm_state=false/);
  assert.equal(new URL(`http://localhost${manualRefreshRequest}`).searchParams.get("_dashboard"), null);
  assert.ok(manualSummaryRefreshRequest);
  assert.match(manualSummaryRefreshRequest, /summary_only=true/);
  assert.match(manualSummaryRefreshRequest, /reconcile_containers=false/);
  assert.match(manualSummaryRefreshRequest, /omit_vm_state=true/);
  assert.equal(new URL(`http://localhost${manualSummaryRefreshRequest}`).searchParams.get("_dashboard"), "1");
  assert.equal(new URL(`http://localhost${manualSummaryRefreshRequest}`).searchParams.get("_dashboard_refresh"), "1");
  assert.deepEqual(pageErrors, []);
  assert.deepEqual(consoleErrors, []);
});
