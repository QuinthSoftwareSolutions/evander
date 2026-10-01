import assert from "node:assert/strict";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { createServer } from "vite";

async function fixture(t) {
  const server = await createServer({
    root: fileURLToPath(new URL("..", import.meta.url)),
    server: { host: "127.0.0.1", port: 0 }, logLevel: "error",
    plugins: [{ name: "operations-fixture", configureServer(server) {
      server.middlewares.use(async (request, response, next) => {
        if (!request.url?.startsWith("/operations-fixture")) return next();
        const html = await server.transformIndexHtml(request.url, `<html data-admin-design><body><div id="root"></div><script type="module">
          import React from 'react';
          import { createRoot } from 'react-dom/client';
          import { AdminUiContext, useAdminUi } from '/src/features/admin/AdminContext.tsx';
          import { ApprovalsScreen } from '/src/features/approvals/ApprovalsScreen.tsx';
          import { WorkersScreen } from '/src/features/workers/WorkersScreen.tsx';
          import { SessionsScreen } from '/src/features/sessions/SessionsScreen.tsx';
          import { IssuesPage } from '/src/features/issues/IssuesPage.tsx';
          import '/src/styles.css';
          const params = new URLSearchParams(location.search);
          const screens = { approvals: ApprovalsScreen, workers: WorkersScreen, sessions: SessionsScreen, issues: IssuesPage };
          function Fixture() {
            const defaults = useAdminUi();
            return React.createElement(AdminUiContext.Provider, { value: {
              ...defaults, selectedDirectoryPath: 'root', privilegedUnlocked: params.get('locked') !== 'true',
              authRequired: true, currentUserId: 'operator', currentUserLabel: 'Operator',
            } }, React.createElement(screens[params.get('view')]));
          }
          createRoot(document.getElementById('root')).render(React.createElement(Fixture));
        </script></body></html>`);
        response.setHeader("Content-Type", "text/html"); response.end(html);
      });
    } }],
  });
  await server.listen(); t.after(() => server.close());
  const browser = await chromium.launch({ headless: true }); t.after(() => browser.close());
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 } }); page.setDefaultTimeout(5000);
  const errors = []; page.on("pageerror", error => { errors.push(error.message); t.diagnostic(error.stack); });
  return { page, errors, base: `http://127.0.0.1:${server.httpServer.address().port}/operations-fixture` };
}

const timestamp = "2026-09-30T10:00:00Z";

