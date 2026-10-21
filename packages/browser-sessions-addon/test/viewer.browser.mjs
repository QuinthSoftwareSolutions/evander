import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { chromium } from "playwright";

test("hosted viewer keeps text feedback usable when voice is disabled", async () => {
  const received = [], messages = [];
  let deliveryStatus = "queued";
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
        if (request.url.endsWith("/messages")) messages.push({ id: "message-1", seq: 1, delivery_status: "queued", actor: "user:operator", body: JSON.parse(body).body });
        response.end(JSON.stringify(state)); return;
      }
      response.end(JSON.stringify(request.url.includes("/messages") ? { messages: request.url.includes("after=0") ? messages : [], message_updates: [{id:"message-1",delivery_status:deliveryStatus}] } : state)); return;
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
    const workspace = await page.locator("#workspace").boundingBox();
    assert.equal(workspace.y, 0);
    assert.equal(workspace.height, 1000);
    const light = await page.locator("body").evaluate(element => getComputedStyle(element).color);
    await page.getByRole("button", {name:"Switch to dark mode",exact:true}).click();
    assert.notEqual(await page.locator("body").evaluate(element => getComputedStyle(element).color), light);
    await page.reload({waitUntil:"domcontentloaded"});
    await page.getByRole("button", {name:"Switch to light mode",exact:true}).waitFor();
    await page.getByRole("button", {name:"Switch to light mode",exact:true}).click();
    assert.equal(await page.getByRole("button", {name:"Enable microphone",exact:true}).isDisabled(),true);
    assert.equal(await page.getByRole("checkbox", {name:"Speak replies (AI voice)"}).isDisabled(),true);
    const composer = await page.getByRole("button", {name:"Send",exact:true}).boundingBox();
    assert.ok(composer.y + composer.height <= 1000, "The chat Send button must remain visible in the viewport");
    const body = page.getByRole("textbox", { name: "Message Truman" });
    await body.fill("   "); await body.press("Enter");
    assert.equal(received.length, 0);
    await body.fill("Draft"); await body.press("Shift+Enter");
    assert.equal(await body.inputValue(), "Draft\n");
    assert.equal(received.length, 0);
    await body.fill("Move the button to the right."); await body.press("Enter");
    await page.getByRole("log").getByText("Move the button to the right.").waitFor();
    assert.equal(received[0].payload.body, "Move the button to the right.");
    assert.ok(received[0].url.endsWith("/messages"));
    await page.locator('[data-delivery="queued"]').waitFor();
    deliveryStatus = "steered";
    await page.locator('[data-delivery="steered"]').waitFor();
    assert.equal(await page.locator('[data-message-id="message-1"]').count(), 1);
    deliveryStatus = "delivery_unknown";
    await page.getByRole("button", {name:"Retry message",exact:true}).waitFor();
    const retried = page.waitForResponse(response => response.url().endsWith("/retry-message"));
    await page.getByRole("button", {name:"Retry message",exact:true}).click();
    await retried;
    assert.equal(received[1].payload.message_id, "message-1");
    assert.equal(typeof received[1].payload.request_id, "string");
    deliveryStatus = "steered";
    await page.locator('[data-delivery="steered"]').waitFor();
    assert.deepEqual(errors, []);
    await page.screenshot({ path: "test-output/viewer.png", fullPage: true });
  } finally {
    await browser.close(); server.closeAllConnections(); await new Promise(resolve => server.close(resolve));
  }
});


async function deadline(promise, label) {
  let timer;
  try {
    return await Promise.race([promise, new Promise((_,reject) => {
      timer = setTimeout(() => reject(new Error(label)), 7000);
    })]);
  } finally { clearTimeout(timer); }
}

