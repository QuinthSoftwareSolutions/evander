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

test("admin shell sessions page surfaces wake ledger entries and retry controls", async (t) => {
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
  const retryBodies = [];
  const sessionRecord = {
    session_id: "session-1",
    vm_id: "vm-1",
    model: "gpt-5.4",
    directory_path: "root/team",
    allowed_tools: ["prompt_agent"],
    approval_required_tools: [],
    effective_tool_policy: {
      allowed_tools: ["prompt_agent"],
      inherited_approval_required_tools: [],
      approval_required_tools: [],
      approval_tool_settings: {},
      mcp_tool_policies: {},
      tool_argument_policies: {},
    },
    allowed_script_names: [],
    discord_channel_allowlist: [],
    teams_channel_allowlist: [],
    communication_file_receive_enabled: false,
    communication_file_send_enabled: false,
    communication_file_max_bytes: null,
    communication_file_allowed_mime_types: [],
    communication_file_allowed_extensions: [],
    communication_file_blocked_extensions: [],
    communication_file_require_approval_for_send: false,
    web_search_enabled: false,
    expires_at: "2026-05-18T12:00:00Z",
    created_at: "2026-05-17T12:00:00Z",
    metadata: {},
    issue_number: null,
    is_expired: false,
  };
  const sessionListResponse = {
    items: [sessionRecord],
    active_items: [sessionRecord],
    expired_items: [],
    counts: {
      total: 1,
      active: 1,
      expired: 0,
    },
  };
  const sessionDetailResponse = {
    session: sessionRecord,
    wake_schedules: [],
    summary_json: {},
  };
  const sessionCatalogResponse = {
    tool_options: [
      {
        value: "prompt_agent",
        label: "Prompt agent",
      },
    ],
    resume_mode_options: [
      {
        value: "same_context",
        label: "Same context",
      },
    ],
    renew_extension_presets: ["1d"],
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
  const failedLedgerEntry = {
    request_id: "wake-failed",
    session_id: "session-1",
    source: "admin_web",
    resume_at: "2026-05-18T12:30:00Z",
    next_run_at: "2026-05-18T12:30:00Z",
    scheduled_for: "2026-05-18T12:30:00Z",
    schedule_cron: null,
    created_at: "2026-05-17T12:15:00Z",
    updated_at: "2026-05-17T12:20:00Z",
    status: "failed",
    status_reason: "wake dispatch failed with status failed",
    status_updated_at: "2026-05-17T12:20:00Z",
    prompt_summary: "Resume work on the release checklist.",
    redacted_prompt: "[redacted]",
    latest_dispatch: {
      dispatch_id: "dispatch-failed",
      status: "failed",
      dispatched_at: "2026-05-17T12:20:00Z",
      source: "admin_web",
      error: "stack trace with token secret",
    },
    latest_dispatch_id: "dispatch-failed",
    latest_dispatch_status: "failed",
    last_error: "wake dispatch failed with status failed",
    last_error_at: "2026-05-17T12:20:00Z",
    completed_at: null,
    completion_evidence: {},
    retry_available: true,
    failure_reason: "wake dispatch failed with status failed",
    enabled: true,
  };
  const completedLedgerEntry = {
    request_id: "wake-completed",
    session_id: "session-1",
    source: "admin_web",
    resume_at: "2026-05-18T12:45:00Z",
    next_run_at: null,
    scheduled_for: "2026-05-18T12:45:00Z",
    schedule_cron: null,
    created_at: "2026-05-17T12:25:00Z",
    updated_at: "2026-05-17T12:40:00Z",
    status: "completed_or_acknowledged",
    status_reason: null,
    status_updated_at: "2026-05-17T12:40:00Z",
    prompt_summary: "Already completed work.",
    redacted_prompt: "[redacted]",
    latest_dispatch: {
      dispatch_id: "dispatch-completed",
      status: "dispatched",
      dispatched_at: "2026-05-17T12:35:00Z",
      source: "admin_web",
      error: null,
    },
    latest_dispatch_id: "dispatch-completed",
    latest_dispatch_status: "dispatched",
    last_error: null,
    last_error_at: null,
    completed_at: "2026-05-17T12:40:00Z",
    completion_evidence: {
      dispatch_id: "dispatch-completed",
      trigger_type: "agent_run_completed",
    },
    retry_available: false,
    failure_reason: null,
    enabled: false,
  };
  let ledgerResponse = {
    items: [failedLedgerEntry, completedLedgerEntry],
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

    if (pathname === "/api/admin/sessions/catalog") {
      await route.fulfill(jsonResponse(sessionCatalogResponse));
      return;
    }

    if (pathname === "/api/admin/sessions") {
      assert.equal(url.searchParams.get("include_expired"), "true");
      assert.equal(url.searchParams.get("directory_path"), "root");
      assert.equal(url.searchParams.get("direct_only"), "true");
      await route.fulfill(jsonResponse(sessionListResponse));
      return;
    }

    if (pathname === "/api/admin/sessions/session-1") {
      await route.fulfill(jsonResponse(sessionDetailResponse));
      return;
    }

    if (pathname === "/api/admin/scheduled-wakes") {
      assert.equal(url.searchParams.get("session_id"), "session-1");
      assert.equal(url.searchParams.get("limit"), "100");
      assert.equal(url.searchParams.get("include_completed"), "true");
      await route.fulfill(jsonResponse(ledgerResponse));
      return;
    }

    if (pathname === "/api/admin/scheduled-wakes/wake-failed/retry") {
      const body = JSON.parse(route.request().postData() ?? "{}");
      retryBodies.push(body);
      assert.deepEqual(body, { run_now: true });
      await route.fulfill(
        jsonResponse({
          item: {
            ...failedLedgerEntry,
            status: "pending",
            status_reason: null,
            retry_available: false,
            latest_dispatch_status: "failed",
          },
        }),
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

  await page.goto(`http://127.0.0.1:${address.port}/#sessions?session_id=session-1`, {
    waitUntil: "domcontentloaded",
  });

  const ledgerSection = page.locator(".wake-ledger-section");

  await ensureSectionsExpanded(page);
  await assert.doesNotReject(() => page.getByRole("heading", { name: "Sessions" }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => ledgerSection.getByRole("heading", { name: "Wake ledger" }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => ledgerSection.getByText("wake-failed").waitFor({ state: "visible" }));
  await assert.doesNotReject(() => ledgerSection.getByText("wake-completed").waitFor({ state: "visible" }));
  await ledgerSection.getByLabel("Status").selectOption("failed");
  await assert.doesNotReject(() => ledgerSection.getByText("wake-failed").waitFor({ state: "visible" }));
  await assert.doesNotReject(() => ledgerSection.getByText("wake-completed").waitFor({ state: "hidden" }));
  await assert.doesNotReject(() => ledgerSection.getByRole("button", { name: "Retry now" }).waitFor({ state: "visible" }));
  await ledgerSection.getByRole("button", { name: "Retry now" }).click();
  await assert.doesNotReject(() =>
    page.getByText("Retried wake schedule wake-failed.").waitFor({ state: "visible" }),
  );

  assert.equal(retryBodies.length, 1);
  assert.ok(requestedEndpoints.some((endpoint) => endpoint.startsWith("/api/admin/scheduled-wakes?session_id=session-1")));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/admin/scheduled-wakes/wake-failed/retry"));
  assert.deepEqual(pageErrors, []);
  assert.deepEqual(consoleErrors, []);
});
