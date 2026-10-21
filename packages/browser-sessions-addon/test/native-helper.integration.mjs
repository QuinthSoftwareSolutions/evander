import test from "node:test";
import assert from "node:assert/strict";
import { cp, mkdir, readFile, writeFile, mkdtemp, rm, access } from "node:fs/promises";
import { join, resolve } from "node:path";
import { tmpdir } from "node:os";
import { promisify } from "node:util";
import { execFile } from "node:child_process";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { createHash } from "node:crypto";
import { chromium } from "playwright";
import { fixture, until } from "./fixture.mjs";

const command = (state, action, extra = {}) => ({ id: crypto.randomUUID(), generation: state.control_generation, action, ...extra });
async function helperHost(f) {
  const extension = join(f.folder, "extension"); await cp("dist/extension", extension, { recursive: true });
  const manifest = JSON.parse(await readFile(join(extension, "manifest.json"), "utf8"));
  manifest.host_permissions = ["http://127.0.0.1/*"];
  manifest.permissions.push("nativeMessaging"); // Pregrant the optional permission requested by the shipped Share click.
  await writeFile(join(extension, "manifest.json"), JSON.stringify(manifest));
  const id = createHash("sha256").update(extension).digest("hex").slice(0, 32).replace(/[0-9a-f]/g, value => String.fromCharCode(97 + parseInt(value, 16)));
  const profile = join(f.folder, "chrome"), config = join(f.folder, "config"), home = join(f.folder, "home");
  await mkdir(home); await mkdir(profile);
  const host = join(f.folder, "helper"); await cp("helper/bin/Debug/net10.0", host, { recursive: true });
  await writeFile(join(host, "helper.json"), JSON.stringify({ browser_data_dir: profile, allowed_origins: ["chrome-extension://" + id + "/"] }));
  const registration = JSON.stringify({ name: "com.evander.browser_helper", description: "Evander Browser Helper test", type: "stdio",
    path: join(host, "evander-browser-helper"), allowed_origins: ["chrome-extension://" + id + "/"] });
  for (const base of [profile, ...["chromium", "google-chrome", "google-chrome-for-testing"].map(name => join(config, name))]) {
    await mkdir(join(base, "NativeMessagingHosts"), { recursive: true });
    await writeFile(join(base, "NativeMessagingHosts/com.evander.browser_helper.json"), registration);
  }
  const foreign = join(f.folder, "foreign"); await mkdir(foreign);
  await writeFile(join(foreign, "manifest.json"), JSON.stringify({ manifest_version: 3, name: "Synthetic password manager", version: "1.0",
    background: { service_worker: "worker.js" }, web_accessible_resources: [{ resources: ["frame.html"], matches: ["http://127.0.0.1/*"] }] }));
  await writeFile(join(foreign, "worker.js"), "chrome.runtime.onInstalled.addListener(() => {});");
  await writeFile(join(foreign, "frame.html"), "<h2>Protected extension frame</h2>");
  const context = await chromium.launchPersistentContext(profile, { channel: "chromium", headless: true, viewport: null,
    env: { ...process.env, XDG_CONFIG_HOME: config, HOME: home },
    // Only this disposable profile uses a debugging flag. Production discovers
    // Chrome's consent-based endpoint and never changes flags or profiles.
    args: ["--no-sandbox", "--remote-debugging-port=0", "--disable-extensions-except=" + extension + "," + foreign, "--load-extension=" + extension + "," + foreign] });
  try {
    await until(async () => { try { return await readFile(join(profile, "DevToolsActivePort"), "utf8"); } catch { return null; } }, "local Chrome endpoint");
    await context.addCookies([{ name: "demo_session", value: "fixture", url: f.base }]);
    const worker = await until(() => context.serviceWorkers().find(value => value.url().endsWith("/background.js")), "sharing worker");
    const other = await until(() => context.serviceWorkers().find(value => value.url().endsWith("/worker.js")), "foreign worker");
    const application = await context.newPage(); await application.goto(f.base + "/application", { waitUntil: "domcontentloaded" });
    await application.evaluate(url => { const frame = document.createElement("iframe"); frame.id = "protected"; frame.src = url; document.body.append(frame); }, "chrome-extension://" + new URL(other.url()).host + "/frame.html");
    await application.frameLocator("#protected").getByRole("heading").waitFor();
    const panel = await context.newPage(); await panel.goto("chrome-extension://" + id + "/panel.html", { waitUntil: "domcontentloaded" });
    await application.bringToFront();
    const setup = await f.api("/test/setup", { local: true }), path = "/test/" + setup.session.id;
    const send = message => panel.evaluate(message => chrome.runtime.sendMessage(message), message);
    const paired = await send({ kind: "pair", invitation: setup.invitation_url, mode: "helper" });
    return { context, worker, application, panel, send, paired, setup, path, profile, host };
  } catch (error) { await context.close(); throw error; }
}

