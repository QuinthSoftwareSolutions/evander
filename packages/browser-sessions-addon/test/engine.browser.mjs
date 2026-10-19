import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { chromium } from "playwright";
import { BrowserEngine } from "../dist/engine.mjs";

test("real Chromium: observe, fill, click, control fences and live style updates", async () => {
  const server = createServer((request, response) => {
    response.setHeader("Content-Type", "text/html");
    response.end('<!doctype html><title>Preview fixture</title><style>button{margin-left:12px}</style><label>Material<input aria-label="Material"></label><button>Add material</button><p id="result"></p><script>document.querySelector("button").onclick=()=>document.querySelector("#result").textContent=document.querySelector("input").value</script>');
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  const url = "http://127.0.0.1:" + server.address().port;
  const browser = await chromium.launch({ headless: true, args: ["--no-sandbox"] });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await page.goto(url, { waitUntil: "domcontentloaded" });
    const cdp = await page.context().newCDPSession(page), pointers = [];
    let state = { id: "browser-test", controller: "agent:a", control_generation: 1,
      allowed_origins: [url], expires_at: Date.now()/1000+60 };
    const engine = new BrowserEngine((method, params) => cdp.send(method, params), state, event => pointers.push(event));
    const command = (action, values={}) => ({ id: crypto.randomUUID(), generation: state.control_generation, action, ...values });
    let observation = await engine.execute(command("observe"), "agent:a");
    assert.ok(observation.image.length > 100);
    const input = observation.elements.find(item => item.role === "textbox");
    const button = observation.elements.find(item => item.role === "button");
    await engine.execute(command("fill", { observation_id: observation.observation_id, ref: input.ref, text: "Alloy A" }), "agent:a");
    await engine.execute(command("click", { observation_id: observation.observation_id, ref: button.ref }), "agent:a");
    assert.equal(await page.locator("#result").textContent(), "Alloy A");
    assert.equal(pointers.at(-1).action, "click");
    state = { ...state, controller: "user:a", control_generation: 2 }; engine.update(state);
    await assert.rejects(engine.execute(command("click", { observation_id: observation.observation_id, ref: button.ref }), "agent:a"), /control_required/);
    await assert.rejects(engine.execute({ ...command("observe"), generation: 1 }, "agent:a"), /stale_control/);
    // The browser and application session stay alive while a developer's style change arrives.
    const before = (await page.getByRole("button").boundingBox()).x;
    await page.addStyleTag({ content: "button{margin-left:240px}" });
    const after = (await page.getByRole("button").boundingBox()).x;
    assert.ok(after > before + 200);
    observation = await engine.execute(command("observe"), "agent:a");
    assert.equal(observation.url, url + "/");
    await assert.rejects(engine.execute(command("navigate", { url: "https://unapproved.test" }), "user:a"), /origin_denied/);
    engine.navigate();
    await assert.rejects(engine.execute(command("click", { observation_id: observation.observation_id, ref: button.ref }), "user:a"), /stale_observation/);
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
});
