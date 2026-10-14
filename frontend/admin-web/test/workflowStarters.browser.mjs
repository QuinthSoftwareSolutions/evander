import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { createServer } from "vite";
import { suiteBrowser, listeningAddress, browserTestTimeout } from "./support/browserHarness.mjs";

const browser = suiteBrowser();
const root = fileURLToPath(new URL("..", import.meta.url));
const fields = {
  name: { type: "string", title: "Workflow name", default: "daily-check" },
  session_id: { type: "string", title: "Agent", format: "evander-session" },
  prompt: { type: "string", title: "Task instructions", format: "textarea" },
};
const starter = { id: "core/scheduled-task", label: "Run an agent task on a schedule", provider: "Evander", description: "Run a recurring task.", input_schema: { properties: fields, required: Object.keys(fields) } };
const preview = { ready: true, preview_revision: "reviewed-preview", workflow_pack: "starter-daily-check.v1.json", checks: [{ id: "agent", label: "Selected agent and directory", status: "ready" }], pack: { graphs: [{ directory_path: "root" }], ir: { nodes: [{ node: { id: "schedule", type: "trigger:cron", display_name: "Scheduled task", config: { expression: "30 9 * * 1-5", timezone: "Europe/Stockholm" } } }, { node: { id: "run", type: "effect:agent:enqueue-prompt", display_name: "Run agent task", config: {} } }], edges: [] } }, verification: { instructions: "Run the task once now or wait for its schedule.", manual_trigger_node_id: "test" } };
const receipt = { id: "instance-1", starter_id: starter.id, workflow_pack: preview.workflow_pack, created_at: "2026-10-14T08:00:00Z", verification: preview.verification };

async function open(t, options = {}) {
  const server = await createServer({ root, server: { host: "127.0.0.1", port: 0 }, logLevel: "error" });
  await server.listen();
  t.after(() => server.close());
  const context = await browser.newContext(t, { viewport: { width: 1280, height: 1000 } });
  const page = await context.newPage();
  const mutations = [];
  const installed = [];
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/api/admin/workflow-starters**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const payload = request.method() === "POST" ? request.postDataJSON() : null;
    if (payload) mutations.push({ path, payload });
    if (options.route && await options.route(route, path, payload)) return;
    let data = { starters: [starter], agents: options.empty ? [] : [{ value: "assistant", label: "Research assistant" }], instances: installed };
    if (path.endsWith("/preview")) data = preview;
    if (path.endsWith("/activate")) { data = receipt; installed.push(receipt); }
    if (path.endsWith("/test")) data = { event_id: "run-1" };
    if (path.endsWith("/verify")) data = { state: "running", message: "The run was received. Waiting for the task to complete.", event_id: "run-1" };
    await route.fulfill({ json: data });
  });
  await page.goto(`http://127.0.0.1:${listeningAddress(server).port}/test/fixtures/workflow-starters.html${options.locked ? "?locked" : ""}`);
  return { page, mutations, errors };
}

async function configure(page) {
  await page.getByRole("button", { name: /Run an agent task on a schedule/ }).click();
  await page.getByLabel("Agent", { exact: true }).selectOption("assistant");
  await page.getByLabel("Task instructions").fill("Report on the project's open tasks.");
  await page.getByRole("button", { name: "Check & preview" }).click();
  await page.getByRole("heading", { name: "Ready to activate" }).waitFor();
}

test("guided activation uses reviewed input and queued work never appears verified", { timeout: browserTestTimeout }, async (t) => {
  const { page, mutations, errors } = await open(t);
  await configure(page);
  assert.equal(mutations.length, 1, "preview must not activate a workflow");
  await mkdir(`${root}/../../output/playwright`, { recursive: true });
  await page.screenshot({ path: `${root}/../../output/playwright/starter-preview.png`, fullPage: true });
  await page.getByRole("button", { name: "Activate workflow" }).click();
  await page.getByRole("button", { name: "Run task once now" }).click();
  await page.getByText("The run was received. Waiting for the task to complete.").waitFor();
  assert.equal(await page.getByText("Verified", { exact: true }).count(), 0);
  assert.equal(mutations[1].payload.preview_revision, "reviewed-preview");
  assert.equal(mutations[1].payload.values.session_id, "assistant");
  assert.equal(await page.getByRole("button", { name: "Run task once now" }).isDisabled(), true);
  await page.reload();
  await page.getByText("WORKFLOW ACTIVATED", { exact: true }).waitFor();
  assert.deepEqual(errors, []);
});

test("changing a field discards a preview and locks block activation", { timeout: browserTestTimeout }, async (t) => {
  const { page, mutations, errors } = await open(t, { locked: true });
  await configure(page);
  assert.equal(await page.getByRole("button", { name: "Activate workflow" }).isDisabled(), true);
  await page.getByLabel("Task instructions").fill("A different task");
  assert.equal(await page.getByRole("button", { name: "Activate workflow" }).count(), 0);
  assert.equal(mutations.length, 1);
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
  await page.screenshot({ path: `${root}/../../output/playwright/starter-mobile.png`, fullPage: true });
  assert.deepEqual(errors, []);
});

test("empty installations offer an agent setup path without enabling a workflow", { timeout: browserTestTimeout }, async (t) => {
  const { page, mutations } = await open(t, { empty: true });
  await page.getByText("First, create an agent.").waitFor();
  assert.equal(await page.getByRole("link", { name: /Open Agents/ }).getAttribute("href"), "#agents");
  await page.getByRole("button", { name: /Run an agent task on a schedule/ }).click();
  assert.equal(await page.getByRole("button", { name: "Check & preview" }).isDisabled(), true);
  assert.deepEqual(mutations, []);
});

test("a late preview response cannot activate edited inputs", { timeout: browserTestTimeout }, async (t) => {
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  const { page } = await open(t, { route: async (route, path) => {
    if (!path.endsWith("/preview")) return false;
    await pending;
    await route.fulfill({ json: preview });
    return true;
  } });
  await page.getByRole("button", { name: /Run an agent task on a schedule/ }).click();
  await page.getByLabel("Agent", { exact: true }).selectOption("assistant");
  await page.getByLabel("Task instructions").fill("Original task");
  const requested = page.waitForRequest("**/workflow-starters/preview");
  await page.getByRole("button", { name: "Check & preview" }).click();
  await requested;
  await page.getByLabel("Task instructions").fill("Different task");
  const response = page.waitForResponse("**/workflow-starters/preview");
  release();
  await response;
  assert.equal(await page.getByRole("button", { name: "Activate workflow" }).count(), 0);
});

test("verified status is shown only after the completion check", { timeout: browserTestTimeout }, async (t) => {
  const { page } = await open(t, { route: async (route, path) => {
    if (!path.endsWith("/verify")) return false;
    await route.fulfill({ json: { state: "verified", message: "The task completed and its required outcome was recorded.", event_id: "run-1" } });
    return true;
  } });
  await configure(page);
  assert.equal(await page.getByText("Verified", { exact: true }).count(), 0);
  await page.getByRole("button", { name: "Activate workflow" }).click();
  await page.getByText("Verified", { exact: true }).waitFor();
  assert.match(await page.getByRole("link", { name: /Open workflow & run/ }).getAttribute("href"), /event_id=run-1/);
});