test("native helper controls the selected tab with trusted input while another extension remains installed", async () => {
  const f = await fixture(); let host, second;
  try {
    host = await helperHost(f);
    assert.equal(host.paired.ok, true, host.paired.error);
    assert.equal((await host.send({ kind: "state" })).mode, "helper");
    let state = await until(async () => { const value = await f.api(host.path + "/state"); return value.state === "paused" && value; }, "helper ready");
    let observed = await f.api(host.path + "/command", command(state, "observe"));
    assert.ok(observed.elements.some(value => value.name === "Station materials")); assert.ok(observed.image.length > 1000);
    assert.equal(state.controller, null);
    await host.application.evaluate(() => { window.trustedKeys = []; document.addEventListener("keydown", event => window.trustedKeys.push(event.isTrusted)); });
    assert.equal((await host.send({ kind: "control", action: "grant" })).ok, true);
    state = await f.api(host.path + "/state"); observed = await f.api(host.path + "/command", command(state, "observe"));
    await f.api(host.path + "/command", command(state, "fill", { observation_id: observed.observation_id,
      ref: observed.elements.find(value => value.role === "textbox").ref, text: "Copper" }));
    await f.api(host.path + "/command", command(state, "type", { observation_id: observed.observation_id, text: " alloy" }));
    await f.api(host.path + "/command", command(state, "click", { observation_id: observed.observation_id,
      ref: observed.elements.find(value => value.role === "button").ref }));
    assert.equal(await host.application.locator("#result").textContent(), "Copper alloy added");
    assert.equal((await f.api(host.path+"/state")).endpoint_features.fast_execution,1);
    const run=await f.api(host.path+"/run",{request_id:"fast-helper",generation:state.control_generation,
      goal:"Add the supplied item",values:{name:{description:"Item name",value:"Fast item"}}});
    assert.equal(run.status,"needs_verification",run.reason);
    assert.equal(await host.application.locator("#result").textContent(),"Fast item added");
    assert.deepEqual(await host.application.evaluate(() => [...new Set(window.trustedKeys)]), [true]);
    assert.equal(await host.application.locator("#protected").count(), 1);
    await host.application.evaluate(() => { document.body.style.minHeight = "3000px";
      document.body.style.background = "linear-gradient(red 0 1000px, blue 1000px)"; scrollTo(0, 1800); });
    const scrolled = await f.api(host.path + "/command", command(state, "observe"));
    const pixel = await host.panel.evaluate(async image => {
      const bitmap = await createImageBitmap(await (await fetch("data:image/jpeg;base64," + image)).blob());
      const canvas = document.createElement("canvas"); canvas.width = bitmap.width; canvas.height = bitmap.height;
      const paint = canvas.getContext("2d"); paint.drawImage(bitmap, 0, 0); bitmap.close();
      return [...paint.getImageData(5, 5, 1, 1).data];
    }, scrolled.image);
    assert.ok(pixel[2] > 220 && pixel[0] < 40, "Screenshot follows the scrolled blue viewport, rather than the red page top: " + JSON.stringify(pixel));
    await host.send({ kind: "control", action: "pause" });
    const denied = await fetch(f.base + host.path + "/command", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(command(state, "type", { observation_id: observed.observation_id, text: "blocked" })) });
    assert.ok(denied.status >= 400);
    second = await fixture(); await host.application.goto(second.base + "/application", { waitUntil: "domcontentloaded" });
    const blocked = await fetch(f.base + host.path + "/command", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(command(await f.api(host.path + "/state"), "observe")) });
    assert.equal(blocked.status, 409);
    const approved = await host.send({ kind: "origin" }); assert.equal(approved.ok, true, approved.error);
    state = await f.api(host.path + "/state"); observed = await f.api(host.path + "/command", command(state, "observe"));
    assert.equal(new URL(observed.url).origin, second.base); assert.equal(state.controller, null);
    await host.send({ kind: "disconnect" }); assert.equal((await f.api(host.path + "/state")).state, "closed");
    assert.ok(!JSON.stringify(JSON.parse(await readFile(join(host.host, "helper.json"), "utf8"))).includes("host_token"));
  } finally { await host?.context.close(); await second?.stop(); await f.stop(); }
});


