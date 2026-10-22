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

async function fixture(t, routeHandler, options = {}) {
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
        const [subscriptionsVisible, showSubscriptions] = React.useState(true);
        return React.createElement(React.Fragment, null,
          React.createElement('button', { onClick: () => unlock(true) }, 'Unlock operations'),
          React.createElement('button', { onClick: () => showSubscriptions(!subscriptionsVisible) },
            subscriptionsVisible ? 'Leave subscriptions' : 'Open subscriptions'),
          React.createElement('select', { 'aria-label': 'Directory', value: selectedDirectoryPath,
            onChange: (event) => selectDirectory(event.target.value) },
            React.createElement('option', { value: 'engineering' }, 'Engineering'),
            React.createElement('option', { value: 'operations' }, 'Operations')),
          React.createElement(AdminUiContext.Provider, { value: { privilegedUnlocked, selectedDirectoryPath } },
            subscriptionsVisible ? React.createElement(SubscriptionsPage) : null));
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
  if (options.initScript) await page.addInitScript(options.initScript);
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

test("signed-out profiles start login directly and reveal the pending login code", { timeout: 60_000 }, async (t) => {
  const selected = profile("connect", "Connect account", 0, { signed_in: false, account_id: null, email: null });
  const requests = [];
  const { page, errors } = await fixture(t, async (route) => {
    const request = route.request();
    const endpoint = new URL(request.url());
    assert.equal(endpoint.searchParams.get("directory_path"), "engineering");
    if (request.method() === "POST") {
      requests.push(endpoint.pathname);
      return route.fulfill({ json: { login_id: "login-1", status: "pending", user_code: "CODE-123", verification_url: null } });
    }
    return route.fulfill({ json: { profiles: [selected], worker_url: "http://worker.example.test" } });
  });
  const selectedRow = row(page, "Connect account");
  const login = selectedRow.getByRole("button", { name: "Login", exact: true });
  await login.waitFor();
  assert.equal(await selectedRow.getAttribute("open"), null);
  assert.equal(await login.isDisabled(), true);
  await page.getByRole("button", { name: "Unlock operations", exact: true }).click();
  await login.click();
  await selectedRow.getByText("Login: pending. Code CODE-123.", { exact: true }).waitFor();
  assert.deepEqual(requests, ["/api/admin/codex-subscriptions/connect/device-login"]);
  assert.notEqual(await selectedRow.getAttribute("open"), null);
  assert.deepEqual(errors, []);
});

async function confirmClick(page, button, accept = true) {
  await Promise.all([
    page.waitForEvent("dialog").then((dialog) => accept ? dialog.accept() : dialog.dismiss()),
    button.click(),
  ]);
}

test("credit balances distinguish zero, unlimited, and unavailable and refresh with usage", { timeout: 60_000 }, async (t) => {
  const selected = profile("balance", "Credit balance", 3);
  selected.rate_limits.default.credits = { has_credits: true, unlimited: false, balance: "99" };
  selected.rate_limits.by_limit_id = {
    codex: { credits: { has_credits: true, unlimited: false, balance: "1250.50" } },
  };
  const legacy = profile("legacy", "Legacy balance", 0);
  legacy.rate_limits.default.credits = { has_credits: true, unlimited: false, balance: "12.75" };
  const empty = profile("empty", "No credits", 1);
  empty.rate_limits.default.credits = { has_credits: false, unlimited: false, balance: null };
  const unlimited = profile("unlimited", "Unlimited credits", 2);
  unlimited.rate_limits.default.credits = { has_credits: true, unlimited: true, balance: null };
  const missing = profile("missing", "Unknown balance", null, { rate_limits: null });
  const olderWorker = profile("older", "Older worker", 4);
  const otherBucket = profile("other", "Other bucket", 0);
  otherBucket.rate_limits.default = {
    limit_id: "other", credits: { has_credits: true, unlimited: false, balance: "500" },
  };
  otherBucket.rate_limits.by_limit_id = {
    other: { credits: { has_credits: true, unlimited: false, balance: "500" } },
  };
  const cases = [
    [selected, "1,250.5"], [legacy, "12.75"], [empty, "0"], [unlimited, "Unlimited"],
    [missing, "Unavailable"], [olderWorker, "Unavailable"], [otherBucket, "Unavailable"],
  ];
  const { page, errors } = await fixture(t, async (route) => {
    assert.equal(route.request().method(), "GET");
    return route.fulfill({ json: { worker_url: "http://worker.test", profiles: cases.map(([entry]) => entry) } });
  });
  for (const [entry, expected] of cases) {
    const creditCell = row(page, entry.display_name).locator(".subscription-credits");
    await creditCell.waitFor();
    assert.equal((await creditCell.innerText()).replace(/\s+/g, " "), `${expected} Credits`);
  }
  assert.match(await row(page, "Credit balance").innerText(), /20%\s+7-day remaining/);
  assert.match(await row(page, "Credit balance").innerText(), /3\s+Banked resets/);

  for (const width of [1600, 1100, 960, 390]) {
    await page.setViewportSize({ width, height: 900 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
    assert.equal(await row(page, "Credit balance").locator(".subscription-credits").isVisible(), true);
    if (process.env.PLAYWRIGHT_ARTIFACT_DIR && [1600, 390].includes(width)) {
      await mkdir(process.env.PLAYWRIGHT_ARTIFACT_DIR, { recursive: true });
      await page.screenshot({ path: `${process.env.PLAYWRIGHT_ARTIFACT_DIR}/subscription-credits-${width}.png`, fullPage: true });
    }
  }
  // A narrow content area needs the compact layout even in a wide browser.
  await page.setViewportSize({ width: 1600, height: 900 });
  await page.locator(".subscription-profiles").evaluate((element) => { element.style.maxWidth = "850px"; });
  assert.equal(await page.locator(".subscription-columns").isVisible(), false);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);

  const creditCell = row(page, "Credit balance").locator(".subscription-credits");
  for (const balance of [null, "", "  ", "unknown", "-1", "Infinity", "0x10", 10, { token: "secret" }]) {
    selected.rate_limits.by_limit_id.codex.credits.balance = balance;
    // Neither view has a usable balance, including when credits are known to exist.
    selected.rate_limits.default.credits = null;
    await page.getByRole("button", { name: "Refresh", exact: true }).click();
    await creditCell.filter({ hasText: /^Unavailable\s*Credits$/ }).waitFor();
  }
  for (const [balance, expected] of [["0", "0"], ["0.0001", "0.0001"], [" 20.25 ", "20.25"]]) {
    selected.rate_limits.by_limit_id.codex.credits.balance = balance;
    await page.getByRole("button", { name: "Refresh", exact: true }).click();
    await creditCell.filter({ hasText: new RegExp(`^${expected.replace(".", "\\.")}\\s*Credits$`) }).waitFor();
  }
  assert.deepEqual(errors, []);
});

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
  for (const { body } of requests) {
    assert.equal(body.expected_worker_url, "http://worker.test");
    assert.equal(body.expected_account_id, "account-team-plus");
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

test("unresolved redemption survives a tab remount and reload until a definitive response", { timeout: 60_000 }, async (t) => {
  let selected = profile("team-plus", "Team Plus", 3);
  const keys = new Set();
  const requests = [];
  let consumed = 0;
  const { page, errors } = await fixture(t, async (route) => {
    const request = route.request();
    if (request.method() === "GET") {
      return route.fulfill({ json: { worker_url: "http://worker.test", profiles: [selected] } });
    }
    const body = request.postDataJSON();
    requests.push(body);
    let outcome = "alreadyRedeemed";
    if (!keys.has(body.idempotency_key)) {
      keys.add(body.idempotency_key);
      consumed += 1;
      selected = profile("team-plus", "Team Plus", 3 - consumed);
      outcome = "reset";
    }
    // Usage becomes resettable again before the operator returns. A new key
    // would now consume another credit instead of replaying the first attempt.
    selected.rate_limits.default.secondary.used_percent = 80;
    if (requests.length <= 2) {
      return route.fulfill({ status: 502, json: { error: "Response lost after redemption" } });
    }
    return route.fulfill({ json: { outcome, profile: selected } });
  });
  await row(page, "Team Plus").waitFor();
  await page.getByRole("button", { name: "Unlock operations" }).click();
  await row(page, "Team Plus").locator("summary").first().click();
  await confirmClick(page, row(page, "Team Plus").getByRole("button", { name: "Redeem one reset" }));
  await page.getByText(/Response lost after redemption/).waitFor();
  await page.getByRole("button", { name: "Leave subscriptions" }).click();
  assert.equal(await page.getByRole("heading", { name: "Subscriptions" }).count(), 0);
  await page.getByRole("button", { name: "Open subscriptions" }).click();
  await row(page, "Team Plus").waitFor();
  await row(page, "Team Plus").locator("summary").first().click();
  await row(page, "Team Plus").getByRole("button", { name: "Retry reset redemption" }).click();
  await page.getByText(/Response lost after redemption/).waitFor();
  await page.reload();
  await row(page, "Team Plus").waitFor();
  await page.getByRole("button", { name: "Unlock operations" }).click();
  await row(page, "Team Plus").locator("summary").first().click();
  await row(page, "Team Plus").getByRole("button", { name: "Retry reset redemption" }).click();
  await page.getByRole("status").filter({ hasText: "already redeemed" }).waitFor();
  assert.equal(consumed, 1);
  assert.equal(requests.length, 3);
  assert.equal(new Set(requests.map((request) => request.idempotency_key)).size, 1);
  for (const request of requests) {
    assert.equal(request.expected_worker_url, "http://worker.test");
    assert.equal(request.expected_account_id, "account-team-plus");
  }
  // Only a definitive outcome removes the pending attempt.
  await page.reload();
  await row(page, "Team Plus").waitFor();
  await row(page, "Team Plus").locator("summary").first().click();
  assert.equal(await row(page, "Team Plus").getByRole("button", { name: "Redeem one reset", exact: true }).count(), 1);
  assert.equal(await row(page, "Team Plus").getByRole("button", { name: "Retry reset redemption" }).count(), 0);
  assert.deepEqual(errors, []);
});

test("a retry retains its original destination when the directory worker changes", { timeout: 60_000 }, async (t) => {
  let configuredWorker = "http://worker.test";
  let originalConsumed = 0;
  let replacementConsumed = 0;
  const requests = [];
  const { page, errors } = await fixture(t, async (route) => {
    const request = route.request();
    if (request.method() === "GET") {
      return route.fulfill({ json: {
        worker_url: configuredWorker, profiles: [profile("team-plus", "Team Plus", 3)],
      } });
    }
    const body = request.postDataJSON();
    requests.push(body);
    if (body.expected_worker_url !== configuredWorker) {
      return route.fulfill({ status: 409, json: { error: "Subscription worker changed" } });
    }
    if (configuredWorker === "http://worker.test") originalConsumed += 1;
    else replacementConsumed += 1;
    configuredWorker = "http://replacement-worker.test";
    return route.fulfill({ status: 502, json: { error: "Response lost after redemption" } });
  });
  const selected = row(page, "Team Plus");
  await selected.waitFor();
  await page.getByRole("button", { name: "Unlock operations" }).click();
  await selected.locator("summary").first().click();
  await confirmClick(page, selected.getByRole("button", { name: "Redeem one reset" }));
  await page.getByText(/Response lost after redemption/).waitFor();
  await selected.getByRole("button", { name: "Retry reset redemption" }).click();
  await page.getByText(/Subscription worker changed/).waitFor();
  assert.equal(originalConsumed, 1);
  assert.equal(replacementConsumed, 0);
  assert.deepEqual(requests[0], requests[1]);
  assert.equal(await selected.getByRole("button", { name: "Retry reset redemption" }).count(), 1);
  assert.deepEqual(errors, []);
});

test("storage failure prevents a redemption from being sent", { timeout: 60_000 }, async (t) => {
  let redemptions = 0;
  const { page, errors } = await fixture(t, (route) => {
    if (route.request().method() !== "GET") redemptions += 1;
    return route.fulfill({ json: {
      worker_url: "http://worker.test", profiles: [profile("team-plus", "Team Plus", 3)],
    } });
  }, { initScript: () => {
    Storage.prototype.setItem = () => { throw new Error("Browser storage is unavailable"); };
  } });
  const selected = row(page, "Team Plus");
  await selected.waitFor();
  await page.getByRole("button", { name: "Unlock operations" }).click();
  await selected.locator("summary").first().click();
  await confirmClick(page, selected.getByRole("button", { name: "Redeem one reset" }));
  await page.getByText(/Reset redemption was not submitted/).waitFor();
  assert.equal(redemptions, 0);
  assert.deepEqual(errors, []);
});