test("approval review clears advanced filters together and preserves decision requirements", { timeout: 45000 }, async t => {
  const { page, errors, base } = await fixture(t); const writes = [];
  const approval = {
    request_id: "request-1", session_id: "session-1", vm_id: "vm-1", action_type: "email_send_approved",
    tool_name: "email_send_approved", payload_digest: "digest-1", human_summary: "Send the approved status update",
    intended_effect: "Deliver the update to the configured recipient", directory_path: "root", status: "pending",
    created_at: timestamp, expires_at: "2099-01-01T00:00:00Z", payload: {}, decision_history: [],
  };
  await page.route("**/api/**", async route => {
    const url = new URL(route.request().url()); let payload = {};
    if (url.pathname.endsWith("/decision")) {
      writes.push(route.request().postDataJSON()); approval.status = "approved"; payload = { request: approval };
    } else if (url.pathname === "/api/admin/approvals/request-1") {
      payload = { request: approval, redacted_payload: { recipient: "ops@example.test", body: "Approved update" },
        audit_context: [{ event: "requested" }], age_seconds: 120, expires_in_seconds: 600,
        payload_summary: "An email to ops@example.test" };
    } else if (url.pathname === "/api/admin/approvals") {
      payload = { items: [approval], health: { total: 1, pending: 1, approved: 0, denied: 0, expired: 0, exhausted: 0 } };
    }
    await route.fulfill({ json: payload });
  });
  await page.goto(`${base}?view=approvals#approvals`);
  assert.equal(await page.getByRole("textbox", { name: "Session", exact: true }).isVisible(), false);
  await page.getByText("More filters", { exact: true }).click();
  await page.getByRole("textbox", { name: "Session", exact: true }).fill("session-1");
  await page.getByRole("textbox", { name: "Tool", exact: true }).fill("email_send_approved");
  await page.getByRole("button", { name: "Clear filters", exact: true }).click();
  assert.equal(await page.getByRole("textbox", { name: "Session", exact: true }).inputValue(), "");
  assert.equal(await page.getByRole("textbox", { name: "Tool", exact: true }).inputValue(), "");
  await page.getByRole("button", { name: /Send the approved status update/ }).click();
  await page.getByRole("heading", { name: "Redacted payload" }).waitFor();
  assert.equal(await page.getByRole("link", { name: "session-1", exact: true }).getAttribute("href"), "#sessions?session_id=session-1");
  assert.equal(await page.getByRole("link", { name: "vm-1", exact: true }).getAttribute("href"), "#vms?vm_name=vm-1");
  await page.getByRole("button", { name: "Approve", exact: true }).click();
  await page.getByText("Reason is required.", { exact: true }).waitFor(); assert.deepEqual(writes, []);
  await page.getByRole("textbox", { name: "Reason", exact: true }).fill("Recipient and content reviewed");
  await page.getByRole("button", { name: "Approve", exact: true }).click();
  await page.getByText("Approved request-1.", { exact: true }).waitFor();
  assert.deepEqual(writes, [{ approve: true, reason: "Recipient and content reviewed" }]);
  assert.equal(await page.getByText('"requested"', { exact: false }).isVisible(), false);
  assert.deepEqual(errors, []);
});

test("worker sections preserve drafts and revision checks while privilege gates remain in place", { timeout: 45000 }, async t => {
  const { page, errors, base } = await fixture(t); const writes = [];
  const summary = { worker_id: "worker-1", name: "Scheduled wake worker", unit: "evander-scheduled-wake.service",
    active_state: "active", sub_state: "running", result: "success", unit_file_state: "enabled", description: "Dispatch scheduled wakes",
    main_pid: 1234, running: true, failed: false, detail: null };
  const detail = { summary, config: { worker_id: "worker-1", path: "/config/worker.json", exists: true,
    config: { poll_seconds: 30 }, revision: "r1" }, config_schema: { worker_id: "worker-1", fields: [
      { field_name: "poll_seconds", label: "Poll interval", kind: "int", options: [], tooltip: "Seconds between polls" },
    ] } };
  await page.route("**/api/**", async route => {
    const url = new URL(route.request().url()); let payload = {};
    if (url.pathname === "/api/workers") payload = { workers: [summary] };
    else if (url.pathname.endsWith("/log")) payload = { text: "Worker ready", lines: 1 };
    else if (url.pathname.endsWith("/config")) {
      const write = route.request().postDataJSON(); writes.push(write);
      detail.config = { ...detail.config, config: write.config, revision: "r2" }; payload = detail;
    } else payload = detail;
    await route.fulfill({ json: payload });
  });
  await page.goto(`${base}?view=workers#workers?worker_id=worker-1`);
  await page.getByRole("heading", { name: "Recent log", exact: true }).waitFor();
  assert.equal(await page.getByRole("spinbutton", { name: "Poll interval", exact: true }).isVisible(), false);
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("spinbutton", { name: "Poll interval", exact: true }).fill("45");
  await page.getByRole("button", { name: "Status and logs", exact: true }).click();
  assert.equal(await page.getByRole("button", { name: "Save settings", exact: true }).isVisible(), false);
  await page.getByRole("button", { name: "Settings · Unsaved", exact: true }).click();
  assert.equal(await page.getByRole("spinbutton", { name: "Poll interval", exact: true }).inputValue(), "45");
  await page.getByRole("button", { name: "Save settings", exact: true }).click();
  await page.getByText("Saved Scheduled wake worker global settings.", { exact: true }).waitFor();
  assert.deepEqual(writes, [{ config: { poll_seconds: 45 }, expected_revision: "r1" }]);
  await page.goto(`${base}?view=workers&locked=true#workers?worker_id=worker-1`);
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  assert.equal(await page.getByRole("spinbutton", { name: "Poll interval", exact: true }).isDisabled(), true);
  assert.equal(await page.getByRole("button", { name: "Save settings", exact: true }).isDisabled(), true);
  assert.equal(await page.getByRole("button", { name: "Stop worker", exact: true }).isDisabled(), true);
  assert.deepEqual(errors, []);
});