function nativeWire(host, caller) {
  const child = spawn(join(host.host, "evander-browser-helper"), [caller], { stdio: ["pipe", "pipe", "pipe"] });
  let buffer = Buffer.alloc(0), sequence = 0, epoch = 0, disconnected = false, errors = "";
  const pending = new Map();
  child.stderr.on("data", data => errors += data);
  child.stdout.on("data", data => {
    buffer = Buffer.concat([buffer, data]);
    while (buffer.length >= 4 && buffer.length >= buffer.readUInt32LE(0) + 4) {
      const length = buffer.readUInt32LE(0), message = JSON.parse(buffer.subarray(4, length + 4)); buffer = buffer.subarray(length + 4);
      if (Number.isSafeInteger(message.epoch)) epoch = message.epoch;
      if (message.kind === "disconnected") disconnected = true;
      const waiter = pending.get(message.id); if (waiter) { pending.delete(message.id); clearTimeout(waiter.timer); waiter.resolve(message); }
    }
  });
  return { child, get epoch() { return epoch; }, get disconnected() { return disconnected; },
    request: body => new Promise((resolve, reject) => {
      const id = ++sequence, data = Buffer.from(JSON.stringify({ id, ...body })), header = Buffer.alloc(4); header.writeUInt32LE(data.length);
      const timer = setTimeout(() => { pending.delete(id); reject(new Error("Native helper request timed out: " + body.kind)); }, 12000);
      pending.set(id, { resolve, timer }); child.stdin.write(Buffer.concat([header, data]));
    }),
    close: async () => {
      child.stdin.end();
      if (child.exitCode === null && child.signalCode === null) await Promise.race([once(child, "exit"), new Promise(resolve => setTimeout(resolve, 3000))]);
      if (child.exitCode === null && child.signalCode === null) { child.kill("SIGKILL"); await once(child, "exit"); }
      for (const waiter of pending.values()) clearTimeout(waiter.timer);
      assert.equal(child.exitCode, 0, errors); assert.equal(errors, "");
    },
  };
}

