import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { createServer } from "vite";
import { browserHookTimeout, browserTestTimeout, listeningAddress, suiteBrowser, withTimeout } from "./support/browserHarness.mjs";

const browser = suiteBrowser();
const json = (payload, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(payload) });

async function editor(t, { writable = false, mobile = false, assistantEnabled = true, delaySession = false } = {}) {
  const server = await createServer({
    root: path.resolve(fileURLToPath(new URL("..", import.meta.url))),
    server: { host: "127.0.0.1", port: 0 }, logLevel: "error",
  });
  t.after(() => server.close(), { timeout: browserHookTimeout });
  await withTimeout(server.listen(), 15_000, "Vite did not start within 15 seconds");
  const context = await browser.newContext(t, mobile
    ? { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true }
    : { viewport: { width: 1440, height: 1100 } });
  const page = await context.newPage();
  const pageErrors = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  t.after(() => assert.deepEqual(pageErrors, []));
  const permissions = ["workflows.read", ...(writable ? ["workflows.write"] : [])];
  const requests = [];
  const mutations = [];
  const messages = [{ id: "old-answer", role: "assistant", content: "Earlier conversation", created_at: "2026-10-15T10:00:00Z" }];
  const assistantSession = { status: "ready", error: null, updated_at: "2026-10-15T10:00:00Z" };
  const transcript = () => ({ thread_id: "thread-1", messages, assistant_capability_mode: "read-only", assistant_session: assistantSession });
  let releaseSession;
  const sessionReady = new Promise((resolve) => { releaseSession = resolve; });
  t.after(releaseSession);
  if (!delaySession) releaseSession();
  let failMessage = false;
  const config = {
    triggers: [
      { id: "entry", display_name: "Incoming request", type: "trigger:event", config: { event_type: "demo.requested", enabled: false } },
      { id: "other", display_name: "Unrelated trigger", type: "trigger:cron", config: { expression: "0 * * * *" } },
    ], gates: [], queries: [],
    effects: [{ id: "action", display_name: "Record result", type: "effect:message", config: { text: "Original message", api_key: "synthetic-browser-key" } }],
    edges: [{ source_id: "entry", source_port: "completed", target_id: "action" }],
  };
  const document = {
    config_path: "workflow.json", layout_path: "layout.json", config_exists: true, layout_exists: true,
    revision: "saved-1", config, layout: { nodes: { entry: { x: 200, y: 80 }, action: { x: 600, y: 80 }, other: { x: 200, y: 300 } } },
    graphs: [
      { id: "chosen", name: "Request workflow", directory_path: "root", user_ids: [], node_ids: ["entry", "action"] },
      { id: "other-graph", name: "Other workflow", directory_path: "root", user_ids: [], node_ids: ["other"] },
    ], output_schemas: {}, validation_diagnostics: [],
  };
  const field = (key, label) => ({ key, label, kind: "string", required: false, bindable: false, read_only: false, multiline: false, options: [], item_fields: [] });
  const catalog = { worker_id: "trigger-orchestrator", internal_events: [], nodes: [
    { kind: "trigger", type: "trigger:event", label: "Event", fields: [field("event_type", "Event type")], input_ports: [], output_ports: [{ key: "completed", label: "Completed" }] },
    { kind: "trigger", type: "trigger:cron", label: "Schedule", fields: [], input_ports: [], output_ports: [] },
    { kind: "effect", type: "effect:message", label: "Message", fields: [field("text", "Message text")], input_ports: [], output_ports: [] },
  ] };
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const name = new URL(request.url()).pathname;
    if (name.startsWith("/api/workflow-editor/") && request.method() !== "GET" && !name.endsWith("/validate")) mutations.push(name);
    if (name === "/api/auth/session") return route.fulfill(json({
      auth_mode: "none", auth_required: false, authenticated: true, username: "viewer", display_name: "Workflow viewer",
      permissions, effective_permissions: permissions, can_manage_users: false, session_id: "session-1", privileged_operations: { unlocked: writable },
    }));
    if (name === "/api/admin/state") return route.fulfill(json({
      selected_directory_path: "root", directory_scoped_config_enabled: false, execution_backend: "libvirt_vm",
      permissions, effective_permissions: permissions, privileged_operations: { unlocked: writable },
      assistant: { enabled: assistantEnabled, show_launcher_when_disabled: false },
    }));
    if (name === "/api/configuration/state") return route.fulfill(json({
      directory_scoped_config_enabled: false, selected_directory_path: "root", execution_backend: "libvirt_vm",
      selected_directory: { path: "root", parent_path: null, can_create: false, can_delete: false },
      tree: { ok: true, data: { directory: { id: "root", name: "root", path: "root", parent_id: null, metadata: {} }, children: [], config_domains: [] }, errors: [], validation_diagnostics: [] },
    }));
    if (name === "/api/admin/deployment-status") return route.fulfill(json({ active: false }));
    if (name === "/api/workflow-editor/document") return route.fulfill(json(document));
    if (name === "/api/workflow-editor/catalog") return route.fulfill(json(catalog));
    if (name === "/api/workflow-editor/validate") {
      const body = request.postDataJSON();
      return route.fulfill(json({ valid: true, config: body.config, layout: body.layout, graphs: body.graphs, diagnostics: [], output_schemas: {} }));
    }
    if (name === "/api/chat/session") {
      await sessionReady;
      return route.fulfill(json({ user_id: "viewer", ...transcript() }));
    }
    if (name === "/api/chat/transcript/thread-1") return route.fulfill(json(transcript()));
    if (name === "/api/chat/stream/thread-1") return route.fulfill({ contentType: "text/event-stream", body: ": keepalive\n\n" });
    if (name === "/api/chat/message") {
      const body = request.postDataJSON();
      requests.push(body);
      if (failMessage) return route.fulfill(json({ error: "Assistant temporarily unavailable" }, 503));
      messages.push(
        { id: `user-${requests.length}`, role: "user", content: body.content, created_at: "2026-10-15T10:01:00Z", status: "answered" },
        { id: `answer-${requests.length}`, role: "assistant", content: "This graph handles incoming requests and records their result.", created_at: "2026-10-15T10:01:01Z" },
      );
      return route.fulfill(json(transcript()));
    }
    if (name.startsWith("/api/admin/branding/assets/")) return route.fulfill({ status: 204, body: "" });
    return route.fulfill(json({ notifications: [], unread_count: 0, completed_tutorial_ids: [], skipped_tutorial_ids: [] }));
  });
  await page.goto(`http://127.0.0.1:${listeningAddress(server).port}/#workflow`, { waitUntil: "domcontentloaded" });
  await page.locator('.react-flow__node[data-id="entry"]').waitFor();
  async function selectGraph(name = "Request workflow") {
    await page.getByRole("button", { name: "Graphs", exact: true }).filter({ visible: true }).click();
    await page.getByRole("button", { name: `${name} ${name === "Request workflow" ? "2 nodes" : "1 node"}`, exact: true }).click();
  }
  return { page, requests, mutations, selectGraph, releaseSession, setFailure: (value) => { failMessage = value; } };
}

