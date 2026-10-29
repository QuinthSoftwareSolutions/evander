import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { chromium } from "playwright";
import { BrowserEngine } from "../dist/engine.mjs";

async function keyboardFixture(run) {
  const server = createServer((_request, response) => {
    response.setHeader("Content-Type", "text/html");
    response.end(`<!doctype html><title>Keyboard input fixture</title>
      <form><label>Material<input aria-label="Material"></label><button>Submit</button></form>
      <label>Notes<textarea aria-label="Notes"></textarea></label>
      <script>
        window.scanned = ""; window.submissions = []; window.formValue = null;
        window.addEventListener("keypress", event => {
          if (event.key === "Enter") {
            window.submissions.push(window.scanned); window.scanned = "";
          } else { window.scanned += event.key; }
        });
        document.querySelector("form").onsubmit = event => {
          event.preventDefault(); window.formValue = document.querySelector("input").value;
        };
      </script>`);
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  let browser;
  try {
    browser = await chromium.launch({ headless: true, args: ["--no-sandbox"] });
    const page = await browser.newPage();
    const url = "http://127.0.0.1:" + server.address().port;
    await page.goto(url, { waitUntil: "domcontentloaded" });
    const cdp = await page.context().newCDPSession(page);
    const engine = new BrowserEngine((method, params) => cdp.send(method, params), {
      id: "keyboard-fixture", controller: "agent:fixture", control_generation: 1,
      allowed_origins: [url], expires_at: Date.now() / 1000 + 60,
    }, () => {});
    const observation = await engine.execute({ action: "observe", generation: 1 }, "agent:fixture");
    const execute = (action, values = {}) => engine.execute({
      id: crypto.randomUUID(), generation: 1, action,
      observation_id: observation.observation_id, ...values,
    }, "agent:fixture");
    await run({ page, observation, execute });
  } finally {
    await browser?.close();
    await new Promise(resolve => server.close(resolve));
  }
}

test("real Chromium: typing and Enter reach a page-level scanner listener", async () => {
  await keyboardFixture(async ({ page, execute }) => {
    await execute("type", { text: "SAMPLE-42" });
    await execute("key", { text: "Enter" });
    assert.deepEqual(await page.evaluate(() => window.submissions), ["SAMPLE-42"]);
  });
});

test("real Chromium: character typing, Space and Enter preserve normal form input", async () => {
  await keyboardFixture(async ({ page, observation, execute }) => {
    const material = observation.elements.find(item => item.name === "Material");
    await execute("fill", { ref: material.ref, text: "Steel" });
    await execute("key", { text: "Space" });
    await execute("type", { text: "Åβ😊" });
    assert.equal(await page.getByRole("textbox", { name: "Material", exact: true }).inputValue(), "Steel Åβ😊");
    await execute("key", { text: "Enter" });
    assert.equal(await page.evaluate(() => window.formValue), "Steel Åβ😊");
    const notes = observation.elements.find(item => item.name === "Notes");
    await execute("fill", { ref: notes.ref, text: "First" });
    await execute("type", { text: "\r\nSecond\nThird\rFourth" });
    assert.equal(await page.getByRole("textbox", { name: "Notes", exact: true }).inputValue(), "First\nSecond\nThird\nFourth");
  });
});