test("helper independently rejects ungranted input, arbitrary CDP, stale generations, unapproved navigation and expiry", async () => {
  const f = await fixture(); let host, wire;
  try {
    host = await helperHost(f); assert.equal(host.paired.ok, true, host.paired.error);
    const target = await host.worker.evaluate(url => chrome.debugger.getTargets().then(values => values.find(value => value.url === url && value.type === "page").id), f.base + "/application");
    await host.send({ kind: "disconnect" });
    const caller = "chrome-extension://" + new URL(host.panel.url()).host + "/";
    wire = nativeWire(host, caller);
    const state = { ...host.setup.session, id: "native-fixture", state: "paused", controller: null, control_generation: 0 };
    assert.equal((await wire.request({ kind: "open", target_id: target, state })).ok, true);
    const invoke = (method, params = {}, generation = state.control_generation) => wire.request({ kind: "command", method, params, generation, epoch: wire.epoch });
    assert.equal((await invoke("Page.enable")).ok, true);
    await host.application.locator("input").focus();
    assert.equal((await invoke("Input.insertText", { text: "blocked" })).error, "control_required");
    assert.equal((await invoke("Runtime.evaluate", { expression: "document.cookie" })).error, "unsupported_action");
    state.control_generation = 1; state.controller = "agent:agent-a";
    assert.equal((await wire.request({ kind: "state", state })).ok, true);
    assert.equal((await invoke("Input.insertText", { text: "blocked" }, 0)).error, "stale_control");
    assert.equal((await invoke("Page.navigate", { url: "https://unapproved.example/" })).error, "origin_denied");
    assert.equal(host.application.url(), f.base + "/application");
    assert.equal(await host.application.locator("input").inputValue(), "");
    const expiredRun=await wire.request({kind:"command",method:"Input.insertText",params:{text:"blocked by deadline"},
      generation:state.control_generation,epoch:wire.epoch,deadline_at:Date.now()/1000-1});
    assert.equal(expiredRun.error,"run_deadline");
    assert.equal(await host.application.locator("input").inputValue(),"");
    assert.equal((await invoke("Input.insertText", { text: "allowed" })).ok, true);
    assert.equal(await host.application.locator("input").inputValue(), "allowed");
    state.expires_at = Date.now() / 1000 + 0.25;
    assert.equal((await wire.request({ kind: "state", state })).ok, true);
    await until(() => wire.disconnected, "independent native expiry", 4000);
  } finally { await wire?.close(); await host?.context.close(); await f.stop(); }
});


test("helper installer creates per-user storage for a fresh desktop account and unregisters cleanly", { skip: process.platform !== "linux" }, async () => {
  const folder = await mkdtemp(join(tmpdir(), "evander-helper-installer-"));
  const binary = resolve("helper/bin/Debug/net10.0/evander-browser-helper");
  const data = join(folder, "data"), home = join(folder, "home"), config = join(folder, "config");
  const env = { ...process.env, HOME: home, XDG_CONFIG_HOME: config, XDG_DATA_HOME: data };
  const run = (...args) => promisify(execFile)(binary, args, { env, cwd: folder, timeout: 15000 });
  try {
    await mkdir(home);
    const first = "a".repeat(32), second = "b".repeat(32);
    await run("--install", first, "--browser-data-dir", join(folder, "chrome"));
    const manifest = JSON.parse(await readFile(join(config, "google-chrome/NativeMessagingHosts/com.evander.browser_helper.json"), "utf8"));
    assert.equal(manifest.path, join(data, "Evander/BrowserHelper/evander-browser-helper"));
    await access(manifest.path);
    assert.deepEqual(manifest.allowed_origins, ["chrome-extension://" + first + "/"]);
    await run("--install", second);
    const settings = JSON.parse(await readFile(join(data, "Evander/BrowserHelper/helper.json"), "utf8"));
    assert.equal(settings.browser_data_dir, join(folder, "chrome"));
    assert.deepEqual(new Set(settings.allowed_origins), new Set([first, second].map(id => "chrome-extension://" + id + "/")));
    await run("--uninstall");
    await assert.rejects(access(join(data, "Evander/BrowserHelper")), { code: "ENOENT" });
    await assert.rejects(access(join(config, "google-chrome/NativeMessagingHosts/com.evander.browser_helper.json")), { code: "ENOENT" });
  } finally { await rm(folder, { recursive: true, force: true }); }
});


test("helper drops delayed pixels from an unapproved document after returning to the approved page", async () => {
  await promisify(execFile)(process.env.EVANDER_TEST_PYTHON ?? "python3",
    ["test/helper-frame-regression.py", resolve("helper/bin/Debug/net10.0")], { timeout: 15000 });
});


