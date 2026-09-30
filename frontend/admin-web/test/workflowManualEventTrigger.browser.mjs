import assert from "node:assert/strict";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { createServer } from "vite";
import { browserHookTimeout, browserTestTimeout, listeningAddress, suiteBrowser, withTimeout } from "./support/browserHarness.mjs";

const browser = suiteBrowser();
const json = (payload, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(payload) });

async function editor(t, { control = true } = {}) {
  const server = await createServer({
    root: path.resolve(fileURLToPath(new URL("..", import.meta.url))),
    server: { host: "127.0.0.1", port: 0 }, logLevel: "error",
  });
  t.after(() => server.close(), { timeout: browserHookTimeout });
  await withTimeout(server.listen(), 15_000, "Vite did not start within 15 seconds");
  const context = await browser.newContext(t, { viewport: { width: 1440, height: 1100 } });
  const page = await context.newPage();
  const requests = [];
  const runLookups = [];
  const permissions = ["workflows.read", "workflows.write", "workflow_runs.read", ...(control ? ["workflow_runs.control"] : [])];
  const config = {
    triggers: [
      { id: "alpha", display_name: "Answer received", type: "trigger:event", config: { event_type: "custom.answer", correlation_key: "ticket:demo" } },
      { id: "beta", display_name: "Disabled event", type: "trigger:event", config: { event_type: "custom.answer", enabled: false } },
      { id: "schedule", display_name: "Scheduled event", type: "trigger:cron", config: { expression: "0 * * * *" } },
    ], gates: [], effects: [], queries: [], edges: [],
  };
  const document = {
    worker_id: "trigger-orchestrator", config_path: "workflow.json", layout_path: "layout.json",
    config_exists: true, layout_exists: true, revision: "revision-1", config,
    layout: { nodes: { alpha: { x: 80, y: 80 }, beta: { x: 80, y: 250 }, schedule: { x: 80, y: 420 } } },
    graphs: [{ id: "graph-1", name: "Answer workflow", directory_path: "root", user_ids: [], node_ids: ["alpha", "beta", "schedule"] }],
    output_schemas: {}, validation_diagnostics: [],
  };
  const field = (key, label, required = false) => ({ key, label, kind: "string", required, bindable: false, read_only: false, multiline: false, options: [], item_fields: [] });
  const catalog = { worker_id: "trigger-orchestrator", internal_events: [], nodes: [
    { kind: "trigger", type: "trigger:event", label: "Internal event", fields: [field("event_type", "Event type", true), field("correlation_key", "Correlation key")], input_ports: [], output_ports: [{ key: "completed", label: "Completed" }] },
    { kind: "trigger", type: "trigger:cron", label: "Schedule", fields: [field("expression", "Expression", true)], input_ports: [], output_ports: [{ key: "completed", label: "Completed" }] },
  ] };
  let reply = async (route) => route.fulfill(json({ event_id: "alpha:queued-event", trace_id: "trace-1" }, 202));
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const name = url.pathname;
    if (name === "/api/auth/session") return route.fulfill(json({
      auth_mode: "none", auth_required: false, authenticated: true, username: "operator", display_name: "Operator",
      permissions, effective_permissions: permissions, can_manage_users: false, session_id: "session-1",
      privileged_operations: { unlocked: true }, available_login_methods: [],
    }));
    if (name === "/api/admin/state") return route.fulfill(json({
      selected_directory_path: "root", directory_scoped_config_enabled: false, execution_backend: "libvirt_vm",
      permissions, effective_permissions: permissions, privileged_operations: { unlocked: true },
      assistant: { enabled: false, show_launcher_when_disabled: false },
    }));
    if (name === "/api/configuration/state") return route.fulfill(json({
      directory_scoped_config_enabled: false, selected_directory_path: "root", execution_backend: "libvirt_vm",
      selected_directory: { path: "root", parent_path: null, can_create: false, can_delete: false },
      tree: { ok: true, data: { directory: { id: "root", name: "root", path: "root", parent_id: null, metadata: {} }, children: [], config_domains: [] }, errors: [], validation_diagnostics: [] },
    }));
    if (name === "/api/admin/deployment-status") return route.fulfill(json({ active: false }));
    if (name.startsWith("/api/admin/branding/assets/")) return route.fulfill({ status: 204, body: "" });
    if (name === "/api/admin/user-notifications") return route.fulfill(json({ notifications: [], unread_count: 0 }));
    if (name === "/api/admin/user-preferences/workflow-tutorial-progress") return route.fulfill(json({ completed_tutorial_ids: [], skipped_tutorial_ids: [] }));
    if (name === "/api/workflow-editor/document") return route.fulfill(json(document));
    if (name === "/api/workflow-editor/catalog") return route.fulfill(json(catalog));
    if (name === "/api/workflow-editor/validate") {
      const body = request.postDataJSON();
      return route.fulfill(json({ valid: true, config: body.config, layout: body.layout, graphs: body.graphs, diagnostics: [], output_schemas: {} }));
    }
    if (name === "/api/workflow-editor/runs") return route.fulfill(json({ worker_id: "trigger-orchestrator", runs: [], node_activity: {}, recent_node_activity: [] }));
    if (name === "/api/workflow-editor/runs/stream") return route.fulfill({ contentType: "text/event-stream", body: ": keepalive\n\n" });
    if (name === "/api/workflow-editor/node-executions/search") return route.fulfill(json({ rows: [], total_count: 0, fields: [] }));
    if (name === "/api/workflow-editor/run-detail") {
      runLookups.push(url.searchParams.get("event_id"));
      return route.fulfill(json({ error: "Run is still queued" }, 404));
    }
    if (name.endsWith("/trigger")) {
      requests.push({ path: name, body: request.postDataJSON() });
      return reply(route);
    }
    return route.fulfill(json({}));
  });
  await page.goto(`http://127.0.0.1:${listeningAddress(server).port}/#workflow`, { waitUntil: "domcontentloaded" });
  await page.locator('.react-flow__node[data-id="alpha"]').click();
  await page.getByRole("button", { name: "Trigger manually", exact: true }).waitFor();
  return { page, requests, runLookups, setReply: (next) => { reply = next; } };
}

