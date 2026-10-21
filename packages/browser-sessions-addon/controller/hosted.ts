import { readFile, stat } from "node:fs/promises";
import { createRequire } from "node:module";
import { BrowserEngine } from "../shared/engine.js";

const config = JSON.parse(await readFile(process.env.EVANDER_BROWSER_CONNECTION!, "utf8"));
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.EVANDER_PLAYWRIGHT_MODULE ??
  "/opt/evander/playwright-runtime/node_modules/playwright");
const browser = await chromium.launch({ headless: true, handleSIGTERM: false, handleSIGINT: false,
  executablePath: process.env.PLAYWRIGHT_MCP_EXECUTABLE_PATH || undefined,
  args: ["--disable-dev-shm-usage"] });
let storageState: any = undefined;
try {
  const statePath = process.env.EVANDER_PREVIEW_STATE ?? "/run/preview-output/browser-state.json";
  if ((await stat(statePath)).size > 1048576) throw new Error("state_too_large");
  const candidate = JSON.parse(await readFile(statePath, "utf8"));
  if (!Array.isArray(candidate.cookies) || !Array.isArray(candidate.origins)) throw new Error("invalid_state");
  const hosts = config.allowed_origins.map((value: string) => new URL(value).hostname);
  if (candidate.cookies.some((cookie: any) => !hosts.includes(cookie.domain.replace(/^\./, ""))) ||
      candidate.origins.some((item: any) => !config.allowed_origins.includes(item.origin)))
    throw new Error("state_origin_mismatch");
  storageState = candidate;
} catch (error: any) {
  if (error.code !== "ENOENT") throw new Error("Invalid preview browser-state handoff");
}
const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, storageState,
  acceptDownloads: false });
await context.route("**/*", async (route: any) => {
  const request = route.request();
  if (request.isNavigationRequest() && !config.allowed_origins.includes(new URL(request.url()).origin)) {
    await route.abort(); return;
  }
  await route.continue();
});
const page = await context.newPage();
const cdp = await context.newCDPSession(page);
let socket: WebSocket | undefined;
let shuttingDown = false;
for (const signal of ["SIGTERM", "SIGINT"] as const) process.on(signal, () => {
  shuttingDown = true;
  socket?.close();
  void browser.close().finally(() => process.exit(0));
});
let state = config.session;
const send = (value: any) => {
  if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(value));
};
const engine = new BrowserEngine((method, params) => cdp.send(method, params), state, send);
cdp.on("Page.frameNavigated", () => engine.navigate());
cdp.on("Page.navigatedWithinDocument", () => engine.navigate());
cdp.on("Page.screencastFrame", async (event: any) => {
  try {
    const metrics = await cdp.send("Page.getLayoutMetrics");
    const viewport = metrics.cssVisualViewport;
    send({ kind: "frame", image: event.data, width: Math.round(viewport.clientWidth),
      height: Math.round(viewport.clientHeight), page_epoch: String(event.sessionId), url: page.url() });
  } finally { await cdp.send("Page.screencastFrameAck", { sessionId: event.sessionId }); }
});
await cdp.send("Page.enable");
await page.goto(config.entry_url, { waitUntil: "domcontentloaded", timeout: 60000 });
let commandChain = Promise.resolve();
let streaming = false;
async function join() {
  streaming = false;
  socket = new WebSocket(config.relay_url.replace(/^http/, "ws"));
  socket.addEventListener("open", () => send({ kind: "authenticate", version: 1, token: config.endpoint_token, features:{fast_execution:1} }));
  socket.addEventListener("error", () => socket?.close());
  socket.addEventListener("message", (event) => {
    let message: any;
    try { message = JSON.parse(String(event.data)); } catch { socket?.close(); return; }
    if (message.kind === "state") {
      state = message.session; engine.update(state);
      if (!streaming) {
        streaming = true;
        send({ kind: "ready", url: page.url() });
        void cdp.send("Page.startScreencast", { format: "jpeg", quality: 65, maxWidth: 1920, maxHeight: 1080 });
      }
    } else if (message.kind === "pause") {
      state = { ...state, controller: null, control_generation: state.control_generation + 1 };
      engine.update(state);
    } else if (message.kind === "command") {
      commandChain = commandChain.then(async () => {
        try {
          const result = await engine.execute(message.command, message.actor);
          send({ kind: "result", id: message.command.id, result });
        } catch (error: any) {
          send({ kind: "result", id: message.command.id, error: true,
            code: /^[a-z_]+$/.test(error.message) ? error.message : "browser_error" });
        }
      });
    }
  });
  return new Promise<void>((resolve) => socket!.addEventListener("close", () => {
    state = { ...state, controller: null, control_generation: state.control_generation + 1 };
    engine.update(state); resolve();
  }));
}
const deadline = setTimeout(() => void browser.close().finally(() => process.exit(0)),
  Math.max(1, config.session.expires_at * 1000 - Date.now()));
const heartbeat = setInterval(() => send({ kind: "heartbeat" }), 20000);
try {
  while (!shuttingDown && Date.now() / 1000 < config.session.expires_at) {
    await join();
    await new Promise((resolve) => setTimeout(resolve, 3000));
  }
} finally { clearInterval(heartbeat); clearTimeout(deadline); await browser.close(); }
