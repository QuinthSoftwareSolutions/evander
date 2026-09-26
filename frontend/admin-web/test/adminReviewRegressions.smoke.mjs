import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { chromium } from "playwright";
import { createServer } from "vite";

const root = fileURLToPath(new URL("..", import.meta.url));
const repoRoot = fileURLToPath(new URL("../../..", import.meta.url));

test("record routing, inherited Miro recovery, and select listboxes", { timeout: 60000 }, async (t) => {
  const server = await createServer({
    root,
    server: { host: "127.0.0.1", port: 0, fs: { allow: [repoRoot] } },
    logLevel: "error",
  });
  t.after(() => server.close());
  await server.listen();
  const url = `http://127.0.0.1:${server.httpServer.address().port}`;
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());

  await t.test("skills follow same-view hashes and keep sidebar selections shareable", async () => {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    page.setDefaultTimeout(5000);
    const errors = [];
    let failSkillRefresh = false;
    page.on("pageerror", (error) => errors.push(error.message));
    const items = ["alpha", "beta"].map((name) => ({ name, size_bytes: 42, updated_at: "2026-09-23T10:00:00Z" }));
    const session = {
      auth_mode: "none", auth_required: false, authenticated: true, username: "admin",
      display_name: "Admin", permissions: ["admin.owner"], effective_permissions: ["admin.owner"],
      can_manage_users: true, session_id: "fixture", available_login_methods: [],
      privileged_operations: { unlocked: true },
    };
    await page.route("**/api/**", async (route) => {
      const { pathname } = new URL(route.request().url());
      let data = {};
      if (pathname === "/api/auth/session") data = session;
      else if (pathname === "/api/admin/state") data = {
        ...session, selected_directory_path: "root", directory_scoped_config_enabled: false,
        execution_backend: "libvirt_vm", assistant: { enabled: false, show_launcher_when_disabled: false },
      };
      else if (pathname === "/api/admin/skills") {
        if (failSkillRefresh) {
          await route.fulfill({ status: 503, json: { error: "Skills service temporarily unavailable" } });
          return;
        }
        data = { items };
      }
      else if (pathname.startsWith("/api/admin/skills/")) {
        const name = decodeURIComponent(pathname.split("/").at(-1));
        data = { skill: { ...items.find((item) => item.name === name), content: `# ${name}` } };
      }
      else if (pathname === "/api/admin/user-notifications") data = { unread_count: 0, notifications: [] };
      else if (pathname === "/api/admin/deployment-status") data = { active: false };
      await route.fulfill({ json: data });
    });
    try {
      await page.goto(`${url}/#skills`);
      const fullRecord = page.locator(".workspace-full-record");
      await page.getByRole("heading", { name: "Select a skill to view details", exact: true }).waitFor();
      assert.equal(await page.locator(".record-inspector, .collection-page").count(), 0);
      await fullRecord.locator(".skill-list-item").filter({ hasText: "alpha" }).click();
      await page.waitForURL("**/#skills?skill=alpha");
      await fullRecord.getByRole("heading", { name: "alpha", exact: true }).waitFor();
      await page.evaluate(() => { window.workspaceBeforeNavigation = document.querySelector(".record-workspace"); });
      await page.evaluate(() => { location.hash = "#skills?skill=beta"; });
      await fullRecord.getByRole("heading", { name: "beta", exact: true }).waitFor();
      assert.equal(await page.evaluate(() => window.workspaceBeforeNavigation === document.querySelector(".record-workspace")), true);
      await page.goBack();
      await fullRecord.getByRole("heading", { name: "alpha", exact: true }).waitFor();
      await page.goForward();
      await fullRecord.getByRole("heading", { name: "beta", exact: true }).waitFor();

      await page.evaluate(() => { location.hash = "#skills?skill=alpha&inspect=1"; });
      await fullRecord.getByRole("heading", { name: "alpha", exact: true }).waitFor();
      await page.waitForURL("**/#skills?skill=alpha");
      assert.equal(await page.locator(".record-inspector").count(), 0);
      await fullRecord.locator(".skill-list-item").filter({ hasText: "beta" }).click();
      await page.waitForURL("**/#skills?skill=beta");
      await fullRecord.getByRole("heading", { name: "beta", exact: true }).waitFor();
      await page.reload();
      await fullRecord.getByRole("heading", { name: "beta", exact: true }).waitFor();
      await fullRecord.locator(".skill-list-item").filter({ hasText: "alpha" }).click();
      await page.waitForURL("**/#skills?skill=alpha");
      await page.evaluate(() => { location.hash = "#skills?skill=beta"; });
      await fullRecord.getByRole("heading", { name: "beta", exact: true }).waitFor();
      await page.evaluate(() => { location.hash = "#skills"; });
      await page.getByRole("heading", { name: "Select a skill to view details", exact: true }).waitFor();
      failSkillRefresh = true;
      await page.getByRole("button", { name: "Refresh", exact: true }).click();
      await page.getByText("Skills service temporarily unavailable", { exact: true }).waitFor();
      assert.equal(await page.getByRole("button", { name: "Refresh", exact: true }).isEnabled(), true);
      assert.equal(await page.locator(".skill-list-item").count(), 2);
      failSkillRefresh = false;
      await page.getByRole("button", { name: "Refresh", exact: true }).click();
      await page.getByText("Skills service temporarily unavailable", { exact: true }).waitFor({ state: "hidden" });
      assert.deepEqual(errors, []);
    } finally {
      await page.close();
    }
  });

  await t.test("reports show no activity bar for inactive graphs and support keyboard selection", async () => {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    page.setDefaultTimeout(5000);
    const session = {
      auth_mode: "none", auth_required: false, authenticated: true, username: "admin",
      display_name: "Admin", permissions: ["admin.owner"], effective_permissions: ["admin.owner"],
      can_manage_users: true, session_id: "fixture", available_login_methods: [],
      privileged_operations: { unlocked: true },
    };
    const graphs = [{ graph_id: "active", name: "Active graph", activity_count: 100 }, { graph_id: "inactive", name: "Inactive graph", activity_count: 0 }]
      .map((graph) => ({ ...graph, directory_path: "root", run_count: graph.activity_count, node_count: 0,
        trigger_event_count: graph.activity_count, gate_execution_count: 0, effect_execution_count: 0,
        nodes: [], user_ids: [], status_counts: {}, latest_activity_at: null }));
    await page.route("**/api/**", async (route) => {
      const { pathname } = new URL(route.request().url());
      let data = {};
      if (pathname === "/api/auth/session") data = session;
      else if (pathname === "/api/admin/state") data = {
        ...session, selected_directory_path: "root", directory_scoped_config_enabled: false,
        execution_backend: "libvirt_vm", assistant: { enabled: false, show_launcher_when_disabled: false },
      };
      else if (pathname === "/api/admin/reports/workflow-graph-activity") data = { graphs };
      else if (pathname === "/api/admin/reports/conformance") data = { reports: [] };
      else if (pathname === "/api/admin/user-notifications") data = { unread_count: 0, notifications: [] };
      else if (pathname === "/api/admin/deployment-status") data = { active: false };
      await route.fulfill({ json: data });
    });
    try {
      await page.goto(`${url}/#reports`);
      const table = page.locator(".report-activity-table");
      const inactive = table.getByRole("row").filter({ hasText: "Inactive graph" });
      await inactive.waitFor();
      assert.equal(await inactive.locator(".activity-bar span").evaluate((el) => el.getBoundingClientRect().width), 0);
      const active = table.getByRole("row").filter({ has: page.getByRole("button", { name: "Active graph", exact: true }) });
      assert.ok(await active.locator(".activity-bar span").evaluate((el) => el.getBoundingClientRect().width) > 0);
      await inactive.getByRole("button", { name: "Inactive graph", exact: true }).focus();
      await page.keyboard.press("Enter");
      await page.locator(".report-grid .detail-panel").getByRole("heading", { name: "Inactive graph", exact: true }).waitFor();
    } finally {
      await page.close();
    }
  });

  await t.test("inherited Miro connections allow unlocked operation recovery", async () => {
    const page = await browser.newPage();
    page.setDefaultTimeout(5000);
    const writes = [];
    const connection = {
      config: { id: "shared", name: "Shared connection", directory_path: "root", boards: [{ board_id: "board-1", name: "Board", writable: true }] },
      connected: true, calls_today: 0, revision: 1,
    };
    const operation = { operation_id: "child-operation", directory_path: "root/child", operation: "update_canvas", status: "uncertain", actor: "operator" };
    await page.route("**/miro-fixture", (route) => route.fulfill({ contentType: "text/html", body:
      `<iframe title="Miro" src="/@fs${repoRoot}/src/evander_miro_addon/ui_static/index.html?directory_path=root%2Fchild"></iframe>`,
    }));
    await page.route("**/api/addons/miro-addon/**", async (route) => {
      const request = route.request();
      const endpoint = new URL(request.url());
      assert.equal(endpoint.searchParams.get("directory_path"), "root/child");
      let data;
      if (endpoint.pathname.endsWith("/connections")) data = { connections: [connection] };
      else if (endpoint.pathname.endsWith("/activity")) data = { operations: [operation], subscriptions: [] };
      else {
        writes.push({ path: endpoint.pathname, body: request.postDataJSON() });
        data = { operation };
      }
      await route.fulfill({ json: data });
    });
    const unlock = (unlocked) => page.evaluate((unlocked) => {
      document.querySelector("iframe").contentWindow.postMessage({
        type: "admin-addon-context", privileged_operations: { unlocked },
      }, location.origin);
    }, unlocked);
    try {
      await page.goto(`${url}/miro-fixture`);
      const frame = page.frameLocator("iframe");
      await frame.locator("#connections button").click();
      const readback = frame.getByRole("button", { name: "Read back", exact: true });
      await readback.waitFor();
      assert.equal(await readback.isDisabled(), true);
      await unlock(true);
      await readback.click();
      for (const [label, outcome] of [["Mark applied", "applied"], ["Mark not applied", "not_applied"]]) {
        page.once("dialog", (dialog) => dialog.accept("Checked the board readback."));
        await frame.getByRole("button", { name: label, exact: true }).click();
        await frame.locator("#activity .operation").waitFor();
        assert.equal(writes.at(-1).body.outcome, outcome);
      }
      assert.equal(writes[0].path, "/api/addons/miro-addon/connections/shared/operations/child-operation/reconcile");
      for (const selector of ["#name", "#add-board", '#settings button[type="submit"]', "#connect", "#disconnect", "#health", "#boards button"]) {
        assert.equal(await frame.locator(selector).isDisabled(), true, `${selector} stays restricted for inherited connections`);
      }
      assert.equal(await frame.locator("#new").isDisabled(), false);
      await unlock(false);
      await page.waitForFunction(() => document.querySelector("iframe").contentDocument.querySelector("#activity [data-privileged-action]").disabled);
      for (const label of ["Read back", "Mark applied", "Mark not applied"]) {
        assert.equal(await frame.getByRole("button", { name: label, exact: true }).isDisabled(), true);
      }
    } finally {
      await page.close();
    }
  });

  await t.test("host and Team listboxes retain four and five visible rows in the compact layout", async () => {
    const page = await browser.newPage();
    page.setDefaultTimeout(5000);
    try {
      for (const styles of [
        ["/src/design/tokens.css", "/src/design/controls.css"],
        [`/@fs${repoRoot}/src/evander_team_addon/ui_static/admin-design.css`],
      ]) {
        await page.setContent(`<html data-admin-design data-addon="team"><head>
          ${styles.map((href) => `<link rel="stylesheet" href="${url}${href}">`).join("")}
          </head><body>
          <select id="dropdown"><option>Single row</option></select>
          <select id="members" multiple size="5"></select>
          <select id="roles" multiple size="4"></select>
          <select id="single-list" size="4"></select>
          <script>document.querySelectorAll('select[size]').forEach(select => {
            for (let row = 0; row < select.size; row++) select.add(new Option('Option ' + row, String(row), row % 2 === 0, row % 2 === 0));
          });</script></body></html>`);
        const sizes = await page.locator("select").evaluateAll((selects) => selects.map((select) => ({
          id: select.id, height: select.getBoundingClientRect().height,
          controlHeight: parseFloat(getComputedStyle(select).getPropertyValue("--ui-control-height")),
          rows: select.size, optionHeight: select.options[0].getBoundingClientRect().height,
        })));
        assert.equal(sizes[0].height, sizes[0].controlHeight);
        for (const size of sizes.slice(1)) {
          assert.ok(size.height >= size.rows * size.optionHeight, `${size.id} must show ${size.rows} rows`);
          assert.ok(size.height > sizes[0].height * 2, `${size.id} must remain a listbox`);
        }
      }
    } finally {
      await page.close();
    }
  });
});