test("session sections preserve model drafts and enable one-step wake changes", { timeout: 45000 }, async t => {
  const { page, errors, base } = await fixture(t); const writes = [];
  const session = { session_id: "session-1", vm_id: "vm-1", model: "gpt-example", agent_harness: "codex",
    directory_path: "root", allowed_tools: [], allowed_script_names: [], communication_target_ids: [],
    email_mailbox_grants: [], created_at: timestamp, expires_at: "2099-01-01T00:00:00Z", is_expired: false, metadata: {} };
  const schedules = [1, 2].map(index => ({ request_id: `wake-${index}`, session_id: session.session_id,
    prompt: `Inspect scheduled task ${index}`, cron: "0 * * * *", schedule_kind: "cron", enabled: true,
    resume_mode: "same_context", status: "scheduled" }));
  await page.route("**/api/**", async route => {
    const url = new URL(route.request().url()); let payload = {};
    if (url.pathname === "/api/admin/sessions/session-2") {
      await route.fulfill({ status: 503, json: { error: "Session details could not be loaded." } });
      return;
    }
    if (url.pathname === "/api/admin/sessions/catalog") payload = { tool_options: [], model_options: [], resume_mode_options: [], renew_extension_presets: ["1d"] };
    else if (url.pathname === "/api/admin/sessions") payload = { items: [session, { ...session, session_id: "session-2" }], counts: { total: 2, active: 2, expired: 0 }, status_ids: { active: [session.session_id, "session-2"], expired: [] } };
    else if (url.pathname === "/api/admin/sessions/session-1") payload = { session, wake_schedules: schedules, git_grants: [], summary_json: session };
    else if (url.pathname.startsWith("/api/admin/sessions/wake-schedules/")) {
      const schedule = schedules.find(item => url.pathname.endsWith(`/${item.request_id}`));
      const body = route.request().postDataJSON();
      writes.push({ path: url.pathname, ...body }); schedule.enabled = body.enabled; payload = { item: schedule };
    } else if (url.pathname === "/api/admin/scheduled-wakes") payload = { items: [] };
    else if (url.pathname === "/api/admin/git-repositories") payload = { repositories: [] };
    else if (url.pathname === "/api/admin/inference-cost-categories") payload = { categories: [] };
    await route.fulfill({ json: payload });
  });
  await page.goto(`${base}?view=sessions#sessions?session_id=session-1`);
  await page.getByRole("heading", { name: "Session overview", exact: true }).waitFor();
  assert.equal(await page.getByLabel("Model", { exact: true }).isVisible(), false);
  await page.getByRole("button", { name: "Model and access", exact: true }).click();
  await page.getByLabel("Model", { exact: true }).fill("gpt-example-next");
  await page.getByRole("button", { name: "Wake schedules (2)", exact: true }).click();
  await page.getByRole("textbox", { name: /^Prompt/ }).fill("Unsaved schedule prompt");
  await page.getByLabel("Cron", { exact: true }).fill("15 * * * *");
  await page.getByRole("button", { name: "Disable wake schedule wake-2", exact: true }).click();
  await page.getByText("Disabled wake schedule wake-2.", { exact: true }).waitFor();
  assert.deepEqual(writes, [{ path: "/api/admin/sessions/wake-schedules/wake-2", enabled: false }]);
  assert.equal(await page.getByRole("textbox", { name: /^Prompt/ }).inputValue(), "Unsaved schedule prompt");
  assert.equal(await page.getByLabel("Cron", { exact: true }).inputValue(), "15 * * * *");
  await page.getByRole("button", { name: "Disable wake schedule wake-1", exact: true }).click();
  await page.getByText("Disabled wake schedule wake-1.", { exact: true }).waitFor();
  assert.deepEqual(writes[1], { path: "/api/admin/sessions/wake-schedules/wake-1", enabled: false });
  assert.equal(await page.getByRole("textbox", { name: /^Prompt/ }).inputValue(), "Unsaved schedule prompt");
  assert.equal(await page.getByLabel("Cron", { exact: true }).inputValue(), "15 * * * *");
  assert.equal(await page.getByRole("checkbox", { name: "Enabled", exact: true }).isChecked(), false);
  await page.getByRole("button", { name: "Model and access · Unsaved", exact: true }).click();
  assert.equal(await page.getByLabel("Model", { exact: true }).inputValue(), "gpt-example-next");
  await page.getByRole("button", { name: "Overview", exact: true }).click();
  assert.equal(await page.getByRole("link", { name: "vm-1", exact: true }).getAttribute("href"), "#vms?vm_name=vm-1");
  await page.getByRole("button", { name: "Model and access · Unsaved", exact: true }).click();
  page.once("dialog", dialog => dialog.accept());
  await page.locator(".session-list-item").filter({ hasText: "session-2" }).click();
  await page.getByRole("alert").filter({ hasText: "Session details could not be loaded." }).waitFor();
  assert.deepEqual(errors, []);
});

