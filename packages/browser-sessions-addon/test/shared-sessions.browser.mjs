import test from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, writeFile, rm, cp, readFile, mkdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { createRequire } from "node:module";
import { once } from "node:events";
import { createHash } from "node:crypto";
import { chromium } from "playwright";
const require = createRequire(import.meta.url);

import { fixture, until } from "./fixture.mjs";
const command = (state, action, extra={}) => ({id:crypto.randomUUID(),generation:state.control_generation,action,...extra});

test("hosted controller shares its authenticated browser, streams frames and reflects a source change", async () => {
  const f = await fixture(); let controller, browser;
  try {
    const config = await f.api("/test/setup", {});
    await writeFile(join(f.folder,"connection.json"), JSON.stringify(config));
    await writeFile(join(f.folder,"storage.json"), JSON.stringify({cookies:[{name:"demo_session",value:"fixture",domain:"127.0.0.1",path:"/",expires:-1,httpOnly:true,secure:false,sameSite:"Lax"}],origins:[]}));
    controller = spawn(process.execPath, ["evander_browser_sessions_addon/controller/hosted.mjs"], {
      env:{...process.env,EVANDER_BROWSER_CONNECTION:join(f.folder,"connection.json"),
        EVANDER_PREVIEW_STATE:join(f.folder,"storage.json"),EVANDER_PLAYWRIGHT_MODULE:require.resolve("playwright")},
      stdio:["ignore","pipe","pipe"]});
    let controllerError=""; controller.stderr.on("data", d=>controllerError+=d);
    const path = "/test/"+config.session.id;
    let state = await until(async()=>{const s=await f.api(path+"/state");return s.state==="ready"&&s;}, "hosted browser readiness");
    const observe = ()=>f.api(path+"/command", command(state,"observe"));
    let observed = await observe();
    assert.ok(observed.elements.some(e=>e.role==="heading"&&e.name==="Station materials"));
    assert.ok(!observed.elements.some(e=>e.name==="Application login required"));
    const button=observed.elements.find(e=>e.role==="button");
    const pointers = await f.api(path+"/command", command(state,"highlight",{observation_id:observed.observation_id,ref:button.ref}));
    assert.equal(pointers.success,true);
    browser = await chromium.launch({headless:true,args:["--no-sandbox"]});
    const viewer = await browser.newPage({viewport:{width:1600,height:1000}});
    await viewer.request.post(f.base+"/api/auth/login");
    await viewer.goto(f.base+"/addons/browser-sessions-addon/view/"+config.session.id,{waitUntil:"domcontentloaded"});
    await viewer.locator("#empty").waitFor({state:"hidden"});
    await viewer.getByRole("button",{name:"Take control",exact:true}).click();
    await until(async()=>{const s=await f.api(path+"/state");return s.controller==="user:operator"&&s;}, "human control");
    state=await f.api(path+"/state");
    const denied = await fetch(f.base+path+"/command",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify(command(state,"click",{observation_id:observed.observation_id,ref:button.ref}))});
    assert.ok(denied.status>=400);
    await viewer.getByRole("button",{name:"Hand back to Truman"}).click();
    state=await until(async()=>{const s=await f.api(path+"/state");return s.controller==="agent:agent-a"&&s;}, "handback");
    observed=await observe();
    await f.api(path+"/command",command(state,"fill",{observation_id:observed.observation_id,
      ref:observed.elements.find(e=>e.role==="textbox").ref,text:"Steel"}));
    await f.api(path+"/command",command(state,"click",{observation_id:observed.observation_id,ref:button.ref}));
    const before=(await observe()).image;
    // This fixture models a dev server applying the changed stylesheet to the same page.
    await f.api(path+"/style",{left:280});
    await new Promise(resolve=>setTimeout(resolve,350));
    const after=(await observe()).image;
    assert.notEqual(after,before);
    await viewer.screenshot({path:"test-output/hosted-session.png",fullPage:true});
    assert.equal(controllerError,"");
  } finally {
    await browser?.close();
    if(controller&&controller.exitCode===null && controller.signalCode===null){
      controller.kill("SIGTERM");
      await Promise.race([once(controller,"exit"),new Promise(resolve=>setTimeout(resolve,3000))]);
      if(controller.exitCode===null && controller.signalCode===null){controller.kill("SIGKILL");await once(controller,"exit");}
    }
    await f.stop();
  }
});

