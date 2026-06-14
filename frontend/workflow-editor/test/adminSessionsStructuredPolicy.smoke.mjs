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

test("sessions structured policy round-trips allowed tools, metadata, and extension validation", async (t) => {
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

  const policyBodies = [];
  const allowedToolBodies = [];
  const sessionRecord = {
    session_id: "session-1",
    vm_id: "vm-1",
    model: "gpt-5.4",
    directory_path: "root/team",
    allowed_tools: ["prompt_agent"],
    approval_required_tools: ["prompt_agent"],
    approval_notification_target_ids: ["notify-target-1"],
    approval_notification_target_options: [
      {
        value: "notify-target-1",
        label: "Notify target",
      },
    ],
    effective_tool_policy: {
      allowed_tools: ["prompt_agent"],
      inherited_approval_required_tools: [],
      approval_required_tools: ["prompt_agent"],
      approval_tool_settings: {},
      mcp_tool_policies: {},
      tool_argument_policies: {},
    },
    allowed_script_names: ["sync-session"],
    discord_channel_allowlist: ["discord-channel-1"],
    teams_channel_allowlist: ["team-1/channel-2"],
    communication_file_receive_enabled: false,
    communication_file_send_enabled: false,
    communication_file_max_bytes: null,
    communication_file_allowed_mime_types: ["text/plain"],
    communication_file_allowed_extensions: [".txt"],
    communication_file_blocked_extensions: [".exe"],
    communication_file_require_approval_for_send: false,
    web_search_enabled: false,
    expires_at: "2026-05-18T12:00:00Z",
    created_at: "2026-05-17T12:00:00Z",
    metadata: {
      ticket: "INC-123",
      labels: {
        team: "ops",
      },
    },
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
      await route.fulfill(
        jsonResponse({
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
          permissions: [],
          effective_permissions: [],
          privileged_operations: {
            unlocked: true,
          },
        }),
      );
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
      await route.fulfill(
        jsonResponse({
          selected_directory_path: "root",
          directory_scoped_config_enabled: false,
          execution_backend: "libvirt_vm",
          role: "admin",
          can_manage_users: true,
          permissions: [],
          effective_permissions: [],
          privileged_operations: {
            unlocked: true,
          },
          assistant: {
            enabled: false,
            show_launcher_when_disabled: false,
          },
        }),
      );
      return;
    }

    if (pathname === "/api/configuration/state") {
      await route.fulfill(
        jsonResponse({
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
                created_at: "2026-05-16T00:00:00Z",
                metadata: {},
                path: "root",
              },
              config_domains: [],
              children: [],
            },
            errors: [],
            validation_diagnostics: [],
          },
        }),
      );
      return;
    }

    if (pathname === "/api/admin/sessions/catalog") {
      await route.fulfill(
        jsonResponse({
          tool_options: [
            {
              value: "prompt_agent",
              label: "Prompt agent",
            },
          ],
          resume_mode_options: [],
          renew_extension_presets: ["1d"],
        }),
      );
      return;
    }

    if (pathname === "/api/admin/sessions") {
      await route.fulfill(jsonResponse(sessionListResponse));
      return;
    }

    if (pathname === "/api/admin/sessions/session-1") {
      if (route.request().method() === "PATCH") {
        const body = JSON.parse(route.request().postData() ?? "{}");
        policyBodies.push(body);
        assert.deepEqual(body.metadata, sessionRecord.metadata);
        assert.deepEqual(body.approval_required_tools, []);
        sessionRecord.approval_required_tools = body.approval_required_tools;
        await route.fulfill(jsonResponse({ action: "updated", message: "Updated session policy.", session: sessionRecord }));
        return;
      }
      await route.fulfill(jsonResponse(sessionDetailResponse));
      return;
    }

    if (pathname === "/api/admin/sessions/session-1/allowed-tools") {
      const body = JSON.parse(route.request().postData() ?? "{}");
      allowedToolBodies.push(body);
      assert.deepEqual(body.allowed_tools, []);
      sessionRecord.allowed_tools = body.allowed_tools;
      await route.fulfill(jsonResponse({ action: "updated", message: "Updated allowed tools.", session: sessionRecord }));
      return;
    }

    await route.continue();
  });

  await page.goto(`http://127.0.0.1:${address.port}/#sessions?session_id=session-1`, {
    waitUntil: "domcontentloaded",
  });

  await ensureSectionsExpanded(page);
  await page.getByRole("heading", { name: "Sessions" }).waitFor({ state: "visible" });
  await page.locator(".session-list-item.selected").waitFor({ state: "visible" });

  const metadataSection = page.locator(".field-stack").filter({ hasText: "Metadata" });
  await page.locator(".session-tool-group-summary").filter({ hasText: "Agent control" }).click();
  const promptAgentRow = page.locator(".session-tool-row").filter({ hasText: "Prompt agent" });
  const allowedTool = promptAgentRow.locator(".session-tool-row-main input[type='checkbox']").first();

  assert.equal(await allowedTool.isChecked(), true);
  await allowedTool.uncheck();
  await allowedTool.check();
  await allowedTool.uncheck();

  const metadataLabelsRow = metadataSection.locator(".structured-input-json-row").nth(1);
  const metadataValue = metadataLabelsRow.locator("textarea").first();
  await metadataValue.fill("{bad");
  await page.getByRole("button", { name: "Save changes" }).click();
  await metadataSection.locator(".structured-input-error").waitFor({ state: "visible" });
  await page.locator(".callout.error-callout").filter({ hasText: "Metadata row 2 contains invalid JSON" }).first().waitFor({ state: "visible" });
  assert.equal(policyBodies.length, 0);
  await metadataValue.fill('{\n  "team": "ops"\n}');
  await metadataSection.locator(".structured-input-error").waitFor({ state: "hidden" });

  const allowedExtensionsSection = page.locator(".field-stack").filter({ hasText: "Allowed extensions" });
  const allowedExtensionsInput = allowedExtensionsSection.locator(".string-list-selector-row input").first();
  await allowedExtensionsInput.fill("txt");
  await allowedExtensionsSection.locator(".structured-input-error").filter({ hasText: "Extension must start with a leading dot." }).waitFor({ state: "visible" });
  await page.getByRole("button", { name: "Save changes" }).click();
  await page.locator(".callout.error-callout").filter({ hasText: "Allowed extensions row 1 must start with a leading dot." }).first().waitFor({ state: "visible" });
  assert.equal(policyBodies.length, 0);
  await allowedExtensionsInput.fill(".txt");
  await allowedExtensionsSection.locator(".structured-input-error").waitFor({ state: "hidden" });

  await page.getByRole("button", { name: "Save changes" }).click();
  await page.locator(".session-tools-panel .success-box").waitFor({ state: "visible" });

  assert.equal(await page.evaluate(() => window.location.hash), "#sessions?session_id=session-1");
  assert.equal(allowedToolBodies.length, 1);
  assert.equal(policyBodies.length, 1);
  assert.deepEqual(policyBodies[0].metadata, sessionRecord.metadata);
});
