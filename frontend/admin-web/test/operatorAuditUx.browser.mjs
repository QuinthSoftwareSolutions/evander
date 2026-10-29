import assert from "node:assert/strict";
import test from "node:test";
import { operatorAuditFixture, auditCall } from "./fixtures/operatorAudit.mjs";
import { browserTestTimeout, suiteBrowser } from "./support/browserHarness.mjs";

const browser = suiteBrowser();
async function fixturePage(t) {
  const fixture = await operatorAuditFixture();
  t.after(() => fixture.server.close());
  const context = await browser.newContext(t, { viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  return { ...fixture, page };
}

test("audit links open the referenced record and secondary filters reset together", { timeout: browserTestTimeout }, async t => {
  const { url, page } = await fixturePage(t);
  for (const [label, hash] of [
    ["Session", "#sessions?session_id=session-audit"], ["Approval", "#approvals?request_id=approval-audit"],
    ["Workflow run", "#workflow?run_id=run-audit"], ["Agent", "#agents?agent_id=agent-audit"], ["VM", "#vms?vm_name=vm-audit"],
  ]) {
    await page.goto(url + "/#tool-calls");
    await page.getByRole("button", { name: "read_repository", exact: true }).click();
    await page.getByRole("button", { name: `${label}: ${auditCall[{ Session: "session_id", Approval: "approval_request_id", "Workflow run": "workflow_run_id", Agent: "agent_id", VM: "vm_id" }[label]]}`, exact: true }).click();
    await page.waitForURL("**/" + hash);
  }
  await page.goto(url + "/#tool-calls?session_id=session-audit&tool_name=read_repository");
  await page.getByLabel("Session", { exact: true }).waitFor();
  assert.equal(await page.locator(".filter-disclosure").getAttribute("open"), "");
  await page.getByRole("button", { name: "Clear filters", exact: true }).click();
  assert.equal(await page.getByLabel("Session", { exact: true }).inputValue(), "");
  assert.equal(await page.getByLabel("Tool", { exact: true }).inputValue(), "");
  await page.getByRole("button", { name: "read_repository", exact: true }).click();
  assert.equal(await page.getByText("Normalized metadata", { exact: true }).locator("..").getAttribute("open"), null);
  await page.getByRole("textbox", { name: "Arguments", exact: true }).waitFor({ state: "visible" });
});

test("artifact key opens with Enter and the compact inventory retains checksum and session access", { timeout: browserTestTimeout }, async t => {
  const { url, page } = await fixturePage(t);
  await page.goto(url + "/#artifacts");
  await page.getByLabel("Key", { exact: true }).fill("runs/review");
  await page.getByLabel("Key", { exact: true }).press("Enter");
  const inspector = page.locator(".record-inspector");
  await inspector.getByRole("heading", { name: "runs/review", exact: true }).waitFor();
  assert.equal(await inspector.getByRole("link", { name: "session-audit", exact: true }).getAttribute("href"), "#sessions?session_id=session-audit");
  assert.equal(await inspector.getByText("a".repeat(64), { exact: true }).isVisible(), true);
  assert.equal(await page.locator(".artifacts-table th").count(), 5);
  assert.equal(await page.getByLabel("Published after", { exact: true }).isVisible(), false);
  assert.equal(await inspector.locator("details").getAttribute("open"), null);
  for (const width of [1280, 768, 390]) {
    await page.setViewportSize({ width, height: 900 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true, `overflow at ${width}`);
  }
});

test("dashboard attention precedes news and opens service or capacity detail in one action", { timeout: browserTestTimeout }, async t => {
  const { url, page } = await fixturePage(t);
  await page.goto(url + "/#dashboard");
  const attention = page.getByRole("region", { name: "Needs attention" });
  const storage = attention.getByRole("button", { name: /Control-plane storage critical/ });
  await storage.waitFor();
  assert.equal(await attention.getByText(/Some attention sources are unavailable/).count(), 0);
  assert.equal(await page.locator(".dashboard-metric-strip").getByText("Partial data", { exact: true }).count(), 0);
  await storage.click();
  const capacity = page.locator(".dashboard-panel").filter({ hasText: "Capacity and execution" });
  await capacity.locator(".dashboard-block-workers").waitFor({ state: "visible" });
  assert.equal(await capacity.locator(".dashboard-block-workers details").count(), 0);
  await page.setViewportSize({ width: 390, height: 900 });
  const news = page.locator(".dashboard-panel-column-news");
  assert.ok((await attention.boundingBox()).y < (await news.boundingBox()).y);
  await attention.getByRole("button", { name: /1 failed services/ }).click();
  await page.waitForURL("**/#workers?worker_id=worker-audit");
});
