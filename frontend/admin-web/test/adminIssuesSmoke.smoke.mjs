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

test("admin shell issues page exposes VM bootstrap labels and tooltips", async (t) => {
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
  const context = await browser.newContext({ viewport: { width: 960, height: 900 } });
  const page = await context.newPage();

  const requestedEndpoints = [];
  const issueRecord = {
    issue_id: "issue-1",
    issue_number: "ISS-1",
    title: "Bootstrap the VM",
    body: "Add VM bootstrap options.",
    description: "Add VM bootstrap options.",
    yaml_path: "",
    directory_path: "root/team",
    managed_repo_id: "mr_0123456789abcdef0123",
    source_type: "local",
    state: "New",
    tags: [],
    comments: [],
    source: "local",
    metadata: {},
    created_at: "2026-05-18T10:00:00Z",
    updated_at: "2026-05-18T10:05:00Z",
    remote_reference: null,
    can_edit: false,
    can_delete: false,
    can_create_vm: true,
  };
  const issueListResponse = {
    items: [issueRecord],
    directory_path: "root",
    direct_only: true,
  };
  const issueDetailResponse = {
    issue_id: issueRecord.issue_id,
    issue_number: issueRecord.issue_number,
    title: issueRecord.title,
    body: issueRecord.body,
    description: issueRecord.description,
    state: issueRecord.state,
    directory_path: issueRecord.directory_path,
    managed_repo_id: issueRecord.managed_repo_id,
    yaml_path: issueRecord.yaml_path,
    url: null,
    source: issueRecord.source,
    source_type: issueRecord.source_type,
    provider: null,
    remote_issue_id: null,
    reason: null,
    rejection_reason: null,
    tags: [],
    comments: [],
    metadata: {},
    remote_reference: null,
    created_at: issueRecord.created_at,
    updated_at: issueRecord.updated_at,
    can_edit: false,
    can_delete: false,
    can_create_vm: true,
    raw: {},
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
      unlocked: true,
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

    if (pathname === "/api/admin/issues") {
      assert.equal(url.searchParams.get("state_filter"), "active");
      assert.equal(url.searchParams.get("directory_path"), "root");
      assert.equal(url.searchParams.get("direct_only"), "true");
      await route.fulfill(jsonResponse(issueListResponse));
      return;
    }

    if (pathname === "/api/admin/issues/issue-1") {
      await route.fulfill(jsonResponse(issueDetailResponse));
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

  await page.goto(`http://127.0.0.1:${address.port}/#issues`, {
    waitUntil: "domcontentloaded",
  });

  await ensureSectionsExpanded(page);
  await assert.doesNotReject(() =>
    page.getByRole("heading", { name: "Issues", level: 1 }).waitFor({ state: "visible" }),
  );
  await page.locator(".issues-page .feature-list-item").first().click();
  await assert.doesNotReject(() => page.getByRole("button", { name: "Create VM" }).waitFor({ state: "visible" }));
  await page.getByRole("button", { name: "Create VM" }).hover();
  await assert.doesNotReject(() =>
    page.getByRole("tooltip").getByText("Create a VM for the selected issue using the configured bootstrap options.", { exact: true }).waitFor({ state: "visible" }),
  );

  await page.getByRole("button", { name: "Create VM" }).click();
  await assert.doesNotReject(() => page.getByText("Bootstrap .NET SDK", { exact: true }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByText("NuGet packages", { exact: true }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByText("Playwright browsers", { exact: true }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByText("MSSQL test database", { exact: true }).waitFor({ state: "visible" }));

  await page.getByText("Bootstrap .NET SDK", { exact: true }).hover();
  await assert.doesNotReject(() =>
    page.getByRole("tooltip").getByText(".NET SDK bootstrap installs the .NET tooling used by this VM.", { exact: true }).waitFor({ state: "visible" }),
  );
  await page.getByText("NuGet packages", { exact: true }).hover();
  await assert.doesNotReject(() =>
    page.getByRole("tooltip").getByText("NuGet packages bootstrap enables the optional restore target below.", { exact: true }).waitFor({ state: "visible" }),
  );
  await page.getByText("NuGet restore target", { exact: true }).hover();
  await assert.doesNotReject(() =>
    page.getByRole("tooltip").getByText("Only enabled when NuGet packages is checked. Leave blank to use the default restore target.", { exact: true }).waitFor({ state: "visible" }),
  );
  await page.getByText("Playwright browsers", { exact: true }).hover();
  await assert.doesNotReject(() =>
    page.getByRole("tooltip").getByText("Playwright browsers bootstrap installs browser and test dependencies.", { exact: true }).waitFor({ state: "visible" }),
  );
  await page.getByText("MSSQL test database", { exact: true }).hover();
  await assert.doesNotReject(() =>
    page.getByRole("tooltip").getByText("MSSQL test database bootstrap provisions SQL Server and database setup using the optional init root.", { exact: true }).waitFor({ state: "visible" }),
  );
  await page.getByText("MSSQL init root", { exact: true }).hover();
  await assert.doesNotReject(() =>
    page.getByRole("tooltip").getByText("Only enabled when MSSQL test database is checked. Leave blank to use the default init root.", { exact: true }).waitFor({ state: "visible" }),
  );

  const nugetTarget = page.getByLabel("NuGet restore target");
  const nugetPackages = page.getByLabel("NuGet packages");
  const mssqlInitRoot = page.getByLabel("MSSQL init root");
  assert.equal(await nugetTarget.isDisabled(), true);
  assert.equal(await nugetPackages.isChecked(), false);
  await nugetPackages.check();
  assert.equal(await nugetTarget.isDisabled(), false);
  assert.equal(await mssqlInitRoot.isDisabled(), true);
  await page.getByLabel("MSSQL test database").check();
  assert.equal(await mssqlInitRoot.isDisabled(), false);

  assert.equal(await page.getByLabel("Bootstrap .NET SDK").isChecked(), false);
  assert.equal(await page.getByLabel("Playwright browsers").isChecked(), false);
  assert.equal(await page.getByLabel("MSSQL test database").isChecked(), true);

  assert.ok(requestedEndpoints.some((endpoint) => endpoint.startsWith("/api/admin/issues?")), "expected issues index request");
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/admin/issues/issue-1"), "expected issue detail request");
  assert.deepEqual(pageErrors, []);
  assert.deepEqual(consoleErrors, []);
});