for (const kind of ["hosted", "voice"]) {
  test(`${kind} chat does not duplicate messages when polling overlaps Send`, async () => {
    const state = {id:"browser-race",title:"Shared application",state:"paused",revision:1,
      controller:null,control_generation:1,current_url:"http://preview-app:4200/",expires_at:Date.now()/1000+3600};
    const messages = [], cursors = [];
    let holdNext = false, writes = 0, activeReads = 0, maxActiveReads = 0, completedReads = 0;
    let signalHeld, releaseHeld, signalCatchup;
    const held = new Promise(resolve => signalHeld = resolve);
    const released = new Promise(resolve => releaseHeld = resolve);
    const catchup = new Promise(resolve => signalCatchup = resolve);
    const root = new URL("../evander_browser_sessions_addon/ui_static/", import.meta.url);
    const server = createServer(async (request, response) => {
      const url = new URL(request.url, "http://fixture");
      const api = url.pathname.startsWith("/api/") || url.pathname.startsWith("/_browser/guest/voice/");
      if (api) {
        response.setHeader("Content-Type", "application/json");
        if (url.pathname.endsWith("/info")) {
          response.end(JSON.stringify({enabled:false,max_recording_seconds:45,max_audio_bytes:2000000})); return;
        }
        if (request.method === "POST") {
          let body = ""; for await (const chunk of request) body += chunk;
          writes += 1;
          messages.push({id:"user-message",seq:1,actor:"user:operator",body:JSON.parse(body).body,delivery_status:"queued"});
          response.end(JSON.stringify(messages[0])); return;
        }
        const isMessages = url.pathname.endsWith(kind === "hosted" ? "/messages" : "/state");
        if (isMessages) {
          const cursor = Number(url.searchParams.get("after")); cursors.push(cursor);
          activeReads += 1; maxActiveReads = Math.max(maxActiveReads, activeReads);
          response.once("finish", () => {
            activeReads -= 1; completedReads += 1;
            if (completedReads >= 3) signalCatchup();
          });
          if (holdNext) { holdNext = false; signalHeld(); await released; }
          response.end(JSON.stringify({session:state,messages:messages.filter(message => message.seq > cursor)})); return;
        }
        response.end(JSON.stringify(state)); return;
      }
      const file = url.pathname.endsWith(".js") ? (kind === "hosted" ? "viewer.js" : "voice.js") :
        url.pathname.endsWith(".css") ? (kind === "hosted" ? "styles.css" : "voice.css") :
        kind === "hosted" ? "index.html" : "voice.html";
      response.setHeader("Content-Type", file.endsWith(".js") ? "text/javascript" : file.endsWith(".css") ? "text/css" : "text/html");
      response.end(await readFile(new URL(file, root)));
    });
    await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
    let browser;
    try {
      browser = await chromium.launch({headless:true,args:["--no-sandbox"]});
      const page = await browser.newPage(); page.setDefaultTimeout(7000);
      const firstRead = page.waitForResponse(response => response.url().includes("after=0"));
      const path = kind === "hosted" ? "/addons/browser-sessions-addon/view/browser-race" : "/_browser/guest/voice#token=fixture";
      await page.goto("http://127.0.0.1:" + server.address().port + path, {waitUntil:"domcontentloaded"});
      await firstRead;
      holdNext = true;
      await deadline(held, "Poll did not start");
      const message = page.getByRole("textbox",{name:"Message Truman"});
      await message.fill("Continue in this preview.");
      await page.getByRole("button",{name:"Send",exact:true}).click();
      await page.waitForFunction(id => document.getElementById(id).value === "", kind === "hosted" ? "chat-body" : "body");
      releaseHeld();
      await deadline(catchup, "Refresh did not catch up");
      await page.getByRole("log").getByText(/Continue in this preview\./).first().waitFor();
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      assert.equal(await page.getByRole("log").getByText(/Continue in this preview\./).count(),1);
      assert.equal(writes,1,"Send must create only one message");
      assert.equal(maxActiveReads,1,"Poll and post-send refresh must not fetch the same cursor concurrently");
      assert.equal(cursors[2],1,"The catch-up read must advance beyond the displayed message");
    } finally {
      releaseHeld(); await browser?.close();
      server.closeAllConnections(); await new Promise(resolve => server.close(resolve));
    }
  });
}