test("unpacked Chrome extension shares one existing tab, requires local grant, and stops input immediately", async () => {
  const f=await fixture(); let context, second;
  try {
    const extension=join(f.folder,"extension");await cp("dist/extension",extension,{recursive:true});
    // Automated browser startup pregrants the same optional loopback host permission
    // that the shipped panel requests in response to the user's Share click.
    const manifest=JSON.parse(await readFile(join(extension,"manifest.json"),"utf8"));
    manifest.host_permissions=["http://127.0.0.1/*"];
    await writeFile(join(extension,"manifest.json"),JSON.stringify(manifest));
    context=await chromium.launchPersistentContext(join(f.folder,"chrome"),{channel:"chromium",headless:true,
      args:["--no-sandbox","--disable-extensions-except="+extension,"--load-extension="+extension]});
    await context.addCookies([{name:"demo_session",value:"fixture",url:f.base}]);
    const page=await context.newPage();await page.goto(f.base+"/application",{waitUntil:"domcontentloaded"});
    const sw=context.serviceWorkers()[0]??await context.waitForEvent("serviceworker",{timeout:10000});
    const extensionId=new URL(sw.url()).host;
    const panel=await context.newPage();await panel.goto("chrome-extension://"+extensionId+"/panel.html",{waitUntil:"domcontentloaded"});
    await page.bringToFront();
    const setup=await f.api("/test/setup",{local:true}),path="/test/"+setup.session.id;
    const send=async(message)=>{
      return Promise.race([panel.evaluate(message=>chrome.runtime.sendMessage(message),message),
        new Promise((_,reject)=>setTimeout(()=>reject(new Error("Extension message timed out: "+message.kind)),8000))]);
    };
    const paired=await send({kind:"pair",invitation:setup.invitation_url});
    assert.equal(paired.ok,true,paired.error);
    let state=await until(async()=>{const s=await f.api(path+"/state");return s.state==="paused"&&s;}, "local observation");
    let observed=await f.api(path+"/command",command(state,"observe"));
    assert.ok(observed.elements.some(e=>e.name==="Station materials"));
    const button=observed.elements.find(e=>e.role==="button");
    assert.equal(state.controller,null);
    const grant=await send({kind:"control",action:"grant"});assert.equal(grant.ok,true,grant.error);
    state=await f.api(path+"/state");
    assert.equal(state.controller,"agent:agent-a");
    observed=await f.api(path+"/command",command(state,"observe"));
    await f.api(path+"/command",command(state,"fill",{observation_id:observed.observation_id,
      ref:observed.elements.find(e=>e.role==="textbox").ref,text:"Copper"}));
    await f.api(path+"/command",command(state,"type",{observation_id:observed.observation_id,text:" alloy"}));
    await f.api(path+"/command",command(state,"click",{observation_id:observed.observation_id,ref:button.ref}));
    assert.equal(await page.locator("#result").textContent(),"Copper alloy added");
     const paused=await send({kind:"control",action:"pause"});assert.equal(paused.ok,true,paused.error);
    const denied=await fetch(f.base+path+"/command",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify(command(state,"click",{observation_id:observed.observation_id,ref:button.ref}))});
    assert.ok(denied.status>=400);
    assert.equal((await f.api(path+"/state")).controller,null);
    const priorGeneration = (await f.api(path+"/state")).control_generation;
    second = await fixture();
    await page.goto(second.base+"/application",{waitUntil:"domcontentloaded"});
    await until(async()=>{const s=await f.api(path+"/state");return s.control_generation>priorGeneration&&s.controller===null&&s;}, "origin change pause");
    const approved = await send({kind:"origin"});
    assert.equal(approved.ok,true,approved.error);
    state = await f.api(path+"/state");
    assert.deepEqual(state.allowed_origins,[second.base]);
    assert.equal(new URL(state.current_url).origin,second.base);
    observed = await f.api(path+"/command",command(state,"observe"));
    assert.equal(new URL(observed.url).origin,second.base);
    await send({kind:"disconnect"});
    assert.equal((await f.api(path+"/state")).state,"closed");
  } finally {await context?.close();await second?.stop();await f.stop();}
});

for (const failure of [
  { method: "attach", reason: "Host access is restricted by policy." },
  { method: "Accessibility.enable", reason: "Synthetic accessibility initialization failure" },
]) {
  test("Chrome setup reports its original error and closes the session: " + failure.method, async () => {
    const f = await fixture();
    let context;
    try {
      const extension = join(f.folder, "extension");
      await cp("dist/extension", extension, { recursive: true });
      const manifest = JSON.parse(await readFile(join(extension, "manifest.json"), "utf8"));
      manifest.host_permissions = ["http://127.0.0.1/*"];
      await writeFile(join(extension, "manifest.json"), JSON.stringify(manifest));
      context = await chromium.launchPersistentContext(join(f.folder, "chrome"), {
        channel: "chromium", headless: true,
        args: ["--no-sandbox", "--disable-extensions-except=" + extension, "--load-extension=" + extension],
      });
      const application = await context.newPage();
      await application.goto(f.base + "/application", { waitUntil: "domcontentloaded" });
      const worker = context.serviceWorkers()[0] ?? await context.waitForEvent("serviceworker", { timeout: 10000 });
      await worker.evaluate(({ method, reason }) => {
        if (method === "attach") {
          chrome.debugger.attach = async () => { throw new Error(reason); };
        } else {
          const original = chrome.debugger.sendCommand;
          chrome.debugger.sendCommand = async (target, command, params) => {
            if (command === method) throw new Error(reason);
            return original.call(chrome.debugger, target, command, params);
          };
        }
      }, failure);
      const panel = await context.newPage();
      await panel.goto("chrome-extension://" + new URL(worker.url()).host + "/panel.html", { waitUntil: "domcontentloaded" });
      await application.bringToFront();
      const setup = await f.api("/test/setup", { local: true });
      const result = await panel.evaluate(invitation => chrome.runtime.sendMessage({ kind: "pair", invitation }), setup.invitation_url);
      assert.equal(result.ok, false);
      assert.ok(result.error.includes(failure.reason), "The original Chrome error must reach the extension panel");
      const state = await f.api("/test/" + setup.session.id + "/state");
      assert.equal(state.state, "closed", "Failed setup must release the sharing session");
      assert.equal(state.controller, null);
      const token = new URL(setup.invitation_url).hash.slice("#invite=".length);
      const replay = await fetch(f.base + "/_browser/guest/pair", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ invitation: token, application_url: f.base + "/application" }),
      });
      assert.equal(replay.status, 401, "Closing failed setup must revoke its invitation");
    } finally {
      await context?.close();
      await f.stop();
    }
  });
}

