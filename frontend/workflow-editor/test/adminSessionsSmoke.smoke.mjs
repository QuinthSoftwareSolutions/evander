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

test("admin shell sessions page clears approval gates when the allowed tool is removed", async (t) => {
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
  const allowedToolsBodies = [];
  const policyBodies = [];
  const sessionRecord = {
    session_id: "session-1",
    vm_id: "vm-1",
    model: "gpt-5.4",
    directory_path: "root/team",
    allowed_tools: ["prompt_agent"],
    approval_required_tools: ["prompt_agent"],
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
      if (route.request().method() === "PATCH") {
        const body = JSON.parse(route.request().postData() ?? "{}");
        policyBodies.push(body);
        assert.deepEqual(body.approval_required_tools, []);
        assert.deepEqual(body.allowed_script_names, []);
        assert.deepEqual(body.discord_channel_allowlist, []);
        assert.deepEqual(body.teams_channel_allowlist, []);
        assert.equal(body.communication_file_receive_enabled, false);
        assert.equal(body.communication_file_send_enabled, false);
        assert.equal(body.communication_file_require_approval_for_send, false);
        sessionRecord.approval_required_tools = body.approval_required_tools;
        await route.fulfill(jsonResponse({ action: "updated", message: "Updated session policy.", session: sessionRecord }));
        return;
      }
      await route.fulfill(jsonResponse(sessionDetailResponse));
      return;
    }

    if (pathname === "/api/admin/sessions/session-1/allowed-tools") {
      const body = JSON.parse(route.request().postData() ?? "{}");
      allowedToolsBodies.push(body);
      assert.deepEqual(body.allowed_tools, []);
      sessionRecord.allowed_tools = body.allowed_tools;
      await route.fulfill(jsonResponse({ action: "updated", message: "Updated allowed tools.", session: sessionRecord }));
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

  await page.goto(`http://127.0.0.1:${address.port}/#sessions`, {
    waitUntil: "domcontentloaded",
  });

  const navEntry = page
    .locator('nav[aria-label="Admin sections"] .admin-shell-tab')
    .filter({ hasText: "Sessions" });
  await page.getByRole("button", { name: "Expand sections" }).click();
  await assert.doesNotReject(() => navEntry.waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByRole("heading", { name: "Sessions" }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByLabel("Prompt agent").waitFor({ state: "visible" }));

  const allowedTool = page.getByLabel("Prompt agent");
  const approvalGate = page.getByLabel("Require approval for Prompt agent");
  const saveButton = page.getByRole("button", { name: "Save changes" });

  assert.equal(await allowedTool.isChecked(), true);
  assert.equal(await approvalGate.isChecked(), true);
  assert.equal(await approvalGate.isDisabled(), false);

  await allowedTool.uncheck();
  await assert.doesNotReject(() => approvalGate.waitFor({ state: "visible" }));
  assert.equal(await approvalGate.isChecked(), false);
  assert.equal(await approvalGate.isDisabled(), true);

  await saveButton.click();
  await assert.doesNotReject(() => page.getByText("Saved.").waitFor({ state: "visible" }));

  assert.equal(await page.evaluate(() => window.location.hash), "#sessions");
  assert.equal(await navEntry.count(), 1);
  assert.equal(await allowedToolsBodies.length, 1);
  assert.equal(await policyBodies.length, 1);
  assert.deepEqual(sessionRecord.allowed_tools, []);
  assert.deepEqual(sessionRecord.approval_required_tools, []);
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/admin/sessions/session-1/allowed-tools"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/admin/sessions/session-1"));
  assert.deepEqual(pageErrors, []);
  assert.deepEqual(consoleErrors, []);
});