test("helper observations and streaming fit native messages on a detailed full-HD page", async () => {
  const f = await fixture(); let host;
  try {
    host = await helperHost(f); assert.equal(host.paired.ok, true, host.paired.error);
    const state = await until(async () => { const value = await f.api(host.path + "/state"); return value.state === "paused" && value; }, "helper ready");
    await host.worker.evaluate(() => {
      globalThis.__helperFrameSizes = [];
      const send = WebSocket.prototype.send;
      WebSocket.prototype.send = function (data) {
        try { const message = JSON.parse(data); if (message.kind === "frame") globalThis.__helperFrameSizes.push(message.image.length); } catch {}
        return send.call(this, data);
      };
    });
    await host.application.setViewportSize({ width: 1920, height: 1080 });
    await host.application.evaluate(() => {
      const canvas = document.createElement("canvas"); canvas.width = 1920; canvas.height = 1080;
      Object.assign(canvas.style, { position: "fixed", inset: "0", zIndex: "10000" }); document.body.append(canvas);
      const paint = canvas.getContext("2d"), pixels = paint.createImageData(canvas.width, canvas.height);
      let seed = 123456789;
      for (let offset = 0; offset < pixels.data.length; offset += 4) {
        seed ^= seed << 13; seed ^= seed >>> 17; seed ^= seed << 5;
        pixels.data[offset] = seed & 255; pixels.data[offset + 1] = (seed >>> 8) & 255;
        pixels.data[offset + 2] = (seed >>> 16) & 255; pixels.data[offset + 3] = 255;
      }
      paint.putImageData(pixels, 0, 0);
      // Keep generating frames while the helper adapts capture size. A static
      // canvas can otherwise finish painting before the stream probe starts.
      let tick=0;
      setInterval(()=>{paint.fillStyle=(++tick%2) ? "#ffffff" : "#000000";paint.fillRect(0,0,4,4);},100);
    });
    const debuggerSession = await host.context.newCDPSession(host.application);
    const oversized = await debuggerSession.send("Page.captureScreenshot", { format: "jpeg", quality: 65 });
    assert.ok(oversized.data.length > 1000000, "Fixture must exceed Chrome's native-message byte limit");
    await debuggerSession.detach();
    const observed = await f.api(host.path + "/command", command(state, "observe"));
    assert.ok(observed.image.length > 100000 && observed.image.length <= 800000);
    try {
      await until(() => host.worker.evaluate(() => globalThis.__helperFrameSizes.some(size => size > 100000)), "detailed helper stream");
    } catch (error) {
      const sizes=await host.worker.evaluate(()=>globalThis.__helperFrameSizes.slice(-20));
      const state=await host.send({kind:"state"});
      throw new Error(error.message+" "+JSON.stringify({sizes,connected:state.connected,mode:state.mode,state:state.session?.state}));
    }
    const sizes = await host.worker.evaluate(() => globalThis.__helperFrameSizes);
    assert.ok(sizes.every(size => size <= 800000));
    assert.equal((await host.send({ kind: "state" })).connected, true);
  } finally { await host?.context.close(); await f.stop(); }
});


test("helper observes a wide accessibility tree through its bounded native transport", async () => {
  const f = await fixture(); let host;
  try {
    host = await helperHost(f); assert.equal(host.paired.ok, true, host.paired.error);
    const state = await until(async () => { const value = await f.api(host.path + "/state"); return value.state === "paused" && value; }, "helper ready");
    await host.application.evaluate(() => {
      const group = document.createElement("div");
      for (let index = 0; index < 3000; index++) {
        const button = document.createElement("button"); button.textContent = "Material operation " + index + " " + (index < 600 ? "素材".repeat(200) : "Station control");
        group.append(button);
      }
      document.body.append(group);
    });
    const debuggerSession = await host.context.newCDPSession(host.application);
    const tree = await debuggerSession.send("Accessibility.getFullAXTree");
    assert.ok(Buffer.byteLength(JSON.stringify(tree)) > 4194304, "Fixture must exceed the previous CDP ingress limit");
    await debuggerSession.detach();
    const observed = await f.api(host.path + "/command", command(state, "observe"));
    assert.equal(observed.elements.length, 600);
    assert.ok(observed.elements.some(node => node.name.startsWith("Material operation 0 ")));
    assert.equal((await host.send({ kind: "state" })).connected, true);
    await host.send({ kind: "control", action: "grant" });
    const granted = await f.api(host.path + "/state");
    await f.api(host.path + "/command", command(granted, "click", { observation_id: observed.observation_id,
      ref: observed.elements.find(node => node.name.startsWith("Material operation 0 ")).ref }));
  } finally { await host?.context.close(); await f.stop(); }
});