test("guided launch-profile setup saves through the production settings API", async () => {
  const f = await fixture();
  let browser;
  try {
    browser = await chromium.launch({ headless: true, args: ["--no-sandbox"] });
    const page = await browser.newPage();
    await page.request.post(f.base + "/api/auth/login");
    await page.goto(f.base + "/addons/browser-sessions-addon/?directory=root/demo",
      { waitUntil: "domcontentloaded" });
    await page.getByRole("button", { name: "Configure", exact: true }).click();
    await page.getByRole("button", { name: "Add profile", exact: true }).click();
    await page.getByLabel("Application image", { exact: true }).fill("seeded-preview:2");
    const saved = page.waitForResponse(response =>
      response.request().method() === "PUT" &&
      response.url().includes("/api/addons/browser-sessions-addon/settings"));
    await page.getByRole("button", { name: "Save settings", exact: true }).click();
    const response = await saved;
    assert.equal(response.status(), 200, await response.text());
    await page.getByRole("status").getByText("Saved", { exact: true }).waitFor();
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.getByRole("button", { name: "Configure", exact: true }).click();
    assert.equal(await page.getByLabel("Application image", { exact: true }).inputValue(),
      "seeded-preview:2");
  } finally {
    await browser?.close();
    await f.stop();
  }
});


async function trackVoice(context) {
  await context.addInitScript(() => {
    window.__voiceStreams = [];
    window.__voicePlayCount = 0;
    if (navigator.mediaDevices?.getUserMedia) {
      const capture = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
      navigator.mediaDevices.getUserMedia = async options => {
        const stream = await capture(options);
        window.__voiceStreams.push(stream);
        return stream;
      };
    }
    const play = HTMLMediaElement.prototype.play;
    HTMLMediaElement.prototype.play = function () {
      return play.call(this).then(() => { window.__voicePlayCount += 1; });
    };
  });
}

async function exerciseVoice(page, f, id) {
  const status = page.locator("[data-voice-status]");
  await page.getByRole("checkbox", {name:"Speak new replies"}).check();
  await page.getByRole("button", {name:"Enable microphone",exact:true}).click();
  const talk = page.getByRole("button", {name:"Hold Talk",exact:true});
  await talk.waitFor();
  await talk.focus();
  await page.keyboard.down("Space");
  await until(async()=>/Recording\./.test(await status.textContent()),"microphone recording");
  await page.waitForTimeout(400);
  await page.keyboard.up("Space");
  await page.getByRole("log").getByText(/Move the button to the left\./).waitFor();
  assert.ok(await page.evaluate(() => window.__voiceStreams.length >= 2 &&
    window.__voiceStreams.every(stream => stream.getTracks().every(track => track.readyState === "ended"))));
  const messages = (await f.api("/test/"+id+"/messages")).messages;
  const question = messages.find(message=>message.body==="Move the button to the left.");
  assert.ok(question);
  await f.api("/test/"+id+"/reply",{message_id:question.id,body:"The button has moved."});
  await until(async()=>await page.evaluate(()=>window.__voicePlayCount>0),"spoken reply playback");
  await page.getByRole("button",{name:"Stop audio",exact:true}).click();
  assert.equal(await page.getByRole("button",{name:"Stop audio",exact:true}).isDisabled(),true);
  assert.match(await status.textContent(),/Audio stopped/);
  const events = (await f.api("/test/voice/events")).events;
  assert.equal(events.filter(event=>event.action==="transcribe").length,1);
  assert.ok(events.find(event=>event.action==="transcribe").wav);
  assert.equal(events.find(event=>event.action==="speech").text,"The button has moved.");
  return question;
}

