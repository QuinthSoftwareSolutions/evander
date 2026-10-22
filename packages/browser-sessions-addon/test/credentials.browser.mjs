import test from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { fixture } from "./fixture.mjs";

test("browser credentials require owner unlock and save without exposing the secret", async () => {
  const f = await fixture();
  let browser;
  try {
    browser = await chromium.launch({ headless: true, args: ["--no-sandbox"] });
    const page = await browser.newPage();
    await page.request.post(f.base + "/api/auth/login");
    await page.goto(f.base + "/addons/browser-sessions-addon/?directory=root/demo");
    await page.getByRole("button", { name: "Configure", exact: true }).click();
    await page.locator("#settings-panel").waitFor({ state: "visible" });
    assert.equal(await page.getByLabel("Private relay URL", { exact: true }).isDisabled(), true);
    assert.equal(await page.locator("#relay-credential input").isDisabled(), true);
    assert.equal((await page.request.post(f.base + "/test/unlock")).status(), 200);
    await page.reload();
    await page.getByRole("button", { name: "Configure", exact: true }).click();
    await page.locator("#settings-panel").waitFor({ state: "visible" });
    assert.equal(await page.getByLabel("Private relay URL", { exact: true }).isEnabled(), true);
    const secret = "synthetic-owner-managed-relay-token";
    await page.locator("#relay-credential input").fill(secret);
    const saved = page.waitForResponse(response => response.url().includes("/credentials?") && response.request().method() === "POST");
    await page.locator("#relay-credential").getByRole("button", { name: "Store credential", exact: true }).click();
    const response = await saved;
    assert.equal(response.status(), 200);
    assert.equal((await response.text()).includes(secret), false);
    await page.getByRole("status").getByText("Credential stored", { exact: true }).waitFor();
    assert.equal(await page.locator("#relay-credential input").inputValue(), "");
    await page.screenshot({ path: "test-output/managed-credentials.png", fullPage: true });
  } finally {
    await browser?.close();
    await f.stop();
  }
});
