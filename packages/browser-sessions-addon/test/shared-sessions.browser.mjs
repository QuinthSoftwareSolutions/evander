import test from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, writeFile, rm, cp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { createRequire } from "node:module";
import { once } from "node:events";
import { chromium } from "playwright";
const require = createRequire(import.meta.url);

async function until(check, label, ms=15000) {
  const end = Date.now()+ms;
  while (Date.now()<end) {
    const result = await check();
    if (result) return result;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error("Timed out: " + label);
}
async function fixture() {
  const folder = await mkdtemp(join(tmpdir(), "evander-browser-"));
  const child = spawn(process.env.EVANDER_TEST_PYTHON ?? "python3",
    ["test/server.py", folder], { stdio:["ignore", "pipe", "pipe"] });
  let logs = ""; child.stderr.on("data", data => logs += data);
  const first = await until(async () => {
    const chunk = child.stdout.read();
    if (!chunk) return null;
    return JSON.parse(String(chunk).trim().split("\n")[0]);
  }, "integration fixture");
  const api = async (path, body) => {
    const response = await fetch(first.base+path, { method: body ? "POST" : "GET",
      headers: body ? {"Content-Type":"application/json"} : {}, body: body ? JSON.stringify(body) : undefined, signal:AbortSignal.timeout(10000) });
    if (!response.ok) throw new Error("Fixture API " + response.status + " " + await response.text());
    return response.json();
  };
  return { folder, base:first.base, api, stop:async()=>{
    child.kill("SIGTERM");
    if(child.exitCode===null && child.signalCode===null) await Promise.race([once(child,"exit"),new Promise(resolve=>setTimeout(resolve,3000))]);
    if(child.exitCode===null && child.signalCode===null){child.kill("SIGKILL");await once(child,"exit");}
    await rm(folder,{recursive:true,force:true});
    assert.equal(logs, "", "fixture should have no server errors");
  }};
}
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
    assert.equal(state.controller,"agent:support-a");
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

test("Chrome extension opens scoped local voice without an Admin login or an input grant", async () => {
  const f=await fixture(); let context;
  try {
    const extension=join(f.folder,"extension");await cp("dist/extension",extension,{recursive:true});
    const manifest=JSON.parse(await readFile(join(extension,"manifest.json"),"utf8"));
    manifest.host_permissions=["http://127.0.0.1/*"];
    await writeFile(join(extension,"manifest.json"),JSON.stringify(manifest));
    context=await chromium.launchPersistentContext(join(f.folder,"chrome"),{channel:"chromium",headless:true,
      args:["--no-sandbox","--disable-extensions-except="+extension,"--load-extension="+extension,
        "--use-fake-device-for-media-stream","--use-fake-ui-for-media-stream"]});
    await trackVoice(context);
    await context.addCookies([{name:"demo_session",value:"fixture",url:f.base}]);
    const app=await context.newPage();await app.goto(f.base+"/application",{waitUntil:"domcontentloaded"});
    const sw=context.serviceWorkers()[0]??await context.waitForEvent("serviceworker",{timeout:10000});
    const extensionId=new URL(sw.url()).host;
    const panel=await context.newPage();await panel.goto("chrome-extension://"+extensionId+"/panel.html");
    await app.bringToFront();
    const setup=await f.api("/test/setup",{local:true});
    const result=await panel.evaluate(invitation=>chrome.runtime.sendMessage({kind:"pair",invitation}),setup.invitation_url);
    assert.equal(result.ok,true,result.error);
    const opened=context.waitForEvent("page",{timeout:10000});
    await panel.getByRole("button",{name:"Open voice chat",exact:true}).click();
    const page=await opened;
    await page.waitForLoadState("domcontentloaded");
    await page.getByRole("heading",{name:"Talk with Truman",exact:true}).waitFor();
    await exerciseVoice(page,f,setup.session.id);
    assert.equal((await f.api("/test/"+setup.session.id+"/state")).controller,null);
    assert.equal(new URL(page.url()).hash,"","Voice capability must leave browser history after admission");
    assert.ok(!(await context.cookies()).some(cookie=>cookie.name==="evander_admin_session"));
    await page.screenshot({path:"test-output/local-voice.png",fullPage:true});
    await f.api("/test/"+setup.session.id+"/close",{});
    await until(async()=>/Voice session ended/.test(await page.locator("[data-voice-status]").textContent()),"local voice cleanup");
  } finally {await context?.close();await f.stop();}
});
