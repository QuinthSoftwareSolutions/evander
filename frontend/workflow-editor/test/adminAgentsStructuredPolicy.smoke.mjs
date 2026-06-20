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

test("agents structured labels round-trip and block malformed rows", async (t) => {
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

  const patchBodies = [];
  const agentRecord = {
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
      session_policy: {
        session_id: "session-1",
        model: "gpt-5.4",
        expires_at: "2026-05-18T12:00:00Z",
        allowed_tools: ["prompt_agent"],
        approval_required_tools: [],
        approval_notification_target_ids: [],
        approval_notification_target_options: [],
        web_search_enabled: false,
        allowed_script_names: [],
        discord_channels: [],
        discord_channel_allowlist: [],
        teams_channel_allowlist: [],
        metadata: {},
        email_mailboxes: [],
        email_folders: [],
        labels: {
          team: "infra",
        },
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

    if (pathname === "/api/admin/skills") {
      await route.fulfill(jsonResponse({ items: [] }));
      return;
    }

    if (pathname === "/api/admin/agents/create-options") {
      await route.fulfill(
        jsonResponse({
          directory_path: "root",
          backends: ["libvirt_vm", "container_worker_vm"],
          agent_providers: [],
          model_provider_modes: [],
          communication_targets: [],
          libvirt: {
            default_template: null,
            templates: [],
          },
          container_worker: {
            default_image: null,
            images: [],
          },
        }),
      );
      return;
    }

    if (pathname === "/api/admin/agents") {
      await route.fulfill(
        jsonResponse({
          items: [agentRecord],
          directory_path: "root/team",
          direct_only: true,
        }),
      );
      return;
    }

    if (pathname === "/api/admin/agents/agent-1") {
      if (route.request().method() === "PATCH") {
        const body = JSON.parse(route.request().postData() ?? "{}");
        patchBodies.push(body);
        assert.deepEqual(body.labels, { team: "ops" });
        assert.deepEqual(body.allowed_tools, ["prompt_agent"]);
        assert.deepEqual(body.allowed_script_names, ["collect-logs"]);
        assert.deepEqual(body.discord_channels, []);
        assert.deepEqual(body.teams_channel_allowlist, []);
        assert.deepEqual(body.email_mailboxes, []);
        assert.deepEqual(body.email_folders, []);
        await route.fulfill(jsonResponse({ message: "Updated agent policy.", target_name: "Dispatch agent" }));
        return;
      }
      await route.continue();
      return;
    }

    await route.continue();
  });

  await page.goto(`http://127.0.0.1:${address.port}/#agents?agent_id=agent-1&session_id=session-1`, {
    waitUntil: "domcontentloaded",
  });

  await ensureSectionsExpanded(page);
  await page.getByRole("heading", { name: "Agents" }).waitFor({ state: "visible" });
  await page.locator(".agent-list-item.selected").waitFor({ state: "visible" });

  const editButton = page.getByRole("button", { name: "Edit policy" });
  await editButton.click();
  const editDialog = page.locator(".modal-card[aria-label='Edit agent policy']");
  await editDialog.waitFor({ state: "visible" });

  const labelsField = editDialog.locator(".field-stack").filter({ hasText: "Labels" });
  assert.equal(await labelsField.locator("textarea").count(), 0);
  for (const label of [
    "Allowed tools",
    "Allowed scripts",
    "Discord channels or targets",
    "Teams targets",
    "Email mailboxes",
    "Email folders",
  ]) {
    assert.equal(
      await editDialog.locator(".field-stack").filter({ hasText: label }).locator("textarea").count(),
      0,
      `${label} should use structured controls instead of a textarea`,
    );
  }

  const firstRow = labelsField.locator(".structured-input-row").first();
  await firstRow.locator("input").nth(1).fill("ops");
  const scriptsField = editDialog.locator(".field-stack").filter({ hasText: "Allowed scripts" });
  await scriptsField.getByRole("button", { name: "Add script" }).click();
  await scriptsField.locator(".structured-input-row").first().locator("input").fill("collect-logs");
  await editDialog.getByRole("button", { name: "Save" }).click();
  await page.locator(".modal-card[aria-label='Edit agent policy']").waitFor({ state: "hidden" });
  assert.equal(patchBodies.length, 1);

  await editButton.click();
  await editDialog.waitFor({ state: "visible" });
  await labelsField.getByRole("button", { name: "Add label" }).click();
  const duplicateRow = labelsField.locator(".structured-input-row").nth(1);
  await duplicateRow.locator("input").nth(0).fill("team");
  await duplicateRow.locator("input").nth(1).fill("ops");
  await editDialog.getByRole("button", { name: "Save" }).click();
  await page.locator(".callout.error-callout").filter({ hasText: "Labels contains duplicate key 'team'." }).first().waitFor({ state: "visible" });
  assert.equal(patchBodies.length, 1);
});
