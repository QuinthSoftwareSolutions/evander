import assert from "node:assert/strict";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { suiteBrowser, withTimeout } from "./support/browserHarness.mjs";

const browser = suiteBrowser();
const primary = JSON.stringify(["openai", "PRIMARY_KEY"]);
const legacy = JSON.stringify(["anthropic", null]);
const report = {
  period_start: "2026-09-26T22:00:00Z", period_end: "2026-09-28T12:00:00Z",
  timezone: "Europe/Stockholm", directory_path: "root", currency: "USD", pricing_version: "test",
  total_cost: 13, estimated_cost: 13, reported_cost: 0,
  coverage: { priced_events: 4, unpriced_events: 0, unknown_billing_events: 0,
    excluded_subscription_events: 1, excluded_local_events: 0, partial: false },
  groups: [
    { provider: "openai", api_key_name: "PRIMARY_KEY", category: "Development", cost: 8, priced_events: 1, unpriced_events: 0 },
    { provider: "openai", api_key_name: "PRIMARY_KEY", category: "Review", cost: 2, priced_events: 1, unpriced_events: 0 },
    { provider: "anthropic", api_key_name: null, category: null, cost: 3, priced_events: 2, unpriced_events: 0 },
  ],
  accounts: [{ id: primary, provider: "openai", api_key_name: "PRIMARY_KEY" }, { id: legacy, provider: "anthropic", api_key_name: null }],
  daily: [
    { date: "2026-09-27", cost: 9, cumulative_cost: 9, categories: [{ category: "Development", cost: 8 }, { category: null, cost: 1 }],
      accounts: [{ id: primary, cost: 8, cumulative_cost: 8 }, { id: legacy, cost: 1, cumulative_cost: 1 }] },
    { date: "2026-09-28", cost: 4, cumulative_cost: 13, categories: [{ category: "Review", cost: 2 }, { category: null, cost: 2 }],
      accounts: [{ id: primary, cost: 2, cumulative_cost: 10 }, { id: legacy, cost: 2, cumulative_cost: 3 }] },
  ],
};

test("cost dashboard links to a reconciled report and retains group selection", { timeout: 60_000 }, async (t) => {
  const html = `<html><head><script type="module" src="/@vite/client"></script></head><body><div id="root"></div>
    <script type="module">
      import React from 'react';
      import {createRoot} from 'react-dom/client';
      import {InferenceCostsPanel} from '/src/features/reports/InferenceCostsPanel.tsx';
      import {ReportsPage} from '/src/features/reports/ReportsPage.tsx';
      import '/src/design/baseline.css';
      import '/src/design/tokens.css';
      import '/src/design/controls.css';
      import '/src/design/layout.css';
      import '/src/design/collections.css';
      import '/src/design/dashboard.css';
      import '/src/design/workspaces.css';
      import '/src/design/workflow.css';
      import '/src/design/surfaces.css';
      document.documentElement.dataset.adminDesign = '';
      function App() { const [hash, setHash] = React.useState(location.hash);
        React.useEffect(() => { const listener = () => setHash(location.hash); window.addEventListener('hashchange', listener);
          return () => window.removeEventListener('hashchange', listener); }, []);
        return hash.startsWith('#reports') ? React.createElement(ReportsPage) : React.createElement(InferenceCostsPanel, {compact:true}); }
      createRoot(document.getElementById('root')).render(React.createElement(App));
    </script></body></html>`;
  const server = await createServer({ root: fileURLToPath(new URL("..", import.meta.url)), logLevel: "error",
    server: { host: "127.0.0.1", port: 0 }, plugins: [{ name: "cost-fixture", configureServer(vite) {
      vite.middlewares.use("/cost-fixture", async (_req, res) => {
        res.setHeader("Content-Type", "text/html"); res.end(await vite.transformIndexHtml("/cost-fixture", html));
      });
    } }] });
  t.after(() => server.close());
  await withTimeout(server.listen(), 15_000, "Cost test server did not start");
  const context = await browser.newContext(t, { timezoneId: "Europe/Stockholm", locale: "en-US" });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const requests = [];
  let response = report;
  await page.route("**/api/**", (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/inference-costs")) requests.push(url);
    const payload = url.pathname.endsWith("/inference-costs") ? response
      : url.pathname.endsWith("/conformance") ? { reports: [] } : { graphs: [] };
    return route.fulfill({ contentType: "application/json", body: JSON.stringify(payload) });
  });
  await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/cost-fixture`);
  await page.getByRole("img", { name: "2026-09-28 · Total: $13.00 accumulated", exact: true }).waitFor();
  assert.equal(requests[0].searchParams.get("timezone"), "Europe/Stockholm");
  assert.equal(requests[0].searchParams.has("start"), false);
  assert.equal(await page.getByRole("table").count(), 0);
  await page.getByLabel("Accumulated chart").selectOption("compare");
  await page.getByRole("img", { name: "2026-09-28 · anthropic / Unknown key: $3.00 accumulated", exact: true }).waitFor();
  await page.getByLabel("Provider / API key", { exact: true }).selectOption([primary]);
  await page.getByRole("img", { name: "2026-09-28 · openai / PRIMARY_KEY: $10.00 accumulated", exact: true }).waitFor();
  await page.getByRole("link", { name: "Full cost report" }).click();
  await page.getByRole("table").waitFor();
  assert.equal(await page.getByLabel("Provider / API key", { exact: true }).evaluate((select) => select.selectedOptions[0].value), primary);
  assert.match(await page.getByRole("table").innerText(), /Uncategorized/);
  assert.match(await page.getByRole("table").innerText(), /Unknown key/);
  assert.match(await page.locator("tfoot").innerText(), /\$13\.00/);
  await page.screenshot({ path: "/tmp/evander-inference-cost-report-desktop.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: "/tmp/evander-inference-cost-report-mobile.png", fullPage: true });
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.getByRole("img", { name: "2026-09-27 · Development: $8.00", exact: true }).focus();
  assert.match(await page.locator(".cost-chart-detail").last().textContent(), /Development: \$8.00/);
  await page.getByLabel("Period", { exact: true }).selectOption("custom");
  await page.getByLabel("From", { exact: true }).fill("2026-03-29");
  await Promise.all([page.waitForResponse((res) => res.url().includes("end=2026-03-31")),
    page.getByLabel("Through", { exact: true }).fill("2026-03-30")]);
  assert.equal(requests.at(-1).searchParams.get("start"), "2026-03-29");
  await page.getByLabel("Through", { exact: true }).fill("");
  await page.getByRole("alert").filter({ hasText: "Choose a valid" }).waitFor();
  await page.getByLabel("Through", { exact: true }).fill("2026-03-30");
  response = { ...report, total_cost: 0, groups: [{ ...report.groups[0], cost: null, priced_events: 0, unpriced_events: 1 }],
    coverage: { ...report.coverage, priced_events: 0, unpriced_events: 1, partial: true } };
  await page.getByRole("button", { name: "Refresh costs", exact: true }).click();
  await page.getByText("Partial total", { exact: true }).waitFor();
  assert.match(await page.getByRole("table").innerText(), /Unavailable/);
  assert.equal(await page.getByRole("img").count(), 0);
  assert.deepEqual(errors, []);
});