test("hosted voice captures a bounded phrase, captions the task and plays an interruptible reply", async () => {
  const f=await fixture(); let browser;
  try {
    browser=await chromium.launch({headless:true,args:["--no-sandbox",
      "--use-fake-device-for-media-stream","--use-fake-ui-for-media-stream"]});
    const context=await browser.newContext({viewport:{width:1600,height:1000}});
    await trackVoice(context);
    const config=await f.api("/test/setup",{voice_seconds:5});
    const page=await context.newPage();
    await page.request.post(f.base+"/api/auth/login");
    const earlier=await (await page.request.post(f.base+"/api/addons/browser-sessions-addon/sessions/"+config.session.id+"/messages",
      {data:{request_id:"history",body:"Earlier question"}})).json();
    await f.api("/test/"+config.session.id+"/reply",{message_id:earlier.id,body:"Earlier reply."});
    let releaseHistory;
    const historyReady=new Promise(resolve=>releaseHistory=resolve);
    await page.route("**/sessions/"+config.session.id+"/messages?after=0",async route=>{
      await historyReady;await route.continue();
    });
    await page.goto(f.base+"/addons/browser-sessions-addon/view/"+config.session.id,
      {waitUntil:"domcontentloaded"});
    await page.getByRole("checkbox",{name:"Speak new replies"}).check();
    releaseHistory();
    await page.getByRole("log").getByText("Earlier reply.",{exact:false}).waitFor();
    await exerciseVoice(page,f,config.session.id);
    assert.equal((await f.api("/test/"+config.session.id+"/state")).controller,"agent:agent-a");
    assert.ok(!(await page.content()).includes("fixture-speech-secret"));
    const talk=page.getByRole("button",{name:"Hold Talk",exact:true});
    await talk.focus(); await page.keyboard.down("Space");
    await until(async()=>/Recording\./.test(await page.locator("[data-voice-status]").textContent()),"second recording");
    const elsewhere=await context.newPage();
    await elsewhere.goto(f.base+"/_browser/guest/join",{waitUntil:"domcontentloaded"});
    await elsewhere.bringToFront(); await elsewhere.keyboard.up("Space");
    // Playwright headless pages emulate focus. Exercise the browser blur-event
    // boundary explicitly, then verify that the real microphone tracks end.
    await page.evaluate(()=>window.dispatchEvent(new FocusEvent("blur")));
    await until(async()=>await page.evaluate(()=>window.__voiceStreams.every(stream =>
      stream.getTracks().every(track=>track.readyState==="ended"))),"microphone cancelled on focus loss");
    await page.bringToFront();
    // Each Playwright page keeps its own keyboard state; clear the key held
    // before switching pages so the next keydown is a new press.
    await page.keyboard.up("Space");
    assert.match(await page.locator("[data-voice-status]").textContent(),/Recording cancelled/);
    assert.equal((await f.api("/test/voice/events")).events.filter(event=>event.action==="transcribe").length,1);
    await talk.focus(); await page.keyboard.down("Space");
    await until(async()=>/Recording\./.test(await page.locator("[data-voice-status]").textContent()),"keyboard recording");
    await page.keyboard.press("Tab"); await page.keyboard.up("Space");
    await until(async()=>await page.evaluate(()=>window.__voiceStreams.every(stream =>
      stream.getTracks().every(track=>track.readyState==="ended"))),"microphone cancelled when Talk loses focus");
    await page.waitForTimeout(5200);
    assert.equal((await f.api("/test/voice/events")).events.filter(event=>event.action==="transcribe").length,1,
      "Moving keyboard focus must discard the recording instead of submitting at its deadline");
    await page.screenshot({path:"test-output/hosted-voice.png",fullPage:true});
    await f.api("/test/"+config.session.id+"/close",{});
    await until(async()=>/Voice session ended/.test(await page.locator("[data-voice-status]").textContent()),"hosted voice cleanup");
  } finally {await browser?.close();await f.stop();}
});

test("long hosted transcriptions leave typed chat controls reachable", async () => {
  const f=await fixture(); let browser;
  const transcript="Please move the primary action button and explain how the updated layout behaves for every station operator. ".repeat(9).trim();
  try {
    browser=await chromium.launch({headless:true,args:["--no-sandbox",
      "--use-fake-device-for-media-stream","--use-fake-ui-for-media-stream"]});
    const page=await browser.newPage({viewport:{width:1366,height:768}});
    const config=await f.api("/test/setup",{voice_transcript:transcript});
    await page.request.post(f.base+"/api/auth/login");
    await page.goto(f.base+"/addons/browser-sessions-addon/view/"+config.session.id,
      {waitUntil:"domcontentloaded"});
    await page.getByRole("button",{name:"Enable microphone",exact:true}).click();
    const talk=page.getByRole("button",{name:"Hold Talk",exact:true});
    await talk.focus(); await page.keyboard.down("Space");
    await until(async()=>/Recording\./.test(await page.locator("[data-voice-status]").textContent()),"long recording");
    await page.waitForTimeout(400); await page.keyboard.up("Space");
    await page.getByRole("log").getByText(transcript,{exact:false}).waitFor();
    await until(()=>talk.isEnabled(),"long transcription sent");
    const send=page.getByRole("button",{name:"Send",exact:true});
    const bounds=await send.boundingBox();
    assert.ok(bounds && bounds.y>=0 && bounds.y+bounds.height<=page.viewportSize().height,
      "Send must remain inside the viewport after a long transcription");
    const followup="Please keep the button beside the material field.";
    await page.getByRole("textbox",{name:"Message Truman",exact:true}).fill(followup);
    await send.click();
    await page.getByRole("log").getByText(followup,{exact:false}).waitFor();
    const messages=(await f.api("/test/"+config.session.id+"/messages")).messages;
    assert.ok(messages.some(message=>message.body===transcript),"The full transcript remains in chat");
    assert.ok(messages.some(message=>message.body===followup),"Typed feedback still sends after voice");
    await page.screenshot({path:"test-output/hosted-long-transcript.png",fullPage:true});
  } finally {await browser?.close();await f.stop();}
});

async function localVoiceHost(f, { microphoneGranted = false } = {}) {
  const extension = join(f.folder, "extension"); await cp("dist/extension", extension, { recursive: true });
  const manifest = JSON.parse(await readFile(join(extension, "manifest.json"), "utf8"));
  manifest.host_permissions = ["http://127.0.0.1/*"];
  await writeFile(join(extension, "manifest.json"), JSON.stringify(manifest));
  const context = await chromium.launchPersistentContext(join(f.folder, "chrome"), {
    channel: "chromium", headless: true,
    args: ["--no-sandbox", "--disable-extensions-except=" + extension, "--load-extension=" + extension,
      "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"],
  });
  try {
    await trackVoice(context);
    await context.addCookies([{ name: "demo_session", value: "fixture", url: f.base }]);
    const app = await context.newPage(); await app.goto(f.base + "/application", { waitUntil: "domcontentloaded" });
    const sw = context.serviceWorkers()[0] ?? await context.waitForEvent("serviceworker", { timeout: 10000 });
    const extensionId = new URL(sw.url()).host;
    const panel = await context.newPage(); await panel.goto("chrome-extension://" + extensionId + "/panel.html");
    if (microphoneGranted) {
      // Exercise Chrome's real extension-origin grant with the synthetic device.
      // Browser.grantPermissions does not accept extension origins.
      await panel.evaluate(async () => {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
        stream.getTracks().forEach(track => track.stop());
      });
      assert.equal(await panel.evaluate(async () => (await navigator.permissions.query({name:"microphone"})).state), "granted");
    }
    await panel.setViewportSize({ width: 380, height: 900 });
    await app.bringToFront();
    const setup = await f.api("/test/setup", { local: true });
    const result = await panel.evaluate(invitation => chrome.runtime.sendMessage({ kind: "pair", invitation }), setup.invitation_url);
    assert.equal(result.ok, true, result.error);
    await panel.bringToFront();
    await panel.getByText("Voice", { exact: true }).click();
    await until(async () => await panel.getByRole("button", { name: "Enable microphone", exact: true }).isEnabled(), "sidebar voice ready");
    return { context, app, panel, setup, extensionId };
  } catch (error) { await context.close(); throw error; }
}