test("issue VM creation uses defaults in one click while optional toolchains remain explicit", { timeout: 45000 }, async t => {
  const { page, errors, base } = await fixture(t); const writes = [];
  const issue = { issue_id: "issue-1", issue_number: "123", title: "Inspect scheduled dispatch", body: "Inspect the dispatch result.",
    yaml_path: "/issues/123.yaml", directory_path: "root", source_type: "manual", source: "local", state: "New",
    tags: [], comments: [], metadata: {}, created_at: timestamp, updated_at: timestamp,
    can_edit: true, can_delete: true, can_create_vm: true };
  await page.route("**/api/**", async route => {
    const url = new URL(route.request().url()); let payload = {};
    if (url.pathname === "/api/admin/issues") payload = { items: [issue], directory_path: "root", direct_only: true };
    else if (url.pathname.endsWith("/create-vm")) {
      writes.push(route.request().postDataJSON()); payload = { success: true, vm_name: "vm-123", message: "Created vm-123." };
    } else if (url.pathname === "/api/admin/issues/issue-1") payload = issue;
    await route.fulfill({ json: payload });
  });
  await page.goto(`${base}?view=issues#issues?issue_id=issue-1`);
  const toolbar = page.locator(".feature-toolbar");
  await toolbar.getByRole("button", { name: "Create VM", exact: true }).click();
  await page.getByText("Created vm-123.", { exact: true }).waitFor();
  assert.deepEqual(writes, [{ model: null, dotnet: false, nuget: null, playwright: false, mssql: false, mssql_init_root: null }]);
  await toolbar.getByRole("button", { name: "VM options", exact: true }).click();
  await page.getByLabel("Playwright browsers", { exact: true }).check();
  await page.locator(".detail-block").getByRole("button", { name: "Create VM", exact: true }).click();
  await toolbar.getByRole("button", { name: "VM options", exact: true }).waitFor();
  assert.deepEqual(writes.at(-1), { model: null, dotnet: false, nuget: null, playwright: true, mssql: false, mssql_init_root: null });
  await page.goto(`${base}?view=issues&locked=true#issues?issue_id=issue-1`);
  await toolbar.getByRole("button", { name: "Create VM", exact: true }).waitFor();
  assert.equal(await toolbar.getByRole("button", { name: "Create VM", exact: true }).isDisabled(), true);
  assert.equal(await toolbar.getByRole("button", { name: "VM options", exact: true }).isDisabled(), true);
  assert.deepEqual(errors, []);
});
