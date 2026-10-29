import test from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { fixture } from "./fixture.mjs";

test("speech backend selection preserves model, voice and native speed limits", async () => {
  const f = await fixture();
  let browser;
  try {
    browser = await chromium.launch({ headless: true, args: ["--no-sandbox"] });
    const page = await browser.newPage();
    await page.request.post(f.base + "/api/auth/login");
    await page.goto(f.base + "/addons/browser-sessions-addon/?directory=root/demo", {waitUntil:"domcontentloaded"});
    await page.getByRole("button", {name:"Configure",exact:true}).click();
    await page.locator("#settings-panel").waitFor({state:"visible"});
    const backend = page.getByLabel("Speech API", {exact:true});
    assert.equal(await backend.inputValue(), "speech_api");
    assert.equal(await page.getByLabel("Speech speed", {exact:true}).getAttribute("max"), "4");
    await backend.selectOption("realtime");
    assert.equal(await page.getByLabel("Speech speed", {exact:true}).getAttribute("max"), "1.5");
    await page.getByLabel("Speech model", {exact:true}).fill("gpt-realtime-2.1-mini");
    await page.getByLabel("Voice name", {exact:true}).fill("cedar");
    await page.getByLabel("Speech speed", {exact:true}).fill("1.25");
    const saved = page.waitForResponse(response => response.url().includes("/settings?") && response.request().method() === "PUT");
    await page.getByRole("button", {name:"Save settings",exact:true}).click();
    const response = await saved;
    assert.equal(response.status(), 200);
    const voice = (await response.json()).settings.voice;
    assert.deepEqual([voice.speech_backend, voice.speech_model, voice.voice, voice.speed],
      ["realtime", "gpt-realtime-2.1-mini", "cedar", 1.25]);
    await page.reload();
    await page.getByRole("button", {name:"Configure",exact:true}).click();
    await page.locator("#settings-panel").waitFor({state:"visible"});
    assert.equal(await backend.inputValue(), "realtime");
    assert.equal(await page.getByLabel("Speech speed", {exact:true}).inputValue(), "1.25");
    await page.locator("#voice-heading").scrollIntoViewIfNeeded();
    await page.screenshot({path:"test-output/realtime-voice-settings.png",fullPage:true});
  } finally {
    await browser?.close();
    await f.stop();
  }
});