test("Chrome extension records and plays voice in its sidebar without opening a voice tab", async () => {
  const f = await fixture(); let context;
  try {
    const host = await localVoiceHost(f, { microphoneGranted: true }); context = host.context;
    const { panel, setup } = host;
    const pages = context.pages().length;
    await exerciseVoice(panel, f, setup.session.id);
    assert.equal(context.pages().length, pages, "Sidebar voice must not create a separate tab");
    assert.equal((await f.api("/test/" + setup.session.id + "/state")).controller, null);
    assert.equal(new URL(panel.url()).hash, "", "No voice capability belongs in extension URLs");
    assert.ok(!(await context.cookies()).some(cookie => cookie.name === "evander_admin_session"));
    const followup = "Continue using text.";
    await panel.getByRole("textbox", { name: "Message Truman", exact: true }).fill(followup);
    await panel.getByRole("button", { name: "Send", exact: true }).click();
    await panel.getByRole("log").getByText(followup, { exact: false }).waitFor();
    await panel.screenshot({ path: "test-output/local-sidebar-voice.png", fullPage: true });
    await f.api("/test/" + setup.session.id + "/close", {});
    await until(async () => /Voice session ended/.test(await panel.locator("[data-voice-status]").textContent()), "sidebar voice cleanup");
  } finally { await context?.close(); await f.stop(); }
});

test("sidebar microphone permission uses a temporary popup and supports cancellation and retry", async () => {
  const f = await fixture(); let context;
  try {
    const host = await localVoiceHost(f); context = host.context;
    const { panel } = host;
    // Fake-media UI pregrants capture. Exercise the first-use UI branch without
    // replacing getUserMedia, window creation or permission-result messages.
    await panel.evaluate(() => {
      const query = navigator.permissions.query.bind(navigator.permissions);
      window.__restoreMicrophonePermissions = () => { navigator.permissions.query = query; };
      navigator.permissions.query = async options => options.name === "microphone"
        ? { state: "prompt" } : query(options);
    });
    const enable = panel.getByRole("button", { name: "Enable microphone", exact: true });
    let opened = context.waitForEvent("page", { timeout: 10000 });
    await enable.click();
    const cancelled = await opened;
    await cancelled.waitForLoadState("domcontentloaded");
    assert.equal((await cancelled.evaluate(() => chrome.windows.getCurrent())).type, "popup");
    const denied = await cancelled.evaluate(id => chrome.runtime.sendMessage({kind:"voice",browser_session_id:id}), host.setup.session.id);
    assert.equal(denied.ok, false, "The permission page cannot obtain a sharing voice capability");
    await cancelled.getByRole("button", { name: "Cancel", exact: true }).click();
    await until(async () => /cancelled/.test(await panel.locator("[data-voice-status]").textContent()), "microphone permission cancelled");
    opened = context.waitForEvent("page", { timeout: 10000 });
    await enable.click();
    const permission = await opened;
    await permission.waitForLoadState("domcontentloaded");
    const closed = permission.waitForEvent("close");
    await permission.getByRole("button", { name: "Allow microphone", exact: true }).click();
    await closed;
    await panel.getByRole("button", { name: "Hold Talk", exact: true }).waitFor();
    assert.ok((await f.api("/test/voice/events")).events.length === 0, "Permission setup must not upload audio");
    await panel.evaluate(() => window.__restoreMicrophonePermissions());
    await panel.getByText("Voice", { exact: true }).click();
    await panel.getByText("Voice", { exact: true }).click();
    const pages = context.pages().length;
    await enable.click();
    await panel.getByRole("button", { name: "Hold Talk", exact: true }).waitFor();
    assert.equal(context.pages().length, pages, "An existing microphone grant must not open another window");
  } finally { await context?.close(); await f.stop(); }
});

test("ending sharing aborts sidebar transcription before it can send into another session", async () => {
  const f = await fixture(); let context, release;
  try {
    const host = await localVoiceHost(f, { microphoneGranted: true }); context = host.context;
    const { panel, app, setup } = host;
    let requested = false;
    const resume = new Promise(resolve => { release = resolve; });
    await panel.route("**/_browser/guest/voice/transcribe", async route => {
      requested = true; await resume; await route.continue().catch(() => {});
    });
    await panel.getByRole("button", { name: "Enable microphone", exact: true }).click();
    const talk = panel.getByRole("button", { name: "Hold Talk", exact: true });
    await talk.focus(); await panel.keyboard.down("Space");
    await until(async () => /Recording\./.test(await panel.locator("[data-voice-status]").textContent()), "sidebar recording");
    await panel.waitForTimeout(350); await panel.keyboard.up("Space");
    await until(() => requested, "sidebar transcription request");
    await panel.getByRole("button", { name: "End sharing", exact: true }).click();
    const next = await f.api("/test/setup", { local: true });
    await app.bringToFront();
    const result = await panel.evaluate(invitation => chrome.runtime.sendMessage({ kind: "pair", invitation }), next.invitation_url);
    assert.equal(result.ok, true, result.error);
    release();
    await panel.bringToFront();
    await until(async () => await panel.locator("#title").textContent() === next.session.title, "replacement session");
    assert.deepEqual((await f.api("/test/" + setup.session.id + "/messages")).messages, []);
    assert.deepEqual((await f.api("/test/" + next.session.id + "/messages")).messages, []);
    assert.equal((await f.api("/test/voice/events")).events.length, 0);
    assert.ok(await panel.evaluate(() => window.__voiceStreams.every(stream => stream.getTracks().every(track => track.readyState === "ended"))));
  } finally { release?.(); await context?.close(); await f.stop(); }
});

