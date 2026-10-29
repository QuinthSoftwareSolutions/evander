import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { browserTestTimeout, suiteBrowser } from "./support/browserHarness.mjs";

const browser = suiteBrowser();
const sourceRoot = fileURLToPath(new URL("../../../src/", import.meta.url));

async function openAddon(t, addon, responses) {
  const assetRoot = path.join(sourceRoot, `evander_${addon}_addon`, "ui_static");
  const server = createServer(async (request, response) => {
    const pathname = new URL(request.url, "http://127.0.0.1").pathname;
    const filename = pathname === "/" ? "index.html" : pathname.slice(1);
    if (filename !== path.basename(filename)) {
      response.writeHead(404).end();
      return;
    }
    try {
      const body = await readFile(path.join(assetRoot, filename));
      response.writeHead(200, { "content-type": filename.endsWith(".js")
        ? "application/javascript" : filename.endsWith(".css") ? "text/css" : "text/html" });
      response.end(body);
    } catch {
      response.writeHead(404).end();
    }
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => { server.closeAllConnections(); server.close(); });
  const context = await browser.newContext(t);
  const page = await context.newPage();
  const mutations = [];
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    if (request.method() !== "GET") mutations.push(request.url());
    const pathname = new URL(request.url()).pathname;
    const payload = responses[pathname];
    assert.ok(payload, `Unexpected add-on API request: ${pathname}`);
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(payload) });
  });
  await page.goto(`http://127.0.0.1:${server.address().port}/`, { waitUntil: "domcontentloaded" });
  return { page, mutations, errors };
}

test("Bokio keeps approval payloads visible while disclosing supporting evidence and audit history", { timeout: browserTestTimeout }, async (t) => {
  const { page, mutations, errors } = await openAddon(t, "bokio", {
    "/api/addons/bokio-addon/health": { status: "ready", settings: { company_id: "company-example" } },
    "/api/addons/bokio-addon/company": { name: "Example company" },
    "/api/addons/bokio-addon/operations": { operations: [] },
    "/api/addons/bokio-addon/prepared-actions": { actions: [{
      action_id: "action-example", kind: "bank_payment", status: "pending_approval",
      payload: { amount: 1234, recipient: "Example supplier" }, evidence_ids: ["evidence-example"],
      source: "session-example", created_at: "2026-01-01T10:00:00Z", updated_at: "2026-01-01T10:00:00Z",
      history: [{ status: "pending_approval", at: "2026-01-01T10:00:00Z", detail: "Prepared for review" }],
    }] },
  });
  const detail = page.locator("#action-detail");
  await detail.getByRole("heading", { name: "Prepared payload", exact: true }).waitFor({ state: "visible" });
  assert.equal(await detail.getByText(/Execution creates the payment in Bokio/).isVisible(), true);
  assert.equal(await detail.getByRole("button", { name: "Approve & execute" }).isDisabled(), true);
  const evidence = detail.locator("details").filter({ hasText: "Managed evidence IDs" });
  assert.equal(await evidence.locator("pre").isVisible(), false);
  await evidence.locator("summary").click();
  assert.match(await evidence.locator("pre").innerText(), /evidence-example/);
  const history = detail.locator("details").filter({ hasText: "History (1)" });
  assert.equal(await history.getByText("Prepared for review").isVisible(), false);
  await history.locator("summary").click();
  assert.equal(await history.getByText("Prepared for review").isVisible(), true);
  assert.deepEqual(mutations, []);
  assert.deepEqual(errors, []);
});

test("Miro opens an operation's details inline without leaving its activity row", { timeout: browserTestTimeout }, async (t) => {
  const { page, mutations, errors } = await openAddon(t, "miro", {
    "/api/addons/miro-addon/connections": { connections: [{
      config: { id: "connection-example", name: "Example connection", directory_path: "root", daily_call_budget: 100, boards: [] },
      connected: true, calls_today: 1, revision: 3,
    }] },
    "/api/addons/miro-addon/connections/connection-example/session-grants": { sessions: [], grants: [] },
    "/api/addons/miro-addon/connections/connection-example/activity": { subscriptions: [], operations: [
      { operation_id: "operation-first", operation: "create_board", status: "applied", actor: "agent-example" },
      { operation_id: "operation-second", operation: "update_board", status: "applied", actor: "agent-example" },
    ] },
  });
  const first = page.locator(".operation").first();
  await first.locator("summary").waitFor({ state: "visible" });
  assert.equal(await first.locator("pre").isVisible(), false);
  await first.locator("summary").click();
  assert.match(await first.locator("pre").innerText(), /operation-first/);
  assert.equal(await page.locator(".operation").nth(1).locator("pre").isVisible(), false);
  assert.equal(await page.locator("#details").innerText(), "");
  assert.deepEqual(mutations, []);
  assert.deepEqual(errors, []);
});