test("event-node inspector validates payloads, retries safely, and opens the queued run", { timeout: browserTestTimeout }, async (t) => {
  const { page, requests, runLookups, setReply } = await editor(t);
  await page.getByRole("button", { name: "Trigger manually", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Trigger event manually" });
  assert.equal(await dialog.getByLabel("Event type", { exact: true }).inputValue(), "custom.answer");
  assert.equal(await dialog.getByLabel("Correlation key (optional)").inputValue(), "ticket:demo");
  assert.equal(await dialog.getByLabel("Correlation key (optional)").getAttribute("readonly"), "");
  const payload = dialog.getByLabel("Event payload JSON");
  const submit = dialog.getByRole("button", { name: "Trigger node", exact: true });
  for (const invalid of ["{", "[]", "null", "42"]) {
    await payload.fill(invalid);
    assert.equal(await submit.isDisabled(), true);
    assert.equal(requests.length, 0);
  }
  await payload.fill('{"answer":"yes"}');
  setReply(async (route) => route.abort("failed"));
  await submit.click();
  await dialog.getByRole("alert").waitFor();
  assert.equal(await payload.inputValue(), '{"answer":"yes"}');
  assert.equal(requests.length, 1);
  assert.equal(await page.evaluate(() => Object.keys(sessionStorage).some((key) => key.startsWith("evander:manual-event-trigger:"))), true);

  let release;
  setReply(async (route) => {
    await new Promise((resolve) => { release = resolve; });
    await route.fulfill(json({ event_id: "alpha:queued-event", trace_id: "trace-1" }, 202));
  });
  await submit.click();
  await dialog.getByRole("button", { name: "Queuing…" }).waitFor();
  assert.equal(await payload.isDisabled(), true);
  assert.equal(await dialog.getByRole("button", { name: "Queuing…" }).isDisabled(), true);
  assert.equal(requests.length, 2);
  assert.equal(requests[0].body.request_id, requests[1].body.request_id);
  assert.deepEqual(requests[1].body.payload, { answer: "yes" });
  assert.equal(requests[1].path, "/api/workflow-editor/nodes/alpha/trigger");
  assert.equal(requests[1].body.expected_revision, "revision-1");
  release();
  await dialog.getByText("Run queued", { exact: true }).waitFor();
  assert.equal(await page.evaluate(() => Object.keys(sessionStorage).some((key) => key.startsWith("evander:manual-event-trigger:"))), false);
  setReply(async (route) => route.fulfill(json({ event_id: "alpha:second-event", trace_id: "trace-2" }, 202)));
  await dialog.getByRole("button", { name: "Trigger again" }).click();
  await dialog.getByText("Run queued", { exact: true }).waitFor();
  assert.notEqual(requests[2].body.request_id, requests[1].body.request_id);
  await Promise.all([
    page.waitForResponse((response) => new URL(response.url()).pathname === "/api/workflow-editor/run-detail"),
    dialog.getByRole("button", { name: "Inspect run" }).click(),
  ]);
  assert.equal(runLookups.at(-1), "alpha:second-event");
});

test("manual action respects disabled nodes, node type, and unsaved edits", { timeout: browserTestTimeout }, async (t) => {
  const { page } = await editor(t);
  await page.locator('.react-flow__node[data-id="beta"]').click();
  const trigger = page.getByRole("button", { name: "Trigger manually", exact: true });
  assert.equal(await trigger.isDisabled(), true);
  await page.getByText("Enable and save this trigger before running it.", { exact: true }).waitFor();
  await page.locator('.react-flow__node[data-id="schedule"]').click();
  assert.equal(await trigger.count(), 0);
  await page.locator('.react-flow__node[data-id="alpha"]').click();
  await page.getByLabel("Display name", { exact: true }).fill("Edited event");
  await page.getByText("Save or discard workflow changes before triggering this node.", { exact: true }).waitFor();
  assert.equal(await trigger.isDisabled(), true);
  await page.getByRole("button", { name: "Reset changes", exact: true }).click();
  await page.waitForFunction(() => [...document.querySelectorAll("button")].some((button) => button.textContent === "Trigger manually" && !button.disabled));
});

test("workflow readers without run-control permission cannot trigger nodes", { timeout: browserTestTimeout }, async (t) => {
  const { page, requests } = await editor(t, { control: false });
  assert.equal(await page.getByRole("button", { name: "Trigger manually", exact: true }).isDisabled(), true);
  await page.getByText("Workflow run-control permission is required.", { exact: true }).waitFor();
  assert.equal(requests.length, 0);
});
