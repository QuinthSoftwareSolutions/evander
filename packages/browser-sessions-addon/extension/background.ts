import { BrowserEngine } from "../shared/engine.js";
declare const chrome: any;
let connection: any;
let socket: WebSocket | undefined;
let engine: BrowserEngine | undefined;
let tabId: number | undefined;
let granted = false;
let retry: ReturnType<typeof setTimeout> | undefined;
let chain = Promise.resolve();
let streaming = false;
let heartbeat: ReturnType<typeof setInterval> | undefined;
let expiry: ReturnType<typeof setTimeout> | undefined;
let deniedOrigin: string | undefined;
let mainFrameId: string | undefined;

chrome.action.onClicked.addListener(async (tab: any) => {
  if (!connection) tabId = tab.id;
  await chrome.sidePanel.open({ tabId: tab.id });
});
function send(message: any) {
  if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message));
}
async function cdp(method: string, params: any = {}) {
  if (!tabId) throw new Error("tab_closed");
  return chrome.debugger.sendCommand({ tabId }, method, params);
}
function update(state: any) {
  if (!connection || state.id !== connection.session.id) return;
  connection.session = { ...state, allowed_origins: [connection.approvedOrigin] };
  if (!granted) connection.session.controller = null;
  engine?.update(connection.session);
}
function pauseForOrigin(url: string) {
  if (!connection) return;
  let currentOrigin: string;
  try { currentOrigin = new URL(url).origin; } catch { currentOrigin = "null"; }
  if (currentOrigin === connection.approvedOrigin) return;
  granted = false;
  update({ ...connection.session, controller: null });
  if (!deniedOrigin) send({ kind: "paused" });
  deniedOrigin = currentOrigin;
}
async function api(action: string, body?: any) {
  if (!connection) throw new Error("Share a tab first");
  const response = await fetch(connection.base + "/_browser/guest/" + action, {
    method: body ? "POST" : "GET",
    headers: { "Authorization": "Bearer " + connection.host_token, ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
    credentials: "omit", redirect: "error",
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error ?? "Support session unavailable");
  return result;
}
async function overlay(message: any) {
  if (!tabId) return;
  await chrome.scripting.executeScript({ target: { tabId },
    func: (x: number, y: number) => {
      let dot = document.getElementById("__evander_agent_pointer");
      if (!dot) {
        dot = document.createElement("div");
        dot.id = "__evander_agent_pointer";
        dot.setAttribute("aria-label", "Truman pointer");
        Object.assign(dot.style, { position: "fixed", width: "18px", height: "18px",
          border: "3px solid #aa36ff", borderRadius: "50%", pointerEvents: "none",
          zIndex: "2147483647", transition: "left 120ms, top 120ms", boxShadow: "0 0 0 4px #aa36ff30" });
        document.documentElement.append(dot);
      }
      dot.style.left = String(x - 9) + "px";
      dot.style.top = String(y - 9) + "px";
    }, args: [message.x, message.y] });
}
async function connectEndpoint() {
  if (!connection || !tabId || !engine) return;
  streaming = false;
  const current = connection;
  socket = new WebSocket(connection.base.replace(/^http/, "ws") + "/_browser/extension");
  socket.addEventListener("open", () => send({ kind: "authenticate", version: 1, token: current.endpoint_token }));
  socket.addEventListener("error", () => socket?.close());
  socket.addEventListener("message", (event) => {
    let message: any;
    try { message = JSON.parse(String(event.data)); } catch { socket?.close(); return; }
    if (message.kind === "state") {
      update(message.session);
      if (!streaming) {
        streaming = true;
        void cdp("Page.startScreencast", { format: "jpeg", quality: 65, maxWidth: 1920, maxHeight: 1080 });
        void cdp("Page.getFrameTree").then((tree) => send({ kind: "ready", url: tree.frameTree.frame.url }));
      }
    } else if (message.kind === "pause") {
      granted = false;
      update({ ...connection.session, controller: null });
    } else if (message.kind === "command") {
      chain = chain.then(async () => {
        try {
          const result = await engine!.execute(message.command, message.actor);
          send({ kind: "result", id: message.command.id, result });
        } catch (error: any) {
          send({ kind: "result", id: message.command.id, error: true,
            code: /^[a-z_]+$/.test(error.message) ? error.message : "browser_error" });
        }
      });
    }
  });
  socket.addEventListener("close", (event) => {
    if (connection !== current) return;
    if (event.code === 1008) { void disconnect(false); return; }
    granted = false;
    if (connection) update({ ...connection.session, controller: null });
    if (connection === current && tabId) retry = setTimeout(() => void connectEndpoint(), 3000);
  });
  clearInterval(heartbeat);
  heartbeat = setInterval(() => send({ kind: "heartbeat" }), 20000);
}
async function attach() {
  if (!connection || !tabId) throw new Error("Select the application tab");
  await chrome.debugger.attach({ tabId }, "1.3");
  engine = new BrowserEngine(cdp, connection.session, (message) => {
    send(message); void overlay(message).catch(() => {});
  });
  await cdp("Page.enable");
  mainFrameId = (await cdp("Page.getFrameTree")).frameTree.frame.id;
  await cdp("Accessibility.enable");
  await connectEndpoint();
}
async function disconnect(close = true) {
  const previous = connection;
  if (connection) engine?.update({ ...connection.session, controller: null, control_generation: connection.session.control_generation + 1 });
  connection = undefined; granted = false;
  clearTimeout(retry); clearTimeout(expiry); clearInterval(heartbeat);
  socket?.close(); engine = undefined;
  if (tabId) { try { await chrome.debugger.detach({ tabId }); } catch {} }
  await chrome.storage.session.remove("connection");
  if (close && previous) {
    await fetch(previous.base + "/_browser/guest/close", { method: "POST",
      headers: { "Authorization": "Bearer " + previous.host_token, "Content-Type": "application/json" },
      body: "{}", credentials: "omit", redirect: "error" }).catch(() => {});
  }
}
chrome.debugger.onDetach.addListener((source: any) => {
  if (source.tabId !== tabId || !connection) return;
  granted = false; send({ kind: "paused" });
  connection = undefined; socket?.close(); engine = undefined;
  clearTimeout(retry); clearInterval(heartbeat);
  void chrome.storage.session.remove("connection");
});
chrome.tabs.onRemoved.addListener((id: number) => { if (id === tabId) void disconnect(); });
chrome.debugger.onEvent.addListener(async (source: any, method: string, params: any) => {
  if (source.tabId !== tabId || !connection || !engine) return;
  if (method === "Page.frameNavigated" || method === "Page.navigatedWithinDocument") {
    engine.navigate();
    if (method === "Page.frameNavigated" && !params.frame.parentId) {
      mainFrameId = params.frame.id;
      pauseForOrigin(params.frame.url);
    } else if (method === "Page.navigatedWithinDocument" && params.frameId === mainFrameId) {
      pauseForOrigin(params.url);
    }
  }
  if (method !== "Page.screencastFrame") return;
  try {
    const tree = await cdp("Page.getFrameTree");
    const url = tree.frameTree.frame.url;
    if (new URL(url).origin !== connection.approvedOrigin) {
      pauseForOrigin(url);
      return;
    }
    const metrics = await cdp("Page.getLayoutMetrics");
    const viewport = metrics.cssVisualViewport;
    send({ kind: "frame", image: params.data, width: Math.round(viewport.clientWidth),
      height: Math.round(viewport.clientHeight), page_epoch: String(params.sessionId), url });
  } catch {
    granted = false;
    if (connection) update({ ...connection.session, controller: null });
    send({ kind: "paused" });
  } finally { await cdp("Page.screencastFrameAck", { sessionId: params.sessionId }).catch(() => {}); }
});
chrome.runtime.onMessage.addListener((message: any, _sender: any, respond: any) => {
  // Extension pages only. Web pages have no externally-connectable transport.
  void (async () => {
    if (message.kind === "pair") {
      const invite = new URL(message.invitation);
      if (invite.pathname !== "/_browser/guest/join" || invite.username || invite.password ||
          (invite.protocol !== "https:" && !["localhost", "127.0.0.1"].includes(invite.hostname)))
        throw new Error("Use a valid invitation from your control plane");
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (!tab?.id || !tab.url || !/^https?:/.test(tab.url)) throw new Error("Open the APP tab before sharing");
      const response = await fetch(invite.origin + "/_browser/guest/pair", {
        method: "POST", headers: { "Content-Type": "application/json" },
        credentials: "omit", redirect: "error",
        body: JSON.stringify({ invitation: new URLSearchParams(invite.hash.slice(1)).get("invite"),
          application_url: tab.url }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error ?? "Invitation is unavailable");
      await disconnect(false);
      tabId = tab.id;
      connection = { ...result, base: invite.origin, tabId, approvedOrigin: new URL(tab.url).origin };
      granted = false; deniedOrigin = undefined;
      clearTimeout(expiry);
      expiry = setTimeout(() => void disconnect(false),
        Math.max(1, connection.session.expires_at * 1000 - Date.now()));
      await chrome.storage.session.set({ connection });
      try { await attach(); } catch {
        await disconnect();
        throw new Error("Chrome could not attach to this tab. Close DevTools, check browser policy, and request a new invitation.");
      }
      return { session: connection.session };
    }
    if (message.kind === "state") {
      if (!connection) return { session: null };
      const result = await api("state?after=" + Number(message.after ?? 0));
      update(result.session);
      return { ...result, granted, connected: socket?.readyState === WebSocket.OPEN };
    }
    if (message.kind === "control") {
      granted = message.action === "grant";
      if (!granted && connection) update({ ...connection.session, controller: null });
      try {
        const result = await api("control", { action: message.action,
          expected_generation: connection.session.control_generation });
        update(result); return { session: result };
      } catch (error) { granted = false; throw error; }
    }
    if (message.kind === "message") return api("message", { request_id: crypto.randomUUID(), body: message.body });
    if (message.kind === "voice") {
      const result = await api("voice", {});
      await chrome.tabs.create({ url: result.voice_url });
      return { opened: true };
    }
    if (message.kind === "origin") {
      const tree = await cdp("Page.getFrameTree");
      const url = tree.frameTree.frame.url;
      const approvedOrigin = new URL(url).origin;
      granted = false;
      const result = await api("origin", { url });
      connection.approvedOrigin = approvedOrigin; deniedOrigin = undefined;
      update(result); return { session: result };
    }
    if (message.kind === "disconnect") { await disconnect(); return { session: null }; }
    throw new Error("Unknown extension operation");
  })().then((result) => respond({ ok: true, ...result }), (error) => respond({ ok: false, error: error.message }));
  return true;
});
