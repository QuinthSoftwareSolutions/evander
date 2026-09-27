import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { chromium } from "playwright";
import { createServer } from "vite";

const root = fileURLToPath(new URL("..", import.meta.url));

test("configuration records start unselected and open directly from the sidebar", { timeout: 60000 }, async t => {
  const server = await createServer({ root, server: { host: "127.0.0.1", port: 0 }, logLevel: "error" });
  t.after(() => server.close());
  await server.listen();
  const url = `http://127.0.0.1:${server.httpServer.address().port}`;
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  page.setDefaultTimeout(5000);
  const errors = [];
  const writes = [];
  page.on("pageerror", error => errors.push(error.message));
  const session = {
    auth_mode: "none", auth_required: false, authenticated: true, username: "operator",
    display_name: "Operator", permissions: ["admin.owner"], effective_permissions: ["admin.owner"],
    can_manage_users: true, session_id: "fixture", available_login_methods: [], privileged_operations: { unlocked: true },
  };
  const groups = [{ group_id: "operators", display_name: "Operators", permissions: [], is_required: false }];
  const users = [{ login: "operator@example.test", display_name: "Example Operator", group_assignments: [], workflow_grants: [], effective_permissions: [] }];
  const addon = {
    candidate_key: "example-addon", addon_id: "example-addon", display_name: "Example Add-on", source: "example_addon",
    source_type: "module", loaded: true, configurable: true, required_restart_services: [], workflow_node_types: [], referenced_workflow_node_types: [],
  };
  const connector = {
    connector_id: "example", display_name: "Example Connector", description: "Example integration", driver: "http",
    auth_status: { available: true, type: "none" }, operations: {},
  };
  const directory = {
    directory_id: "documents", display_name: "Documents", description: "Shared documents", root: "/srv/documents",
    capabilities: ["list"], delete_requires_approval: true, max_import_bytes: 1024, max_import_entries: 100,
    max_watch_entries: 100, health: { status: "healthy", available: true },
  };
  await page.route("**/api/**", async route => {
    const request = route.request();
    if (request.method() !== "GET") writes.push(request.url());
    const { pathname } = new URL(request.url());
    let data = {};
    if (pathname === "/api/auth/session") data = session;
    else if (pathname === "/api/admin/state") data = {
      ...session, selected_directory_path: "root", directory_scoped_config_enabled: false,
      execution_backend: "libvirt_vm", assistant: { enabled: false, show_launcher_when_disabled: false },
    };
    else if (pathname === "/api/configuration/state") data = {
      selected_directory_path: "root", directory_scoped_config_enabled: false, tabs: [],
      selected_directory: { path: "root" },
    };
    else if (pathname === "/api/admin/users") data = { users, groups, contacts: [], permission_catalog: [] };
    else if (pathname === "/api/admin/addons") data = {
      addons: [addon], explicit_enabled_addon_ids: [addon.addon_id], loaded_addon_ids: [addon.addon_id],
      required_restart_services: [], config_paths: { host: "host", trigger_orchestrator: "worker" },
    };
    else if (pathname === "/api/admin/connectors") data = { directory_path: "root", revision: "r1", config: {}, connectors: [connector], driver_names: ["http"] };
    else if (pathname === "/api/admin/host-directories") data = { directory_path: "root", revision: "r1", config: {}, host_directories: [directory] };
    else if (pathname.endsWith("/quarantine")) data = { entries: [] };
    else if (pathname === "/api/admin/mcp-tools") data = { directory_path: "root", revision: "r1", mcp_tool_policies: {}, items: [{
      value: "list_documents", label: "List documents", group: "Core", effective_settings: { ttl_seconds: 3600, max_uses: 1, payload_mode: "exact" },
    }] };
    else if (pathname === "/api/admin/user-notifications") data = { unread_count: 0, notifications: [] };
    else if (pathname === "/api/admin/deployment-status") data = { active: false };
    await route.fulfill({ json: data });
  });
  for (const [route, label, name, key, id] of [
    ["users/users", "a user", "Example Operator", "login", "operator@example.test"],
    ["users/groups", "a group", "Operators", "group_id", "operators"],
    ["connectors", "a connector", "Example Connector", "connector_id", "example"],
    ["host-directories", "a host directory", "Documents", "directory_id", "documents"],
    ["addons", "an add-on", "Example Add-on", "addon", "example-addon"],
    ["mcp-tools", "an MCP tool", "List documents", "tool", "list_documents"],
  ]) {
    await t.test(route, async () => {
      await page.goto(`${url}/#${route}`);
      await page.getByRole("heading", { name: `Select ${label} to view details`, exact: true }).waitFor();
      const item = page.locator(".feature-sidebar .feature-list-item").filter({ hasText: name });
      await item.waitFor();
      assert.equal(await page.locator(".feature-list-item.selected").count(), 0);
      assert.equal(await page.locator(".record-inspector, .collection-page").count(), 0);
      await item.click();
      await page.getByRole("heading", { name, exact: true }).first().waitFor();
      await page.waitForFunction(({ key, id }) => new URLSearchParams(location.hash.split("?")[1]).get(key) === id, { key, id });
      await page.reload();
      await page.getByRole("heading", { name, exact: true }).first().waitFor();
      await page.evaluate(route => { location.hash = route; }, route);
      await page.getByRole("heading", { name: `Select ${label} to view details`, exact: true }).waitFor();
    });
  }
  await page.goto(`${url}/#users/users`);
  await page.getByRole("button", { name: "New user", exact: true }).click();
  await page.getByRole("heading", { name: "Add user", exact: true }).waitFor();
  await page.getByLabel("Login", { exact: true }).fill("new@example.test");
  await page.getByRole("tab", { name: "Groups", exact: true }).click();
  await page.getByRole("button", { name: "New group", exact: true }).click();
  await page.getByRole("heading", { name: "New group", exact: true }).first().waitFor();
  assert.deepEqual(writes, []);
  assert.deepEqual(errors, []);
});