// Playwright does not expose Chrome's side-panel WebContents as a normal tab.
// Attach a protocol session to that target so this test exercises the real panel.
async function nativeSidebar(host) {
  const { context, panel } = host;
  const bridge = await context.newCDPSession(context.pages()[0]);
  const before = new Set((await bridge.send("Target.getTargets")).targetInfos.map(t => t.targetId));
  await panel.getByText("Voice", { exact: true }).click();
  await panel.evaluate(() => {
    const button = document.createElement("button"); button.textContent = "Open native sidebar";
    button.onclick = async () => {
      const { connection } = await chrome.storage.session.get("connection");
      await chrome.tabs.update(connection.tabId, { active: true });
      await chrome.sidePanel.open({ tabId: connection.tabId });
    };
    document.body.append(button);
  });
  await panel.getByRole("button", { name: "Open native sidebar", exact: true }).click();
  const target = await until(async () => (await bridge.send("Target.getTargets")).targetInfos.find(
    t => !before.has(t.targetId) && t.type === "page" && t.url === panel.url()), "native sidebar target");
  const { sessionId } = await bridge.send("Target.attachToTarget", { targetId: target.targetId, flatten: false });
  let sequence = 0;
  const pending = new Map();
  bridge.on("Target.receivedMessageFromTarget", event => {
    if (event.sessionId !== sessionId) return;
    const response = JSON.parse(event.message), item = pending.get(response.id);
    if (!item) return;
    pending.delete(response.id); clearTimeout(item.timer);
    if (response.error) item.reject(new Error(response.error.message)); else item.resolve(response.result);
  });
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++sequence;
    const timer = setTimeout(() => { pending.delete(id); reject(new Error("Native sidebar command timed out: " + method)); }, 10000);
    pending.set(id, { resolve, reject, timer });
    void bridge.send("Target.sendMessageToTarget", { sessionId, message: JSON.stringify({ id, method, params }) }).catch(error => {
      clearTimeout(timer); pending.delete(id); reject(error);
    });
  });
  const evaluate = async (fn, value) => {
    const result = await send("Runtime.evaluate", {
      expression: "(" + fn.toString() + ")(" + JSON.stringify(value) + ")",
      awaitPromise: true, returnByValue: true, userGesture: true,
    });
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description ?? "Native sidebar evaluation failed");
    return result.result.value;
  };
  const down = async selector => {
    const point = await until(async () => evaluate(selector => {
      const target = document.querySelector(selector);
      if (!target || target.disabled) return null;
      target.scrollIntoView({ block: "center" });
      const rect = target.getBoundingClientRect();
      if (!rect.width || !rect.height) return null;
      return { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 };
    }, selector), "native sidebar element " + selector);
    await send("Input.dispatchMouseEvent", { type: "mouseMoved", ...point });
    await send("Input.dispatchMouseEvent", { type: "mousePressed", button: "left", clickCount: 1, ...point });
    return point;
  };
  const up = point => send("Input.dispatchMouseEvent", { type: "mouseReleased", button: "left", clickCount: 1, ...point });
  return { evaluate, down, up, click: async selector => up(await down(selector)),
    dispose: () => { for (const item of pending.values()) clearTimeout(item.timer); return bridge.detach(); } };
}

test("native Chrome side panel captures speech and plays replies alongside the shared application", async () => {
  const f = await fixture(); let context, sidebar;
  try {
    const host = await localVoiceHost(f, { microphoneGranted: true }); context = host.context;
    const pages = context.pages().length;
    sidebar = await nativeSidebar(host);
    await sidebar.evaluate(() => {
      window.__voicePlayCount = 0;
      const play = HTMLMediaElement.prototype.play;
      HTMLMediaElement.prototype.play = function () {
        return play.call(this).then(() => { window.__voicePlayCount += 1; });
      };
    });
    assert.equal(await sidebar.evaluate(() => document.visibilityState), "visible");
    await sidebar.click("#voice-panel > summary");
    try {
      await until(async () => await sidebar.evaluate(() => !document.querySelector("[data-voice-talk]").disabled), "native sidebar voice ready");
    } catch (error) {
      const state = await sidebar.evaluate(() => ({open:document.querySelector("#voice-panel").open,
        visible:document.visibilityState,status:document.querySelector("[data-voice-status]").textContent,
        error:document.querySelector("#error").textContent,sessionHidden:document.querySelector("#session").hidden}));
      throw new Error(error.message + ": " + JSON.stringify(state));
    }
    await sidebar.click("[data-voice-replies]");
    await sidebar.click("[data-voice-talk]");
    await until(async () => await sidebar.evaluate(() => document.querySelector("[data-voice-talk]").textContent === "Hold Talk"), "native sidebar microphone");
    const point = await sidebar.down("[data-voice-talk]");
    await until(async () => await sidebar.evaluate(() => /Recording\./.test(document.querySelector("[data-voice-status]").textContent)), "native sidebar recording");
    await new Promise(resolve => setTimeout(resolve, 350));
    await sidebar.up(point);
    const question = await until(async () => (await f.api("/test/" + host.setup.session.id + "/messages")).messages.find(
      row => row.body === "Move the button to the left."), "native sidebar transcription");
    await f.api("/test/" + host.setup.session.id + "/reply", { message_id: question.id, body: "Updated from the sidebar." });
    await until(async () => await sidebar.evaluate(() => window.__voicePlayCount > 0), "native sidebar spoken reply");
    assert.equal(context.pages().length, pages, "The real side panel must not create another tab");
    assert.equal((await f.api("/test/" + host.setup.session.id + "/state")).controller, null);
    await sidebar.click("#disconnect");
    await until(async () => (await f.api("/test/" + host.setup.session.id + "/state")).state === "closed", "native sidebar ended sharing");
  } finally { await sidebar?.dispose(); await context?.close(); await f.stop(); }
});


