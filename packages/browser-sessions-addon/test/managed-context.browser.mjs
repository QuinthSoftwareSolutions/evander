import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { createServer as createHttpsServer } from "node:https";
import { createHash, X509Certificate } from "node:crypto";
import { connect } from "node:net";
import { once } from "node:events";
import { spawn, execFileSync } from "node:child_process";
import { mkdtemp, readFile, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createRequire } from "node:module";
import { chromium } from "playwright";
import { WebSocketServer } from "ws";

const require = createRequire(import.meta.url);
const { createManagedWebsiteContext } = await import("../../../src/evander/assets/playwright-launch.mjs");
async function until(read, label, milliseconds = 10000) {
  const deadline = Date.now() + milliseconds;
  while (Date.now() < deadline) {
    const value = await read();
    if (value) return value;
    await new Promise(resolve => setTimeout(resolve, 20));
  }
  throw new Error("Timed out: " + label);
}
async function bounded(promise, label, milliseconds = 5000) {
  let timer;
  try {
    return await Promise.race([promise, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error("Timed out: " + label)), milliseconds);
    })]);
  } finally { clearTimeout(timer); }
}

for (const { authenticatedProxy, nativeLogin, secure } of [{authenticatedProxy:false}, {authenticatedProxy:true}, {authenticatedProxy:true,nativeLogin:true}, {authenticatedProxy:true,secure:true}]) {
  test(`managed shared context navigates and hands back control${authenticatedProxy ? " through an authenticated proxy" : ""}${nativeLogin ? " with controller login" : ""}${secure ? " using HTTPS CONNECT" : ""}`, { timeout: 30000 }, async () => {
    const trace = message => { if (process.env.EVANDER_TEST_TRACE) console.error(authenticatedProxy, message); };
    let controller, agent, relay, proxy, tlsServer;
    let connectChallenges = 0, authorizedConnects = 0, challengeNextConnect = false;
    let applicationProxyHeaders = 0;
    let escapedRequests = 0;
    const denied = createServer((_request, response) => { escapedRequests++; response.end("Unexpected navigation"); });
    denied.listen(0, "127.0.0.1"); await once(denied, "listening");
    const deniedOrigin = "http://127.0.0.1:" + denied.address().port;
    const sockets = new Set();
    const results = new Map();
    let ready = false, controllerError = "", clicks = 0;
    const serve = (request, response) => {
      if (request.headers["proxy-authorization"]) applicationProxyHeaders++;
      if (request.url.startsWith("/session/")) {
        response.setHeader("Content-Type", "application/json");
        response.end(JSON.stringify({origin,expiresAt:Date.now()/1000+60,storageState:{cookies:[{name:"signed-in",value:"yes",url:origin,httpOnly:true}],origins:[{origin,localStorage:[{name:"identity",value:"verified"}]}]},sessionStorage:{role:"managed"}}));
        return;
      }
      if (request.url === "/identity") { response.end(request.headers.cookie?.includes("signed-in=yes") ? "signed-in" : "anonymous"); return; }
      if (request.url === "/redirect") { response.writeHead(302, { Location: deniedOrigin + "/escaped" }); response.end(); return; }
      response.setHeader("Content-Type", "text/html");
      if (request.url === "/clicked") { clicks++; response.end("ok"); return; }
      response.end('<h1 id="identity"></h1><button onclick="fetch(\'/clicked\')">Run</button><script>document.querySelector("#identity").textContent=localStorage.getItem("identity")==="verified"&&sessionStorage.getItem("role")==="managed"?"Signed in":"Login required"</script>');
    };
    const server = createServer(serve);
    server.listen(0, "127.0.0.1");
    await once(server, "listening");
    const relayOrigin = "http://127.0.0.1:" + server.address().port;
    let origin = relayOrigin;
    const wsServer = new WebSocketServer({ server, path: "/relay" });
    let state = { id: "browser-managed", agent_session_id: "agent-one", controller: "agent:agent-one", control_generation: 0, expires_at: Date.now() / 1000 + 60, allowed_origins: [origin] };
    wsServer.on("connection", socket => {
      relay = socket;
      socket.on("message", raw => {
        const message = JSON.parse(String(raw));
        if (message.kind === "authenticate") socket.send(JSON.stringify({ kind: "state", session: state }));
        if (message.kind === "ready") ready = true;
        if (message.kind === "result") results.set(message.id, message);
      });
    });
    const probe = createServer(); probe.listen(0, "127.0.0.1"); await once(probe, "listening");
    const controlPort = probe.address().port; await new Promise(resolve => probe.close(resolve));
    const folder = await mkdtemp(join(tmpdir(), "evander-managed-context-"));
    const proxyAuth = "Basic " + Buffer.from("proxy-user:proxy-proof").toString("base64");
    let proxyConfig, chromiumExecutable;
    try {
      if (secure) {
        // Generate a disposable fixture identity, then trust only its exact
        // public key in this test browser. Production TLS settings are unchanged.
        const keyPath = join(folder, "localhost.key"), certPath = join(folder, "localhost.crt");
        execFileSync("openssl", ["req", "-x509", "-newkey", "rsa:2048", "-nodes", "-sha256", "-days", "1",
          "-subj", "/CN=evander-fixture.local", "-addext", "subjectAltName=IP:127.0.0.1",
          "-keyout", keyPath, "-out", certPath], {stdio: "ignore", timeout: 5000});
        const key = await readFile(keyPath), cert = await readFile(certPath);
        const spki = createHash("sha256").update(new X509Certificate(cert).publicKey.export({format: "der", type: "spki"})).digest("base64");
        chromiumExecutable = join(folder, "fixture-chromium");
        const quotedChromium = "'" + chromium.executablePath().replaceAll("'", "'\"'\"'") + "'";
        await writeFile(chromiumExecutable, `#!/bin/sh\nexec ${quotedChromium} --ignore-certificate-errors-spki-list=${spki} "$@"\n`, {mode: 0o700});
        tlsServer = createHttpsServer({key, cert}, serve);
        tlsServer.listen(0, "127.0.0.1"); await once(tlsServer, "listening");
        origin = "https://127.0.0.1:" + tlsServer.address().port;
        state = {...state, allowed_origins: [origin]};
      }
      if (authenticatedProxy) {
        proxy = createServer((request, response) => {
          trace("proxy request " + request.url + " authorized=" + (request.headers["proxy-authorization"] === proxyAuth));
          if (request.headers["proxy-authorization"] !== proxyAuth) {
            response.writeHead(407, { "Proxy-Authenticate": 'Basic realm="fixture"' }); response.end(); return;
          }
          const target = new URL(request.url);
          if (target.origin !== origin) { response.writeHead(403); response.end(); return; }
          fetch(target, { redirect: "manual", headers: {cookie: request.headers.cookie ?? ""} }).then(async upstream => {
            response.writeHead(upstream.status, { "Content-Type": upstream.headers.get("content-type") ?? "text/plain", ...(upstream.headers.has("location") ? {Location: upstream.headers.get("location")} : {}) });
            response.end(Buffer.from(await upstream.arrayBuffer()));
          }).catch(() => { response.writeHead(502); response.end(); });
        });
        proxy.on("connect", (request, socket, head) => {
          sockets.add(socket); socket.on("close", () => sockets.delete(socket));
          if (request.url !== new URL(origin).host) { socket.end("HTTP/1.1 403 Forbidden\r\n\r\n"); return; }
          const forceChallenge = challengeNextConnect;
          challengeNextConnect = false;
          if (request.headers["proxy-authorization"] !== proxyAuth || forceChallenge) {
            connectChallenges++;
            socket.end('HTTP/1.1 407 Proxy Authentication Required\r\nProxy-Authenticate: Basic realm="fixture"\r\nContent-Length: 0\r\n\r\n'); return;
          }
          authorizedConnects++;
          const upstream = connect(new URL(origin).port, "127.0.0.1", () => {
            socket.write("HTTP/1.1 200 Connection Established\r\n\r\n"); if (head.length) upstream.write(head);
            socket.pipe(upstream); upstream.pipe(socket);
          });
          sockets.add(upstream); upstream.on("close", () => sockets.delete(upstream));
          upstream.on("error", () => socket.destroy()); socket.on("error", () => upstream.destroy());
        });
        proxy.listen(0, "127.0.0.1"); await once(proxy, "listening");
        proxyConfig = { server: "http://127.0.0.1:" + proxy.address().port, username: "proxy-user", password: "proxy-proof", bypass: "<-loopback>" };
      }
      const config = { session: state, entry_url: origin + "/login", allowed_origins: [origin], endpoint_token: "worker-proof", relay_url: relayOrigin + "/relay", control_port: controlPort, playwright_enabled: true, website_session:{relay_url:relayOrigin,session_id:"proxy~browser-managed",token:"proof",aliases:["ui"]}, ...(proxyConfig ? { proxy: proxyConfig } : {}) };
      await writeFile(join(folder, "connection.json"), JSON.stringify(config));
      controller = spawn(process.execPath, ["evander_browser_sessions_addon/controller/hosted.mjs"], {
        env: { ...process.env, EVANDER_BROWSER_CONNECTION: join(folder, "connection.json"), EVANDER_PREVIEW_STATE: join(folder, "missing-state.json"), EVANDER_PLAYWRIGHT_MODULE: require.resolve("playwright"), ...(chromiumExecutable ? {PLAYWRIGHT_MCP_EXECUTABLE_PATH: chromiumExecutable} : {}) },
        stdio: ["ignore", "ignore", "pipe"], detached: true,
      });
      controller.stderr.on("data", data => controllerError += data);
      await until(() => ready, "hosted browser ready").catch(error => { throw new Error(error.message + "; " + controllerError); });
      trace("hosted ready");
      relay.send(JSON.stringify({ kind: "command", actor: "agent:agent-one", command: { id: "initial-page", action: "observe", generation: 0, include_image: false } }));
      const initial = await until(() => results.get("initial-page"), "initial page");
      trace("initial page: " + JSON.stringify(initial));
      if (nativeLogin) {
        relay.send(JSON.stringify({kind:"command",actor:"agent:agent-one",command:{id:"native-login",action:"authenticate",target_alias:"ui",generation:0}}));
        const login=await until(()=>results.get("native-login"),"controller-managed login");
        assert.equal(login.error,undefined,JSON.stringify(login));
      }
      const control = async body => {
        const response = await fetch(`http://127.0.0.1:${controlPort}/control`, { method: "POST", headers: { Authorization: "Bearer worker-proof", "Content-Type": "application/json" }, body: JSON.stringify(body), signal: AbortSignal.timeout(12000) });
        assert.equal(response.status, 200, await response.text());
      };
      const nativeCommand = async (action, extra = {}) => {
        const id = crypto.randomUUID();
        relay.send(JSON.stringify({kind: "command", actor: state.controller, command: {id, action, generation: state.control_generation, ...extra}}));
        return until(() => results.get(id), "native " + action, 5000);
      };
      await control({ session: state, automation_token: "agent-proof" });
      trace("descriptor granted");
      agent = await chromium.connectOverCDP(`ws://127.0.0.1:${controlPort}/connect`, { headers: { Authorization: "Bearer agent-proof", "X-Evander-Control-Generation": "0" }, timeout: 5000 });
      trace("agent connected");
      let page;
      if (nativeLogin) {
        page=agent.contexts().flatMap(context=>context.pages()).find(page=>page.url()===origin+"/");
        assert.ok(page,"controller-created authenticated page remains visible to Playwright");
      } else {
        // Mirror connectSharedChromium's host-owned proxy injection while using
        // the real broker helper for cookies, localStorage and sessionStorage.
        const newContext = agent.newContext.bind(agent);
        agent.newContext = options => newContext({...options, serviceWorkers: "block", ...(proxyConfig ? {proxy: proxyConfig} : {})});
        const previous = Object.fromEntries(["HTTP_TARGET_RELAY_BASE_URL", "EVANDER_SESSION_ID", "EVANDER_MCP_SESSION_TOKEN"].map(key => [key, process.env[key]]));
        let context;
        try {
          Object.assign(process.env, {HTTP_TARGET_RELAY_BASE_URL: relayOrigin, EVANDER_SESSION_ID: "agent-one", EVANDER_MCP_SESSION_TOKEN: "job-proof"});
          context = await bounded(createManagedWebsiteContext(agent, "ui"), "managed context");
        } finally {
          for (const [key, value] of Object.entries(previous)) {
            if (value === undefined) delete process.env[key]; else process.env[key] = value;
          }
        }
        trace("managed context created");
        page = await bounded(context.newPage(), "managed page");
      }
      page.on("console", message => trace("console " + message.type() + " " + message.text()));
      page.on("requestfailed", request => trace("request failed " + request.url() + " " + JSON.stringify(request.failure())));
      const response = await page.goto(origin + "/application", { waitUntil: "domcontentloaded", timeout: 5000 });
      trace("navigation done: " + response.status() + " " + (await page.content()).slice(0,300));
      assert.equal(response.status(), 200);
      assert.equal(await page.locator("h1").textContent({ timeout: 3000 }), "Signed in");
      assert.equal(await page.evaluate(() => fetch("/identity").then(response => response.text())), "signed-in");
      const original = agent.contexts().flatMap(context => context.pages()).find(page => page.url() === origin + "/login");
      assert.ok(original);
      assert.equal(await original.locator("h1").textContent({timeout: 3000}), "Login required");
      assert.equal(await original.evaluate(() => fetch("/identity").then(response => response.text())), "anonymous");
      if (!authenticatedProxy) {
        const disposableContext = await agent.newContext();
        const closingPage = await disposableContext.newPage();
        const closingInput = await disposableContext.newCDPSession(closingPage);
        const closingId = await until(async () => {
          const listed = await nativeCommand("pages");
          return listed.result.pages.find(item => item.url === "about:blank")?.id;
        }, "disposable target registered");
        assert.equal((await nativeCommand("select_page", {page_id: initial.result.page_id})).error, undefined);
        // A closing target can invalidate focus requests already queued behind
        // the original page's stop-screencast call. Losing that target must not
        // terminate the controller or conceal explicit invalid-page errors.
        await bounded(Promise.allSettled([
          ...Array.from({length: 16}, (_, x) => closingInput.send("Input.dispatchMouseEvent", {type: "mouseMoved", x, y: 1})),
          closingInput.send("Page.close"),
        ]), "closing target with queued focus", 3000);
        await until(() => closingPage.isClosed(), "disposable target closed");
        assert.equal((await nativeCommand("select_page", {page_id: initial.result.page_id})).error, undefined);
        const rejected = await nativeCommand("select_page", {page_id: closingId});
        assert.equal(rejected.error, true);
        assert.equal(rejected.code, "page_unavailable");
        await disposableContext.close();
      }
      if (secure) {
        // Two in-flight input requests can both attempt to select the managed
        // tab while the initial tab's stop-screencast acknowledgement is pending.
        relay.send(JSON.stringify({kind: "command", actor: "agent:agent-one", command: {id: "select-initial", action: "select_page", page_id: initial.result.page_id, generation: 0}}));
        const selected = await until(() => results.get("select-initial"), "initial tab selection");
        assert.equal(selected.error, undefined);
        const managedPage = selected.result.pages.find(item => item.url === origin + "/application");
        assert.ok(managedPage);
        const input = await page.context().newCDPSession(page);
        await bounded(Promise.all([1, 2].map(x => input.send("Input.dispatchMouseEvent", {type: "mouseMoved", x, y: 1}))), "concurrent tab focus");
        await bounded(input.detach(), "input session detach");
        relay.send(JSON.stringify({kind: "command", actor: "agent:agent-one", command: {id: "select-managed", action: "select_page", page_id: managedPage.id, generation: 0}}));
        const managedSelected = await until(() => results.get("select-managed"), "managed tab selection after concurrent input");
        assert.equal(managedSelected.error, undefined);
      }
      await page.bringToFront();
      await page.getByRole("button", { name: "Run", exact: true }).click({ timeout: 3000 });
      await until(() => clicks === 1, "one confirmed click");
      const disconnected = once(agent, "disconnected");
      state = { ...state, controller: "user:operator", control_generation: 1 };
      await control({ session: state });
      await disconnected;
      assert.equal(agent.isConnected(), false);
      const challengesBeforeDetach = connectChallenges;
      if (secure) {
        assert.ok(connectChallenges > 0, "HTTPS must challenge for delegated proxy proof");
        assert.ok(authorizedConnects > 0, "HTTPS must use an authenticated CONNECT tunnel");
        // Require another challenge with no agent CDP client present, rather
        // than silently reusing its authenticated tunnel for retained navigation.
        challengeNextConnect = true;
        for (const socket of sockets) socket.destroy();
      }
      relay.send(JSON.stringify({ kind: "command", actor: "user:operator", command: { id: "pages-after-takeover", action: "pages", generation: 1 } }));
      const pages = await until(() => results.get("pages-after-takeover"), "retained pages");
      assert.equal(pages.error, undefined);
      const retained = pages.result.pages.find(item => item.url === origin + "/application");
      assert.ok(retained);
      // The controller can navigate a context originally created by the agent
      // after that agent's CDP authentication handlers have disconnected.
      relay.send(JSON.stringify({kind: "command", actor: "user:operator", command: {id: "human-navigation", action: "navigate", page_id: retained.id, url: origin + "/retained", generation: 1}}));
      const humanNavigation = await until(() => results.get("human-navigation"), "human navigation");
      assert.equal(humanNavigation.error, undefined, JSON.stringify(humanNavigation));
      if (secure) assert.ok(connectChallenges > challengesBeforeDetach, "controller navigation must survive a fresh CONNECT challenge after agent detach");
      state = {...state, controller: "agent:agent-one", control_generation: 2};
      await control({session: state, automation_token: "fresh-agent-proof"});
      agent = await chromium.connectOverCDP(`ws://127.0.0.1:${controlPort}/connect`, {headers: {Authorization: "Bearer fresh-agent-proof", "X-Evander-Control-Generation": "2"}, timeout: 5000});
      const resumed = agent.contexts().flatMap(context => context.pages()).find(page => page.url() === origin + "/retained");
      assert.ok(resumed);
      assert.equal(await resumed.locator("h1").textContent({timeout: 3000}), "Signed in");
      assert.equal(await resumed.evaluate(() => fetch("/identity").then(response => response.text())), "signed-in");
      assert.equal(clicks, 1);
      await assert.rejects(resumed.goto(deniedOrigin + "/direct", {waitUntil: "domcontentloaded", timeout: 5000}), /Evander browser policy denied/);
      assert.equal(escapedRequests, 0);
      if (authenticatedProxy && !secure) {
        // The delegated proxy independently enforces destinations after an
        // allowed page redirects or opens a popup before its Page is available.
        const redirected = await resumed.goto(origin + "/redirect", {waitUntil: "domcontentloaded", timeout: 5000});
        assert.equal(redirected.status(), 403);
        assert.equal(escapedRequests, 0);
      }
      assert.equal(applicationProxyHeaders, 0, "delegated proxy proof must not become an application request header");
      assert.equal(controllerError, "");
    } catch (error) {
      trace(String(error));
      trace(controllerError);
      throw error;
    } finally {
      trace("cleanup starts");
      if (controller?.exitCode === null) {
        try { process.kill(-controller.pid, "SIGTERM"); } catch {}
        await Promise.race([once(controller, "exit"), new Promise(resolve => setTimeout(resolve, 1000))]);
        try { process.kill(-controller.pid, "SIGKILL"); } catch {}
      }
      await agent?.close().catch(() => {});
      trace("agent closed");
      for (const socket of wsServer.clients) socket.terminate();
      await new Promise(resolve => wsServer.close(resolve));
      for (const socket of sockets) socket.destroy();
      if (proxy) { proxy.closeAllConnections(); await new Promise(resolve => proxy.close(resolve)); }
      trace("proxy closed");
      if (tlsServer) { tlsServer.closeAllConnections(); await new Promise(resolve => tlsServer.close(resolve)); }
      server.closeAllConnections(); await new Promise(resolve => server.close(resolve));
      denied.closeAllConnections(); await new Promise(resolve => denied.close(resolve));
      await rm(folder, { recursive: true, force: true });
      trace("cleanup done");
    }
  });
}
