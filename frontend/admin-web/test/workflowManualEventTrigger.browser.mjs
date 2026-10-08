import assert from "node:assert/strict";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { createServer } from "vite";
import { browserHookTimeout, browserTestTimeout, listeningAddress, suiteBrowser, withTimeout } from "./support/browserHarness.mjs";

const browser = suiteBrowser();
const json = (payload, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(payload) });

async function editor(t, { control = true, schema = null } = {}) {
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
    if (name.endsWith("/event-schema")) return route.fulfill(json({ event_type: "custom.answer", payload_schema: schema, schema_revision: "schema-1" }));
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
  return { page, requests, runLookups, setReply: (next) => { reply = next; }, saveRevision: (revision) => {
    document.revision = revision;
    document.layout.nodes.alpha.x += 20;
  } };
}

test("event-node inspector validates payloads, retries safely, and opens the queued run", { timeout: browserTestTimeout }, async (t) => {
  const { page, requests, runLookups, setReply, saveRevision } = await editor(t);
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
  await payload.fill('{"answer":"yes","options":{"publish":true,"count":2}}');
  setReply(async (route) => route.abort("failed"));
  await submit.click();
  await dialog.getByRole("alert").waitFor();
  assert.equal(await payload.inputValue(), '{"answer":"yes","options":{"publish":true,"count":2}}');
  assert.equal(requests.length, 1);
  assert.equal(await page.evaluate(() => Object.keys(sessionStorage).some((key) => key.startsWith("evander:manual-event-trigger:"))), true);

  saveRevision("revision-2");
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.locator('.react-flow__node[data-id="alpha"]').click();
  await page.getByRole("button", { name: "Trigger manually", exact: true }).click();
  await payload.fill('{"options":{"count":2,"publish":true},"answer":"yes"}');

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
  assert.deepEqual(requests[1].body.payload, { answer: "yes", options: { publish: true, count: 2 } });
  assert.equal(requests[1].path, "/api/workflow-editor/nodes/alpha/trigger");
  assert.equal(requests[1].body.expected_revision, "revision-2");
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

test("manual retries retain request IDs from older persisted fingerprints after a save", { timeout: browserTestTimeout }, async (t) => {
  const { page, requests, setReply, saveRevision } = await editor(t);
  await page.getByRole("button", { name: "Trigger manually", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Trigger event manually" });
  await dialog.getByLabel("Event payload JSON").fill('{"answer":"yes"}');
  setReply(async (route) => route.abort("failed"));
  await dialog.getByRole("button", { name: "Trigger node", exact: true }).click();
  await dialog.getByRole("alert").waitFor();
  const { request_id: requestId, ...body } = requests[0].body;
  await page.evaluate(({ body, requestId }) => {
    const key = Object.keys(sessionStorage).find((key) => key.startsWith("evander:manual-event-trigger:"));
    sessionStorage.setItem(key, JSON.stringify({ fingerprint: JSON.stringify(body), requestId }));
  }, { body, requestId });
  saveRevision("revision-2");
  setReply(async (route) => route.fulfill(json({ event_id: "alpha:recovered-event", trace_id: "trace-1" }, 202)));
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.locator('.react-flow__node[data-id="alpha"]').click();
  await page.getByRole("button", { name: "Trigger manually", exact: true }).click();
  await dialog.getByLabel("Event payload JSON").fill('{"answer":"yes"}');
  await dialog.getByRole("button", { name: "Trigger node", exact: true }).click();
  await dialog.getByText("Run queued", { exact: true }).waitFor();
  assert.equal(requests[1].body.request_id, requestId);
  assert.equal(requests[1].body.expected_revision, "revision-2");
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

test("registered event forms preserve typed nested values, optional fields, and null", { timeout: browserTestTimeout }, async (t) => {
  const schema = { type: "object", additionalProperties: false, required: ["answer", "options", "tags"], properties: {
    answer: { type: "string", enum: ["yes", "no"] },
    options: { type: "object", additionalProperties: false, required: ["publish", "count"], properties: { publish: { type: "boolean" }, count: { type: "integer", minimum: 0 } } },
    tags: { type: "array", items: { type: "string" } }, note: { type: ["string", "null"] }, omitted: { type: "string" },
  } };
  const { page, requests } = await editor(t, { schema });
  await page.getByRole("button", { name: "Trigger manually", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Trigger event manually" });
  await dialog.getByLabel("answer", { exact: true }).selectOption("0");
  await dialog.getByLabel("count", { exact: true }).fill("0");
  await dialog.getByRole("button", { name: "Add tags item", exact: true }).click();
  await dialog.getByLabel("tags 1", { exact: true }).fill("review");
  await dialog.getByLabel("Include note", { exact: true }).check();
  await dialog.getByRole("group", { name: "note", exact: true }).getByLabel("Use null", { exact: true }).check();
  await dialog.getByRole("button", { name: "Trigger node", exact: true }).click();
  await dialog.getByText("Run queued", { exact: true }).waitFor();
  assert.deepEqual(requests[0].body.payload, { answer: "yes", options: { publish: false, count: 0 }, tags: ["review"], note: null });
  assert.equal(requests[0].body.schema_revision, "schema-1");
  assert.equal(await dialog.getByLabel("Event payload JSON", { exact: true }).count(), 0);
});

test("server schema failures stay editable and accepted retries keep the request ID", { timeout: browserTestTimeout }, async (t) => {
  const schema = { type: "object", additionalProperties: false, required: ["answer"], properties: { answer: { type: "string" } } };
  const { page, requests, setReply } = await editor(t, { schema });
  await page.getByRole("button", { name: "Trigger manually", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Trigger event manually" });
  await dialog.getByLabel("answer", { exact: true }).fill("invalid");
  setReply(async (route) => route.fulfill(json({ error: "Event payload does not match its registered schema", details: { failures: [{ path: "/answer", code: "schema.enum", message: "Choose an accepted answer" }] } }, 400)));
  await dialog.getByRole("button", { name: "Trigger node", exact: true }).click();
  await dialog.getByRole("alert").filter({ hasText: "Choose an accepted answer" }).waitFor();
  assert.equal(await dialog.getByLabel("answer", { exact: true }).inputValue(), "invalid");
  await dialog.getByLabel("answer", { exact: true }).fill("yes");
  setReply(async (route) => route.abort("failed"));
  await dialog.getByRole("button", { name: "Trigger node", exact: true }).click();
  await dialog.getByRole("alert").waitFor();
  setReply(async (route) => route.fulfill(json({ event_id: "alpha:registered", trace_id: "registered-trace" }, 202)));
  await dialog.getByRole("button", { name: "Trigger node", exact: true }).click();
  await dialog.getByText("Run queued", { exact: true }).waitFor();
  assert.notEqual(requests[0].body.request_id, requests[1].body.request_id);
  assert.equal(requests[1].body.request_id, requests[2].body.request_id);
});

test("required strings and array items can submit empty values through simple references", { timeout: browserTestTimeout }, async (t) => {
  const schema = {
    type: "object", additionalProperties: false, required: ["answer", "tags"],
    $defs: { answer: { type: "string" } },
    properties: {
      answer: { $ref: "#/$defs/answer", title: "Answer", default: "" },
      tags: { type: "array", items: { type: "string" } },
    },
  };
  const { page, requests } = await editor(t, { schema });
  await page.getByRole("button", { name: "Trigger manually", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Trigger event manually" });
  assert.equal(await dialog.getByLabel("Answer", { exact: true }).inputValue(), "");
  await dialog.getByRole("button", { name: "Add tags item", exact: true }).click();
  await dialog.getByRole("button", { name: "Trigger node", exact: true }).click();
  await dialog.getByText("Run queued", { exact: true }).waitFor();
  assert.deepEqual(requests[0].body.payload, { answer: "", tags: [""] });
});

test("reference sibling constraints preserve required values through JSON entry", { timeout: browserTestTimeout }, async (t) => {
  const schema = {
    type: "object", $ref: "#/$defs/base",
    $defs: { base: { type: "object", additionalProperties: false, required: ["id"], properties: { id: { type: "string" } } } },
    properties: { note: { type: "string" } },
  };
  const { page, requests } = await editor(t, { schema });
  await page.getByRole("button", { name: "Trigger manually", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Trigger event manually" });
  await dialog.getByLabel("Event payload JSON", { exact: true }).fill('{"id":"example"}');
  await dialog.getByRole("button", { name: "Trigger node", exact: true }).click();
  await dialog.getByText("Run queued", { exact: true }).waitFor();
  assert.deepEqual(requests[0].body.payload, { id: "example" });
});

test("nested schema resources keep scoped references editable through JSON entry", { timeout: browserTestTimeout }, async (t) => {
  const schema = {
    type: "object", required: ["nested"],
    $defs: { value: { type: "integer" } },
    properties: { nested: {
      $id: "nested.json", type: "object", additionalProperties: false, required: ["value"],
      $defs: { value: { type: "string" } },
      properties: { value: { $ref: "#/$defs/value" } },
    } },
  };
  const { page, requests } = await editor(t, { schema });
  await page.getByRole("button", { name: "Trigger manually", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Trigger event manually" });
  await dialog.getByLabel("nested JSON", { exact: true }).fill('{"value":"example"}');
  await dialog.getByRole("button", { name: "Trigger node", exact: true }).click();
  await dialog.getByText("Run queued", { exact: true }).waitFor();
  assert.deepEqual(requests[0].body.payload, { nested: { value: "example" } });
});

test("complex-field JSON cannot be submitted while invalid even after another field changes", { timeout: browserTestTimeout }, async (t) => {
  const schema = { type: "object", additionalProperties: false, required: ["answer", "choice"], properties: { answer: { type: "string" }, choice: { oneOf: [{ type: "string" }, { type: "integer" }] } } };
  const { page, requests } = await editor(t, { schema });
  await page.getByRole("button", { name: "Trigger manually", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Trigger event manually" });
  await dialog.getByLabel("choice JSON", { exact: true }).fill("{");
  await dialog.getByLabel("answer", { exact: true }).fill("yes");
  assert.equal(await dialog.getByRole("button", { name: "Trigger node", exact: true }).isDisabled(), true);
  assert.equal(requests.length, 0);
  await Promise.all([
    page.waitForResponse((response) => new URL(response.url()).pathname.endsWith("/event-schema")),
    dialog.getByRole("button", { name: "Reload event definition", exact: true }).click(),
  ]);
  assert.equal(await dialog.getByLabel("choice JSON", { exact: true }).inputValue(), "{");
  assert.equal(await dialog.getByRole("button", { name: "Trigger node", exact: true }).isDisabled(), true);
  await dialog.getByLabel("choice JSON", { exact: true }).fill("3");
  await dialog.getByRole("button", { name: "Trigger node", exact: true }).click();
  await dialog.getByText("Run queued", { exact: true }).waitFor();
  assert.deepEqual(requests[0].body.payload, { answer: "yes", choice: 3 });
});

test("nullable defaults can be changed and ordinary JSON property names stay intact", { timeout: browserTestTimeout }, async (t) => {
  const schema = JSON.parse('{"type":"object","additionalProperties":false,"required":["__proto__"],"properties":{"__proto__":{"type":"string"},"constructor":{"type":"string"},"note":{"type":["string","null"],"default":null}}}');
  const { page, requests } = await editor(t, { schema });
  await page.getByRole("button", { name: "Trigger manually", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Trigger event manually" });
  assert.equal(await dialog.getByLabel("Include constructor", { exact: true }).isChecked(), false);
  await dialog.getByLabel("__proto__", { exact: true }).fill("event data");
  await dialog.getByLabel("Include note", { exact: true }).uncheck();
  await dialog.getByLabel("Include note", { exact: true }).check();
  const nullToggle = dialog.getByRole("group", { name: "note", exact: true }).getByLabel("Use null", { exact: true });
  assert.equal(await nullToggle.isChecked(), true);
  await nullToggle.uncheck();
  await dialog.getByLabel("note", { exact: true }).fill("a note");
  await dialog.getByLabel("Include constructor", { exact: true }).check();
  await dialog.getByLabel("constructor", { exact: true }).fill("also data");
  await dialog.getByRole("button", { name: "Trigger node", exact: true }).click();
  await dialog.getByText("Run queued", { exact: true }).waitFor();
  assert.deepEqual(requests[0].body.payload, JSON.parse('{"__proto__":"event data","constructor":"also data","note":"a note"}'));
});

test("preflight validation reports success without starting a workflow", { timeout: browserTestTimeout }, async (t) => {
  const schema = { type: "object", required: ["answer"], properties: { answer: { type: "string" } } };
  const { page, requests } = await editor(t, { schema });
  let checkedPayload;
  await page.route("**/api/workflow-editor/nodes/alpha/validate-event", (route) => {
    checkedPayload = route.request().postDataJSON().payload;
    return route.fulfill(json({ valid: true, failures: [] }));
  });
  await page.getByRole("button", { name: "Trigger manually", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Trigger event manually" });
  await dialog.getByLabel("answer", { exact: true }).fill("yes");
  await dialog.getByRole("button", { name: "Validate payload", exact: true }).click();
  await dialog.getByText("Payload matches the event schema.", { exact: true }).waitFor();
  assert.deepEqual(checkedPayload, { answer: "yes" });
  assert.equal(requests.length, 0);
  await dialog.getByLabel("answer", { exact: true }).fill("updated");
  assert.equal(await dialog.getByText("Payload matches the event schema.", { exact: true }).count(), 0);
});