async function compatibilityHost(f, { captureGrant = true, foreignInitially = true } = {}) {
  const extension = join(f.folder, "extension");
  await cp("dist/extension", extension, { recursive: true });
  const manifest = JSON.parse(await readFile(join(extension, "manifest.json"), "utf8"));
  manifest.host_permissions = ["http://127.0.0.1/*"];
  await writeFile(join(extension, "manifest.json"), JSON.stringify(manifest));
  const foreign = join(f.folder, "foreign-extension"); await mkdir(foreign);
  await writeFile(join(foreign, "manifest.json"), JSON.stringify({ manifest_version: 3, name: "Synthetic password manager", version: "1.0",
    background: { service_worker: "worker.js" }, web_accessible_resources: [{ resources: ["frame.html", "frame.js"], matches: ["http://127.0.0.1/*"] }] }));
  await writeFile(join(foreign, "worker.js"), "chrome.runtime.onInstalled.addListener(() => {});");
  await writeFile(join(foreign, "frame.html"), '<button id="manager">Password manager fixture</button><script src="frame.js"></script>');
  await writeFile(join(foreign, "frame.js"), 'document.querySelector("button").onclick = event => event.target.textContent = "Manager still active";');
  // Headless Playwright cannot click Chrome's toolbar. Chromium's test-only
  // allowlist supplies that user invocation for this disposable extension only.
  // Capture itself, the protected iframe and DOM input all use the real APIs.
  const id = createHash("sha256").update(extension).digest("hex").slice(0, 32).replace(/[0-9a-f]/g, value => String.fromCharCode(97 + parseInt(value, 16)));
  const context = await chromium.launchPersistentContext(join(f.folder, "chrome"), { channel: "chromium", headless: true, viewport: null,
    args: ["--no-sandbox", "--disable-extensions-except=" + extension + "," + foreign, "--load-extension=" + extension + "," + foreign,
      ...(captureGrant ? ["--allowlisted-extension-id=" + id] : [])] });
  try {
    await context.addCookies([{ name: "demo_session", value: "fixture", url: f.base }]);
    const worker = await until(() => context.serviceWorkers().find(value => value.url().endsWith("/background.js")), "sharing worker");
    const other = await until(() => context.serviceWorkers().find(value => value.url().endsWith("/worker.js")), "password manager worker");
    const application = await context.newPage(); await application.goto(f.base + "/application", { waitUntil: "domcontentloaded" });
    const panel = await context.newPage(); await panel.goto("chrome-extension://" + new URL(worker.url()).host + "/panel.html", { waitUntil: "domcontentloaded" });
    const inject = async () => {
      const url = "chrome-extension://" + new URL(other.url()).host + "/frame.html";
      await application.evaluate(url => { const frame = document.createElement("iframe"); frame.id = "password-manager"; frame.src = url; document.body.append(frame); }, url);
      await application.frameLocator("#password-manager").getByRole("button", { name: "Password manager fixture" }).waitFor();
    };
    if (foreignInitially) await inject();
    await application.bringToFront();
    const setup = await f.api("/test/setup", { local: true }), path = "/test/" + setup.session.id;
    const send = message => panel.evaluate(message => chrome.runtime.sendMessage(message), message);
    const paired = await send({ kind: "pair", invitation: setup.invitation_url });
    return { context, worker, application, panel, setup, path, paired, send, inject };
  } catch (error) { await context.close(); throw error; }
}

