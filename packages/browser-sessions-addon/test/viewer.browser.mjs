import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { chromium } from "playwright";

test("hosted viewer keeps text feedback usable when voice is disabled", async () => {
  const received = [], messages = [];
  const state = { id: "browser-test", title: "Station material preview", state: "paused", revision: 1,
    controller: null, control_generation: 1, current_url: "http://preview-app:4200/station",
    expires_at: Date.now()/1000+3600 };
  const root = new URL("../evander_browser_sessions_addon/ui_static/", import.meta.url);
  const server = createServer(async (request, response) => {
    if (request.url.startsWith("/api/")) {
      response.setHeader("Content-Type", "application/json");
      if (request.url.endsWith("/voice/info")) {
        response.end(JSON.stringify({enabled:false,max_recording_seconds:45,max_audio_bytes:2000000,latest_message_seq:0})); return;
      }
      if (request.method === "POST") {
        let body = ""; for await (const chunk of request) body += chunk;
        received.push({ url: request.url, payload: JSON.parse(body) });
        if (request.url.endsWith("/messages")) messages.push({ seq: 1, actor: "user:operator", body: JSON.parse(body).body });
        response.end(JSON.stringify(state)); return;
      }
      response.end(JSON.stringify(request.url.includes("/messages") ? { messages: request.url.includes("after=0") ? messages : [] } : state)); return;
    }
    const file = request.url.endsWith(".js") ? "viewer.js" : request.url.endsWith(".css") ? "styles.css" : "index.html";
    response.setHeader("Content-Type", file.endsWith(".js") ? "text/javascript" : file.endsWith(".css") ? "text/css" : "text/html");
    response.end(await readFile(new URL(file, root)));
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  const browser = await chromium.launch({ headless: true, args: ["--no-sandbox"] });
  try {
    const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
    const errors = []; page.on("pageerror", e => errors.push(e.message));
    await page.goto("http://127.0.0.1:" + server.address().port + "/addons/browser-sessions-addon/view/browser-test",
                    { waitUntil: "domcontentloaded" });
    await page.getByRole("heading", { name: "Station material preview" }).waitFor();
    assert.equal(await page.getByRole("button", {name:"Enable microphone",exact:true}).isDisabled(),true);
    assert.equal(await page.getByRole("checkbox", {name:"Speak new replies"}).isDisabled(),true);
    const composer = await page.getByRole("button", {name:"Send",exact:true}).boundingBox();
    assert.ok(composer.y + composer.height <= 1000, "The chat Send button must remain visible in the viewport");
    await page.getByRole("textbox", { name: "Message Truman" }).fill("Move the button to the right.");
    await page.getByRole("button", { name: "Send", exact: true }).click();
    await page.getByRole("log").getByText("Move the button to the right.").waitFor();
    assert.equal(received[0].payload.body, "Move the button to the right.");
    assert.ok(received[0].url.endsWith("/messages"));
    assert.deepEqual(errors, []);
    await page.screenshot({ path: "test-output/viewer.png", fullPage: true });
  } finally {
    await browser.close(); server.closeAllConnections(); await new Promise(resolve => server.close(resolve));
  }
});
