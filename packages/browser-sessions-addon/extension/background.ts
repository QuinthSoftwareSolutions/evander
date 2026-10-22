import { BrowserEngine } from "../shared/engine.js";
import { CompatibleTab, extensionConflict } from "./compatible-tab.js";
import { NativeTab } from "./native-tab.js";
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
let compatible: CompatibleTab | undefined;
let native: NativeTab | undefined;
let frameTimer: ReturnType<typeof setInterval> | undefined;
let upgrading: Promise<void> | undefined;

chrome.action.onClicked.addListener(async (tab: any) => {
  if (!connection) tabId = tab.id;
  await chrome.sidePanel.open({ tabId: tab.id });
});
function send(message: any) {
  if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message));
}
async function cdp(method: string, params: any = {}) {
  if (!tabId) throw new Error("tab_closed");
  if (native) return native.send(method, params);
  if (compatible) return compatible.send(method, params);
  try { return await chrome.debugger.sendCommand({ tabId }, method, params); }
  catch (error) {
    if (connection && extensionConflict(error)) {
      await useCompatibility(true);
      throw new Error("stale_control"); // Never replay an action whose outcome is unknown.
    }
    throw error;
  }
}
function update(state: any) {
  if (!connection || state.id !== connection.session.id ||
      state.control_generation < connection.session.control_generation) return;
  connection.session = { ...state, allowed_origins: [connection.approvedOrigin] };
  if (!granted) connection.session.controller = null;
  engine?.update(connection.session);
  native?.update(connection.session);
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
  void compatible?.suspend();
}
async function api(action: string, body?: any) {
  if (!connection) throw new Error("Share a tab first");
  const current = connection;
  const response = await fetch(current.base + "/_browser/guest/" + action, {
    method: body ? "POST" : "GET",
    headers: { "Authorization": "Bearer " + current.host_token, ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
    credentials: "omit", redirect: "error",
  });
  const result = await response.json();
  if (connection !== current) throw new Error("The sharing session changed. Try again.");
  if (!response.ok) {
    const error = new Error(result.error ?? "Support session unavailable") as Error & { status: number };
    error.status = response.status; throw error;
  }
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
        dot.setAttribute("aria-label", "Agent pointer");
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
  socket?.close();
  const endpointSocket = new WebSocket(connection.base.replace(/^http/, "ws") + "/_browser/extension");
  socket = endpointSocket;
  endpointSocket.addEventListener("open", () => {
    if (connection === current && socket === endpointSocket)
      endpointSocket.send(JSON.stringify({ kind: "authenticate", version: 1, token: current.endpoint_token, features:(!native || native.fastExecution) ? {fast_execution:1} : {} }));
  });
  endpointSocket.addEventListener("error", () => endpointSocket.close());
  endpointSocket.addEventListener("message", (event) => {
    if (connection !== current || socket !== endpointSocket) return;
    let message: any;
    try { message = JSON.parse(String(event.data)); } catch { socket?.close(); return; }
    if (message.kind === "state") {
      if (connection !== current) return;
      update(message.session);
      if (compatible && !streaming) {
        streaming = true;
        void cdp("Page.getFrameTree").then((tree) => {
          if (connection === current) send({ kind: "ready", url: tree.frameTree.frame.url });
        }).catch(() => {});
      } else if (!streaming) {
        streaming = true;
        void cdp("Page.startScreencast", { format: "jpeg", quality: 65, maxWidth: 1920, maxHeight: 1080 });
        void cdp("Page.getFrameTree").then((tree) => send({ kind: "ready", url: tree.frameTree.frame.url }));
      }
    } else if (message.kind === "pause") {
      granted = false;
      update({ ...connection.session, controller: null });
    } else if (message.kind === "command") {
      const endpoint = engine;
      chain = chain.then(async () => {
        try {
          if (connection !== current || engine !== endpoint) throw new Error("disconnected");
          if (native) native.commandDeadline = message.command.deadline_at;
          const result = await endpoint!.execute(message.command, message.actor);
          if (connection !== current) return;
          send({ kind: "result", id: message.command.id, result });
        } catch (error: any) {
          if (connection !== current) return;
          send({ kind: "result", id: message.command.id, error: true,
            code: /^[a-z_]+$/.test(error.message) ? error.message : "browser_error" });
        } finally { if (native) native.commandDeadline = undefined; }
      });
    }
  });
  endpointSocket.addEventListener("close", (event) => {
    if (connection !== current || socket !== endpointSocket) return;
    if (event.code === 1008) { void disconnect(false); return; }
    granted = false;
    if (connection) update({ ...connection.session, controller: null });
    if (connection === current && tabId) retry = setTimeout(() => void connectEndpoint(), 3000);
  });
  clearInterval(heartbeat);
  heartbeat = setInterval(() => send({ kind: "heartbeat" }), 20000);
}
function createEngine(current: any) {
  return new BrowserEngine(async (method, params) => {
    if (connection !== current) throw new Error("disconnected");
    const result = await cdp(method, params);
    if (connection !== current) throw new Error("disconnected");
    return result;
  }, current.session, message => {
    if (connection !== current) return;
    send(message); void overlay(message).catch(() => {});
  });
}
async function useCompatibility(pause = false) {
  if (upgrading) return upgrading;
  const current = connection, selected = tabId;
  if (!current || !selected) throw new Error("disconnected");
  upgrading = (async () => {
    if (pause) {
      granted = false; update({ ...current.session, controller: null }); send({ kind: "paused" });
    }
    compatible = new CompatibleTab(selected, () => current.approvedOrigin, () => connection === current,
      () => engine?.navigate());
    // Detach only our own debugger; leave every other extension and frame intact.
    try { await chrome.debugger.detach({ tabId: selected }); } catch {}
    engine = createEngine(current);
    await compatible.start();
    if (connection !== current) throw new Error("disconnected");
    current.mode = "compatibility";
    await chrome.storage.session.set({ connection: current });
    clearInterval(frameTimer);
    let busy = false;
    frameTimer = setInterval(() => {
      if (busy || connection !== current || socket?.readyState !== WebSocket.OPEN || deniedOrigin) return;
      busy = true;
      void (async () => {
        const tree = await cdp("Page.getFrameTree");
        const image = await cdp("Page.captureScreenshot");
        const metrics = await cdp("Page.getLayoutMetrics");
        if (connection !== current || deniedOrigin) return;
        send({ kind: "frame", image: image.data, width: Math.round(metrics.cssVisualViewport.clientWidth),
          height: Math.round(metrics.cssVisualViewport.clientHeight), page_epoch: "compatible", url: tree.frameTree.frame.url });
      })().catch(() => {
        // No pixel or input access when the tab leaves the approved origin.
        if (connection === current) {
          granted = false; update({ ...current.session, controller: null }); send({ kind: "paused" });
        }
      }).finally(() => { busy = false; });
    }, 1000);
  })();
  try { await upgrading; } finally { upgrading = undefined; }
}
async function attach() {
  if (!connection || !tabId) throw new Error("Select the application tab");
  let step = "attaching Chrome to the tab";
  try {
    if (connection.mode === "helper") {
      step = "connecting the local helper to Chrome";
      const current = connection, selected = tabId;
      const target = (await chrome.debugger.getTargets()).find((value: any) => value.tabId === selected && value.type === "page");
      if (!target) throw new Error("Chrome could not locate the selected application tab.");
      native = new NativeTab(current.session,
        (method, params) => void browserEvent({ tabId: selected }, method, params),
        () => engine?.navigate(),
        () => { if (connection === current) void disconnect(); });
      engine = createEngine(current);
      await native.open(target.id);
    } else try { await chrome.debugger.attach({ tabId }, "1.3"); }
    catch (error) {
      if (!extensionConflict(error)) throw error;
      step = "starting extension-compatible sharing";
      await useCompatibility();
      await connectEndpoint(); return;
    }
    step = "preparing the shared browser";
    engine = createEngine(connection);
    step = "enabling page access";
    await cdp("Page.enable");
    step = "reading the page frame";
    mainFrameId = (await cdp("Page.getFrameTree")).frameTree.frame.id;
    step = "enabling accessibility";
    await cdp("Accessibility.enable");
    step = "connecting to Evander";
    await connectEndpoint();
  } catch (error: any) {
    const reason = String(error?.message ?? error ?? "Unknown browser error").replace(/\s+/g, " ").slice(0, 400);
    throw new Error(`Browser setup failed while ${step}: ${reason}`);
  }
}
async function disconnect(close = true) {
  const previous = connection;
  if (connection) engine?.update({ ...connection.session, controller: null, control_generation: connection.session.control_generation + 1 });
  connection = undefined; granted = false;
  clearTimeout(retry); clearTimeout(expiry); clearInterval(heartbeat); clearInterval(frameTimer);
  socket?.close(); engine = undefined;
  const local = native; native = undefined; local?.close();
  const capture = compatible; compatible = undefined;
  await capture?.stop();
  if (tabId) { try { await chrome.debugger.detach({ tabId }); } catch {} }
  await chrome.storage.session.remove("connection");
  if (close && previous) {
    await fetch(previous.base + "/_browser/guest/close", { method: "POST",
      headers: { "Authorization": "Bearer " + previous.host_token, "Content-Type": "application/json" },
      body: "{}", credentials: "omit", redirect: "error" }).catch(() => {});
  }
}
chrome.debugger.onDetach.addListener((source: any, reason: string) => {
  if (source.tabId !== tabId || !connection || compatible || native) return;
  const current = connection;
  granted = false; update({ ...current.session, controller: null }); send({ kind: "paused" });
  engine?.navigate();
  void (async () => {
    // Chrome also reports target_closed when a protected extension frame appears.
    // Only that automatic detach can attempt reattachment; user cancellation stops sharing.
    const tab = reason === "target_closed" ? await chrome.tabs.get(source.tabId).catch(() => null) : null;
    if (connection !== current) return;
    if (tab?.url && new URL(tab.url).origin === current.approvedOrigin) {
      try { await attach(); return; } catch {}
    }
    if (connection === current) await disconnect();
  })();
});
chrome.tabs.onRemoved.addListener((id: number) => { if (id === tabId) void disconnect(); });
chrome.tabs.onUpdated.addListener((id: number, change: any, tab: any) => {
  if (id !== tabId || !connection || !compatible) return;
  if (change.status === "loading" || change.url) compatible.navigate();
  if (change.url || change.status === "loading") pauseForOrigin(tab.url ?? "about:blank");
});
chrome.debugger.onEvent.addListener((source: any, method: string, params: any) => {
  if (!native) void browserEvent(source, method, params);
});
async function browserEvent(source: any, method: string, params: any) {
  if (source.tabId !== tabId || !connection || !engine || compatible) return;
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
}
chrome.runtime.onMessage.addListener((message: any, _sender: any, respond: any) => {
  if (message.kind === "microphone_permission_result" || message.target === "tab-capture") return false;
  // Extension pages only. Web pages have no externally-connectable transport.
  void (async () => {
    if (message.kind === "pair") {
      const invite = new URL(message.invitation);
      if (invite.pathname !== "/_browser/guest/join" || invite.username || invite.password ||
          (invite.protocol !== "https:" && !["localhost", "127.0.0.1"].includes(invite.hostname)))
        throw new Error("Use a valid invitation from your control plane");
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (!tab?.id) throw new Error("Open the application tab before sharing");
      if (!tab.url) throw new Error(
        "Chrome could not read this tab's address. Select the application tab, click the Evander Browser Assistance toolbar icon to grant access, then try again.");
      if (!/^https?:/.test(tab.url)) throw new Error(
        "This page cannot be shared. Open an application page with an http:// or https:// address.");
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
      connection = { ...result, base: invite.origin, tabId, approvedOrigin: new URL(tab.url).origin,
        mode: message.mode === "helper" ? "helper" : "native" };
      granted = false; deniedOrigin = undefined;
      clearTimeout(expiry);
      expiry = setTimeout(() => void disconnect(false),
        Math.max(1, connection.session.expires_at * 1000 - Date.now()));
      await chrome.storage.session.set({ connection });
      try { await attach(); } catch (error: any) {
        await disconnect();
        throw new Error(`${error.message} Request a new invitation after resolving this error.`);
      }
      return { session: connection.session };
    }
    if (message.kind === "state") {
      if (!connection) return { session: null };
      const after = "browser_session_id" in message && message.browser_session_id !== connection.session.id
        ? 0 : Number(message.after ?? 0);
      const result = await api("state?after=" + after);
      update(result.session);
      return { ...result, granted, mode: connection.mode ?? "native", connected: socket?.readyState === WebSocket.OPEN };
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
    if (message.kind === "cancel-run") {
      granted=false;
      if (connection) update({...connection.session,controller:null});
      return {run:await api("cancel-run",{})};
    }
    if (message.kind === "message") return api("message", { request_id: crypto.randomUUID(), body: message.body });
    if (message.kind === "retry-message") return api("retry-message", {request_id:message.request_id,message_id:message.message_id});
    if (message.kind === "voice") {
      if (_sender.id !== chrome.runtime.id ||
          _sender.url?.split(/[?#]/)[0] !== chrome.runtime.getURL("panel.html"))
        throw new Error("Open voice controls from the sharing sidebar.");
      if (!connection || message.browser_session_id !== connection.session.id)
        throw new Error("The sharing session changed. Reopen voice controls.");
      const current = connection;
      const result = await api("voice", {});
      if (connection !== current) throw new Error("The sharing session changed. Reopen voice controls.");
      const url = new URL(result.voice_url);
      if (url.origin !== new URL(current.base).origin || url.pathname !== "/_browser/guest/voice")
        throw new Error("The voice endpoint is unavailable.");
      const token = new URLSearchParams(url.hash.slice(1)).get("token");
      if (!token) throw new Error("The voice endpoint is unavailable.");
      return { base: current.base, voice_token: token, session_id: current.session.id };
    }
    if (message.kind === "origin") {
      // activeTab is renewed when the host clicks our toolbar icon on the new page.
      const url = (compatible || native) ? (await chrome.tabs.get(tabId)).url : (await cdp("Page.getFrameTree")).frameTree.frame.url;
      if (!url || !/^https?:/.test(url)) throw new Error("Select the application tab and click the Evander toolbar icon before approving it.");
      const approvedOrigin = new URL(url).origin;
      granted = false;
      const result = await api("origin", { url });
      connection.approvedOrigin = approvedOrigin; deniedOrigin = undefined;
      update(result);
      if (compatible) { compatible.navigate(); await compatible.start(); }
      return { session: result };
    }
    if (message.kind === "disconnect") { await disconnect(); return { session: null }; }
    throw new Error("Unknown extension operation");
  })().then((result) => respond({ ok: true, ...result }), (error) => respond({ ok: false, error: error.message, status: error.status }));
  return true;
});
