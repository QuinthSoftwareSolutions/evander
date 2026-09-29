import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { suiteBrowser, withTimeout } from "./support/browserHarness.mjs";

const browser = suiteBrowser();

function profile(profileId, displayName, count, update = {}) {
  return {
    profile_id: profileId, display_name: displayName, enabled: true, available: true,
    signed_in: true, account_id: `account-${profileId}`, email: `${profileId}@example.test`,
    plan_type: "plus", pinned_account_id: `account-${profileId}`, active_job_count: 0,
    queued_prompt_count: 1, error: null, usage: null,
    rate_limits: {
      default: { secondary: { window_duration_minutes: 10080, used_percent: 80 } },
      reset_credits: count === null ? null : {
        available_count: count,
        // The detail list can be capped; the reported count remains authoritative.
        credits: [{ id: "credit-1", status: "available" }],
      },
    },
    ...update,
  };
}

async function fixture(t, routeHandler) {
  const html = `<html><head><meta name="viewport" content="width=device-width, initial-scale=1" /></head>
    <body><div id="root"></div><script type="module">
      import React from 'react';
      import { createRoot } from 'react-dom/client';
      import { AdminUiContext } from '/src/features/admin/AdminContext.tsx';
      import { SubscriptionsPage } from '/src/features/subscriptions/SubscriptionsPage.tsx';
      import '/src/design/baseline.css';
      import '/src/design/tokens.css';
      import '/src/design/controls.css';
      import '/src/design/layout.css';
      import '/src/design/surfaces.css';
      import '/src/design/workspaces.css';
      document.documentElement.dataset.adminDesign = '';
      function App() {
        const [privilegedUnlocked, unlock] = React.useState(false);
        const [selectedDirectoryPath, selectDirectory] = React.useState('engineering');
        return React.createElement(React.Fragment, null,
          React.createElement('button', { onClick: () => unlock(true) }, 'Unlock operations'),
          React.createElement('select', { 'aria-label': 'Directory', value: selectedDirectoryPath,
            onChange: (event) => selectDirectory(event.target.value) },
            React.createElement('option', { value: 'engineering' }, 'Engineering'),
            React.createElement('option', { value: 'operations' }, 'Operations')),
          React.createElement(AdminUiContext.Provider, { value: { privilegedUnlocked, selectedDirectoryPath } },
            React.createElement(SubscriptionsPage)));
      }
      createRoot(document.getElementById('root')).render(React.createElement(App));
    </script></body></html>`;
  const server = await createServer({
    root: fileURLToPath(new URL("..", import.meta.url)), logLevel: "error",
    server: { host: "127.0.0.1", port: 0 },
    plugins: [{ name: "subscription-reset-fixture", configureServer(vite) {
      vite.middlewares.use("/subscription-fixture", async (_request, response) => {
        response.setHeader("Content-Type", "text/html");
        response.end(await vite.transformIndexHtml("/subscription-fixture", html));
      });
    } }],
  });
  t.after(() => server.close());
  await withTimeout(server.listen(), 15_000, "Subscription test server did not start");
  const context = await browser.newContext(t);
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/api/**", routeHandler);
  await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/subscription-fixture`);
  return { page, errors };
}

function row(page, displayName) {
  return page.locator("details.subscription-profile").filter({
    has: page.getByText(displayName, { exact: true }),
  });
}

async function confirmClick(page, button, accept = true) {
  await Promise.all([
    page.waitForEvent("dialog").then((dialog) => accept ? dialog.accept() : dialog.dismiss()),
    button.click(),
  ]);
}

test("banked resets use the authoritative count and redemption retries consume once", { timeout: 60_000 }, async (t) => {
  let selected = profile("team-plus", "Team Plus", 3);
  const others = [
    profile("empty", "No resets", 0),
    profile("unknown", "Unknown resets", null),
    profile("malformed", "Malformed count", "2"),
    profile("fractional", "Fractional count", 1.5),
    profile("negative", "Negative count", -1),
    profile("disabled", "Disabled profile", 2, { enabled: false }),
    profile("signed-out", "Signed out profile", 2, { signed_in: false }),
  ];
  const requests = [];
  const redeemedKeys = new Set();
  let consumed = 0;
  let nextOutcome = "reset";
  let loseResponse = true;
  const { page, errors } = await fixture(t, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() === "GET") {
      return route.fulfill({ json: { worker_url: "http://worker.test", profiles: [selected, ...others] } });
    }
    const body = request.postDataJSON();
    requests.push({ url, body });
    let outcome = nextOutcome;
    if (redeemedKeys.has(body.idempotency_key)) outcome = "alreadyRedeemed";
    else if (outcome === "reset") {
      redeemedKeys.add(body.idempotency_key);
      consumed += 1;
      selected = profile("team-plus", "Team Plus", 3 - consumed);
      selected.rate_limits.default.secondary.used_percent = 0;
    } else if (outcome === "noCredit") {
      selected = profile("team-plus", "Team Plus", 0);
    }
    if (loseResponse) {
      loseResponse = false;
      return route.fulfill({ status: 502, json: { error: "Response lost after redemption" } });
    }
    return route.fulfill({ json: { outcome, profile: selected } });
  });
  const selectedRow = row(page, "Team Plus");
  await selectedRow.waitFor();
  assert.match(await selectedRow.locator("summary").first().innerText(), /3\s+Banked resets/);
  assert.match(await row(page, "No resets").innerText(), /0\s+Banked resets/);
  for (const name of ["Unknown resets", "Malformed count", "Fractional count", "Negative count"]) {
    assert.match(await row(page, name).innerText(), /Unavailable\s+Banked resets/);
  }
  await selectedRow.locator("summary").first().click();
  const redeem = selectedRow.getByRole("button", { name: "Redeem one reset", exact: true });
  assert.equal(await redeem.isDisabled(), true);
  await page.getByRole("button", { name: "Unlock operations" }).click();
  assert.equal(await redeem.isEnabled(), true);
  for (const name of ["No resets", "Unknown resets", "Malformed count", "Disabled profile", "Signed out profile"]) {
    const other = row(page, name);
    await other.locator("summary").first().click();
    assert.equal(await other.getByRole("button", { name: "Redeem one reset" }).isDisabled(), true);
    await other.locator("summary").first().click();
  }
  await confirmClick(page, redeem, false);
  assert.equal(requests.length, 0);
  // Exercise the UUID fallback used by private HTTP deployments.
  await page.evaluate(() => { window.crypto.randomUUID = undefined; });
  await confirmClick(page, redeem);
  const retry = selectedRow.getByRole("button", { name: "Retry reset redemption", exact: true });
  await retry.waitFor();
  await retry.click();
  await page.getByRole("status").filter({ hasText: "already redeemed" }).waitFor();
  assert.equal(consumed, 1);
  assert.equal(requests.length, 2);
  assert.equal(requests[0].body.idempotency_key, requests[1].body.idempotency_key);
  assert.match(requests[0].body.idempotency_key, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  assert.match(await selectedRow.locator("summary").first().innerText(), /100%\s+7-day remaining/);
  assert.match(await selectedRow.locator("summary").first().innerText(), /2\s+Banked resets/);
  await confirmClick(page, redeem);
  await page.getByRole("status").filter({ hasText: "One banked reset redeemed" }).waitFor();
  assert.equal(consumed, 2);
  assert.notEqual(requests[2].body.idempotency_key, requests[1].body.idempotency_key);
  nextOutcome = "nothingToReset";
  await confirmClick(page, redeem);
  await page.getByRole("status").filter({ hasText: "No reset was consumed" }).waitFor();
  assert.equal(consumed, 2);
  nextOutcome = "noCredit";
  await confirmClick(page, redeem);
  await page.getByRole("status").filter({ hasText: "no banked resets available" }).waitFor();
  assert.equal(await redeem.isDisabled(), true);
  for (const { url } of requests) {
    assert.equal(url.pathname, "/api/admin/codex-subscriptions/team-plus/reset-credits/consume");
    assert.equal(url.searchParams.get("directory_path"), "engineering");
  }
  assert.deepEqual(errors, []);
  if (process.env.PLAYWRIGHT_ARTIFACT_DIR) {
    await mkdir(process.env.PLAYWRIGHT_ARTIFACT_DIR, { recursive: true });
    await page.screenshot({ path: `${process.env.PLAYWRIGHT_ARTIFACT_DIR}/subscriptions-desktop.png`, fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: `${process.env.PLAYWRIGHT_ARTIFACT_DIR}/subscriptions-mobile.png`, fullPage: true });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
  }
});

test("a redemption finishing after a directory switch cannot replace the new worker profile", { timeout: 60_000 }, async (t) => {
  let completeRedemption;
  const pendingRedemption = new Promise((resolve) => { completeRedemption = resolve; });
  t.after(() => completeRedemption());
  const { page, errors } = await fixture(t, async (route) => {
    const request = route.request();
    if (request.method() === "POST") {
      await pendingRedemption;
      return route.fulfill({ json: { outcome: "reset", profile: profile("team-plus", "Old worker", 1) } });
    }
    const otherWorker = new URL(request.url()).searchParams.get("directory_path") === "operations";
    return route.fulfill({ json: {
      worker_url: otherWorker ? "http://other-worker.test" : "http://worker.test",
      profiles: [profile("team-plus", otherWorker ? "New worker" : "Old worker", otherWorker ? 5 : 2)],
    } });
  });
  const old = row(page, "Old worker");
  await old.waitFor();
  await page.getByRole("button", { name: "Unlock operations" }).click();
  await old.locator("summary").first().click();
  await confirmClick(page, old.getByRole("button", { name: "Redeem one reset" }));
  await page.getByLabel("Directory").selectOption("operations");
  const current = row(page, "New worker");
  await current.waitFor();
  completeRedemption();
  await page.getByRole("button", { name: "Refresh", exact: true }).waitFor({ state: "visible" });
  await withTimeout(page.waitForFunction(() => !Array.from(document.querySelectorAll('button')).find(button => button.textContent === 'Refresh')?.disabled), 10_000, "Redemption did not finish");
  assert.match(await current.locator("summary").first().innerText(), /5\s+Banked resets/);
  assert.equal(await row(page, "Old worker").count(), 0);
  assert.equal(await page.getByRole("status").count(), 0);
  assert.deepEqual(errors, []);
});