async function screenshot(page, name) {
  if (!process.env.WORKFLOW_EXPLANATION_SCREENSHOTS) return;
  const directory = path.resolve("../../output/playwright");
  await mkdir(directory, { recursive: true });
  await page.screenshot({ path: path.join(directory, `${name}.png`), fullPage: true });
}

// The real shell-to-editor-to-chat path must submit the current graph exactly once and keep existing conversation state.
test("Explain graph opens the assistant with unsaved nodes while preserving history and draft", { timeout: browserTestTimeout }, async (t) => {
  const { page, requests, mutations, selectGraph } = await editor(t, { writable: true });
  await page.locator('.react-flow__node[data-id="action"]').click();
  await page.locator(".inspector-field").filter({ hasText: "Message text" }).getByRole("textbox").fill("Unsaved message for the explanation");
  await page.getByRole("button", { name: /^Assistant / }).click();
  await page.getByLabel("Chat message").fill("Keep this draft for later");
  await page.getByRole("button", { name: "Minimize assistant" }).click();
  await selectGraph();
  await page.getByLabel("Graph name", { exact: true }).waitFor();
  await page.waitForFunction(() => document.querySelector(".workflow-graph-editor input")?.value === "Request workflow");
  await screenshot(page, "workflow-graph-explanation-action");
  await page.getByRole("button", { name: "Explain graph", exact: true }).click();
  const assistant = page.getByRole("region", { name: "Admin assistant" });
  await assistant.waitFor();
  await assistant.getByText("This graph handles incoming requests and records their result.", { exact: true }).waitFor();
  assert.equal(await assistant.getByLabel("Chat message").inputValue(), "Keep this draft for later");
  assert.equal(await assistant.getByText("Earlier conversation", { exact: true }).count(), 1);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].thread_id, "thread-1");
  assert.equal(requests[0].content.includes("config"), false, "the visible message stays concise");
  const snapshot = requests[0].context;
  assert.equal(snapshot.graph.id, "chosen");
  assert.equal(snapshot.has_unsaved_changes, true);
  assert.equal(snapshot.config.effects[0].config.text, "Unsaved message for the explanation");
  assert.equal(snapshot.config.effects[0].config.api_key, "[redacted]");
  assert.equal(JSON.stringify(requests[0]).includes("synthetic-browser-key"), false);
  assert.deepEqual(snapshot.config.triggers.map(({ id }) => id), ["entry"]);
  assert.equal(JSON.stringify(requests[0].context).includes("Unrelated trigger"), false);
  assert.deepEqual(mutations, [], "explaining must not save, run, or otherwise mutate workflows");
  await screenshot(page, "workflow-graph-explanation-desktop");
});