test("sharing coexists with a protected extension frame, preserves grants and captures the selected background tab", async () => {
  const f = await fixture(); let host, second;
  try {
    host = await compatibilityHost(f);
    assert.equal(host.paired.ok, true, host.paired.error);
    assert.equal((await host.send({ kind: "state" })).mode, "compatibility");
    let state = await until(async () => { const value = await f.api(host.path + "/state"); return value.state === "paused" && value; }, "compatible sharing ready");
    assert.equal(state.controller, null);
    const observe = () => f.api(host.path + "/command", command(state, "observe"));
    let observed = await observe();
    assert.ok(observed.image.length > 1000);
    assert.ok(observed.elements.some(value => value.name === "Station materials"));
    assert.ok(!observed.elements.some(value => value.name.includes("Password manager")), "Never inspect protected extension frame DOM");
    const beforeGrant = await fetch(f.base + host.path + "/command", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(command(state, "fill", { observation_id: observed.observation_id, ref: observed.elements.find(value => value.role === "textbox").ref, text: "Denied" })) });
    assert.equal(beforeGrant.status, 403);
    assert.equal((await host.send({ kind: "control", action: "grant" })).ok, true);
    state = await f.api(host.path + "/state"); observed = await observe();
    await f.api(host.path + "/command", command(state, "fill", { observation_id: observed.observation_id,
      ref: observed.elements.find(value => value.role === "textbox").ref, text: "Copper" }));
    await f.api(host.path + "/command", command(state, "type", { observation_id: observed.observation_id, text: " alloy" }));
    await f.api(host.path + "/command", command(state, "click", { observation_id: observed.observation_id,
      ref: observed.elements.find(value => value.role === "button").ref }));
    assert.equal(await host.application.locator("#result").textContent(), "Copper alloy added");
    await host.application.frameLocator("#password-manager").getByRole("button").click();
    assert.equal(await host.application.frameLocator("#password-manager").getByRole("button").textContent(), "Manager still active");
    const otherTab = await host.context.newPage(); await otherTab.goto(f.base + "/position");
    await otherTab.evaluate(() => { document.body.style.background = "rgb(255, 0, 0)"; });
    await otherTab.bringToFront();
    const after = await observe();
    const pixel = await host.panel.evaluate(async image => {
      const bitmap = await createImageBitmap(await (await fetch("data:image/jpeg;base64," + image)).blob());
      const canvas = document.createElement("canvas"); canvas.width = bitmap.width; canvas.height = bitmap.height;
      const paint = canvas.getContext("2d"); paint.drawImage(bitmap, 0, 0); bitmap.close();
      return [...paint.getImageData(5, 5, 1, 1).data];
    }, after.image);
    await writeFile("test-output/compatible-background.jpeg", Buffer.from(after.image, "base64"));
    assert.ok(pixel[1] > 230 && pixel[2] > 230, "Capture remains on the application, not the red active tab: " + JSON.stringify(pixel));
    assert.ok(after.image.length > 1000);
    assert.ok(after.elements.some(value => value.name === "Station materials"));
    await host.application.evaluate(() => { const input = document.querySelector("input"); input.style.marginTop = "1800px"; });
    observed = await observe();
    await f.api(host.path + "/command", command(state, "click", { observation_id: observed.observation_id,
      ref: observed.elements.find(value => value.role === "button").ref }));
    assert.ok(await host.application.evaluate(() => scrollY > 0), "Element click scrolls the application page");
    await host.send({ kind: "control", action: "pause" });
    assert.equal((await f.api(host.path + "/state")).controller, null);
    second = await fixture();
    await host.application.goto(second.base + "/application", { waitUntil: "domcontentloaded" });
    await until(async () => (await f.api(host.path + "/state")).controller === null, "cross-origin pause");
    const blocked = await fetch(f.base + host.path + "/command", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(command(await f.api(host.path + "/state"), "observe")) });
    assert.equal(blocked.status, 409, "Unapproved origin cannot be observed");
    const approved = await host.send({ kind: "origin" }); assert.equal(approved.ok, true, approved.error);
    state = await f.api(host.path + "/state");
    observed = await observe(); assert.equal(new URL(observed.url).origin, second.base);
    assert.equal(state.controller, null, "Origin approval never grants input");
    await host.send({ kind: "disconnect" });
    assert.equal((await f.api(host.path + "/state")).state, "closed");
    assert.equal(await host.worker.evaluate(() => chrome.offscreen.hasDocument()), false, "Close releases capture document and tracks");
  } finally { await host?.context.close(); await second?.stop(); await f.stop(); }
});

test("compatibility setup without the toolbar grant closes its session and capture resources", async () => {
  const f = await fixture(); let host;
  try {
    host = await compatibilityHost(f, { captureGrant: false });
    assert.equal(host.paired.ok, false);
    assert.match(host.paired.error, /toolbar icon/);
    assert.equal((await f.api(host.path + "/state")).state, "closed");
    assert.equal(await host.worker.evaluate(() => chrome.offscreen.hasDocument()), false);
    assert.equal(await host.application.locator("#password-manager").count(), 1);
  } finally { await host?.context.close(); await f.stop(); }
});


test("a protected frame added during native sharing switches modes without replaying input or retaining control", async () => {
  const f = await fixture(); let host;
  try {
    host = await compatibilityHost(f, { foreignInitially: false });
    assert.equal(host.paired.ok, true, host.paired.error);
    let state = await until(async () => { const value = await f.api(host.path + "/state"); return value.state === "paused" && value; }, "native sharing ready");
    await host.send({ kind: "control", action: "grant" }); state = await f.api(host.path + "/state");
    await f.api(host.path + "/command", command(state, "observe"));
    await host.inject();
    const response = await fetch(f.base + host.path + "/command", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(command(state, "observe")) });
    assert.equal(response.status, 409, "Mode changes require fresh observation and control");
    await until(async () => (await host.send({ kind: "state" })).mode === "compatibility", "switch to compatibility");
    state = await until(async () => { const value = await f.api(host.path + "/state"); return value.controller === null && value; }, "control revoked on mode change");
    assert.equal(await host.application.locator("#result").textContent(), "");
    const observed = await f.api(host.path + "/command", command(state, "observe"));
    assert.ok(observed.elements.some(value => value.name === "Station materials"));
    assert.equal(await host.application.locator("#password-manager").count(), 1);
    await host.send({ kind: "disconnect" });
  } finally { await host?.context.close(); await f.stop(); }
});