test("mobile read-only explanation waits for chat initialization and recovers after a send failure", { timeout: browserTestTimeout }, async (t) => {
  const { page, requests, mutations, selectGraph, releaseSession, setFailure } = await editor(t, { mobile: true, delaySession: true });
  await selectGraph();
  setFailure(true);
  await page.getByRole("button", { name: "Explain graph", exact: true }).click();
  await page.getByRole("region", { name: "Admin assistant" }).waitFor();
  assert.equal(requests.length, 0, "a request must wait until its chat thread is loaded");
  releaseSession();
  await page.getByText("Assistant temporarily unavailable", { exact: true }).waitFor();
  assert.equal(requests.length, 1);
  await page.getByRole("button", { name: "Minimize assistant" }).click();
  setFailure(false);
  await selectGraph("Other workflow");
  await page.getByRole("button", { name: "Explain graph", exact: true }).click();
  await page.getByText("This graph handles incoming requests and records their result.", { exact: true }).waitFor();
  assert.equal(requests.length, 2);
  const snapshot = requests[1].context;
  assert.equal(snapshot.graph.id, "other-graph");
  assert.deepEqual(snapshot.config.triggers.map(({ id }) => id), ["other"]);
  assert.deepEqual(mutations, []);
  await screenshot(page, "workflow-graph-explanation-mobile");
});

test("viewers can select a graph and see why explanation is unavailable when the assistant is disabled", { timeout: browserTestTimeout }, async (t) => {
  const { page, requests, selectGraph } = await editor(t, { assistantEnabled: false });
  await selectGraph();
  assert.equal(await page.getByRole("button", { name: "Explain graph", exact: true }).isDisabled(), true);
  await page.getByText("The built-in assistant is disabled.", { exact: true }).waitFor();
  assert.equal(requests.length, 0);
});
