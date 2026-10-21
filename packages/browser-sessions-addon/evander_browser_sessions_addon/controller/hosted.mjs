// controller/hosted.ts
import { readFile, stat } from "node:fs/promises";
import { createRequire } from "node:module";

// shared/observation.ts
var roles = /* @__PURE__ */ new Set(["button", "link", "textbox", "combobox", "checkbox", "radio", "tab", "menuitem", "option", "heading", "dialog", "alert", "gridcell", "row"]);
var roleOf = (node) => ({ LayoutTableRow: "row", LayoutTableCell: "gridcell", cell: "gridcell", rowheader: "gridcell", columnheader: "gridcell" })[node.role?.value] ?? node.role?.value;
var states = ["checked", "selected", "expanded", "disabled", "readonly", "focused", "editable", "required"];
function accessibleElements(nodes) {
  const byId = new Map(nodes.map((node) => [String(node.nodeId), node]));
  const elements = [];
  const signature = (node) => {
    const properties = Object.fromEntries((node.properties ?? []).filter((p) => states.includes(p.name)).map((p) => [p.name, p.value?.value]));
    return {
      role: roleOf(node),
      name: String(node.name?.value ?? "").slice(0, 400),
      value: String(node.value?.value ?? "").slice(0, 1e3),
      ...properties
    };
  };
  for (const node of nodes) {
    if (node.ignored || !node.backendDOMNodeId || !roles.has(roleOf(node))) continue;
    const context2 = node.evanderContext ?? [];
    let parent = node, depth = 0;
    while (!node.evanderContext && parent && depth++ < 16) {
      parent = byId.get(String(parent.parentId));
      if (parent && ["row", "dialog", "form", "group", "region"].includes(roleOf(parent))) {
        const children = (parent.childIds ?? []).slice(0, 24).map((id) => byId.get(String(id))).filter(Boolean).map((child) => String(child.name?.value ?? "")).filter(Boolean).join(" ").slice(0, 600);
        context2.push({ role: roleOf(parent), name: String(parent.name?.value ?? "").slice(0, 200), text: children });
      }
      if (context2.length === 3) break;
    }
    const data = { ...signature(node), context: context2 };
    elements.push({ ref: "node-" + node.backendDOMNodeId, ...data });
    if (elements.length === 600) break;
  }
  return { elements, truncated: nodes.filter((n) => !n.ignored && n.backendDOMNodeId && roles.has(roleOf(n))).length > 600 };
}
function targetSignature(value) {
  if (!value) return "";
  const { ref, focused, ...semantic } = value;
  return JSON.stringify(semantic);
}
function pageSignature(elements) {
  return JSON.stringify(elements.filter((item) => ["textbox", "combobox", "checkbox", "radio", "heading", "dialog", "alert"].includes(item.role)).map((item) => ({
    ref: item.ref,
    role: item.role,
    name: item.name,
    value: item.value,
    checked: item.checked,
    selected: item.selected,
    expanded: item.expanded,
    disabled: item.disabled,
    readonly: item.readonly
  })));
}

// shared/engine.ts
var BrowserEngine = class {
  constructor(send2, state2, emit) {
    this.send = send2;
    this.state = state2;
    this.emit = emit;
  }
  observation = "";
  refs = /* @__PURE__ */ new Map();
  epoch = 0;
  signatures = /* @__PURE__ */ new Map();
  pageContext = "";
  update(state2) {
    if (state2.id === this.state.id && state2.control_generation >= this.state.control_generation) this.state = state2;
  }
  navigate() {
    this.epoch += 1;
    this.observation = "";
    this.refs.clear();
  }
  async location() {
    const tree = await this.send("Page.getFrameTree");
    return tree.frameTree.frame.url;
  }
  guard(command, actor, epoch) {
    if (command.deadline_at && Date.now() / 1e3 >= command.deadline_at) throw new Error("run_deadline");
    if (Date.now() / 1e3 >= this.state.expires_at) throw new Error("session_expired");
    if (command.generation !== this.state.control_generation) throw new Error("stale_control");
    if (!["observe", "highlight"].includes(command.action) && this.state.controller !== actor)
      throw new Error("control_required");
    if (epoch !== void 0 && epoch !== this.epoch) throw new Error("stale_observation");
  }
  async execute(command, actor) {
    const epoch = this.epoch;
    const fence = async () => {
      this.guard(command, actor, epoch);
      const current = await this.location();
      this.guard(command, actor, epoch);
      if (!this.state.allowed_origins.includes(new URL(current).origin)) throw new Error("origin_denied");
      return current;
    };
    let edited = false, inputStarted = false;
    const validateTarget = async () => {
      if (!command.revalidate || !command.ref) return;
      const { nodes } = await this.send("Accessibility.getFullAXTree");
      const elements = accessibleElements(nodes ?? []).elements;
      const fresh = elements.find((item) => item.ref === command.ref);
      if (!edited && pageSignature(elements) !== this.pageContext) throw new Error(inputStarted ? "outcome_unknown" : "stale_target");
      const saved = this.signatures.get(command.ref);
      if (!fresh || fresh.disabled || !saved) throw new Error(inputStarted ? "outcome_unknown" : "stale_target");
      const before = JSON.parse(saved), after = JSON.parse(targetSignature(fresh));
      if (edited) for (const key of ["value", "checked", "selected", "expanded"]) {
        delete before[key];
        delete after[key];
      }
      if (JSON.stringify(before) !== JSON.stringify(after)) throw new Error(inputStarted ? "outcome_unknown" : "stale_target");
    };
    const input = async (method, params = {}) => {
      await validateTarget();
      await fence();
      this.guard(command, actor, epoch);
      try {
        inputStarted = true;
        return await this.send(method, params);
      } finally {
        if (method === "Input.insertText" || method === "Input.dispatchKeyEvent" && params.type === "keyDown") edited = true;
      }
    };
    const press = async (key, text, code, keyCode) => {
      const identity = {
        key,
        ...code ? { code } : {},
        ...keyCode ? { windowsVirtualKeyCode: keyCode } : {}
      };
      await input("Input.dispatchKeyEvent", {
        type: "keyDown",
        ...identity,
        ...text !== void 0 ? { text, unmodifiedText: text } : {}
      });
      await input("Input.dispatchKeyEvent", { type: "keyUp", ...identity });
    };
    const url = await fence();
    if (command.action === "observe") {
      const before = this.epoch;
      const tree = await this.send("Accessibility.getFullAXTree");
      const nodes = tree.nodes;
      this.refs.clear();
      this.signatures.clear();
      const { elements, truncated } = accessibleElements(nodes ?? []);
      this.pageContext = pageSignature(elements);
      for (const element of elements) {
        this.refs.set(element.ref, Number(element.ref.slice(5)));
        this.signatures.set(element.ref, targetSignature(element));
      }
      let screenshot;
      for (let attempt = 0; command.include_image !== false && attempt < 3; attempt++) {
        try {
          screenshot = await this.send("Page.captureScreenshot", {
            format: "jpeg",
            quality: 65,
            captureBeyondViewport: false
          });
          break;
        } catch (error) {
          if (attempt === 2) throw error;
          await new Promise((resolve) => setTimeout(resolve, 75));
        }
      }
      await fence();
      if (before !== this.epoch) throw new Error("stale_observation");
      const metrics2 = await this.send("Page.getLayoutMetrics");
      const viewport2 = metrics2.cssVisualViewport ?? metrics2.visualViewport;
      await fence();
      this.observation = crypto.randomUUID();
      return {
        observation_id: this.observation,
        page_epoch: String(this.epoch),
        url,
        elements,
        text: tree.text ?? (nodes ?? []).filter((n) => n.role?.value === "StaticText").map((n) => n.name?.value ?? "").join(" ").slice(0, 12e3),
        title: String(tree.title ?? (nodes ?? []).find((n) => n.role?.value === "RootWebArea")?.name?.value ?? "").slice(0, 400),
        viewport: viewport2 ? { x: viewport2.pageX ?? 0, y: viewport2.pageY ?? 0 } : void 0,
        truncated: truncated || tree.truncated === true,
        ...screenshot ? { image: screenshot.data, mime_type: "image/jpeg" } : {}
      };
    }
    if (!["navigate", "reload"].includes(command.action) && command.observation_id !== this.observation)
      throw new Error("stale_observation");
    if (command.action === "navigate") {
      if (!this.state.allowed_origins.includes(new URL(command.url).origin)) throw new Error("origin_denied");
      await input("Page.navigate", { url: command.url });
      this.navigate();
      return { success: true };
    }
    if (command.action === "reload") {
      await input("Page.reload");
      this.navigate();
      return { success: true };
    }
    await validateTarget();
    let x = command.x, y = command.y;
    if (command.ref && ["click", "hover", "highlight", "scroll"].includes(command.action)) {
      const backendNodeId = this.refs.get(command.ref);
      if (!backendNodeId) throw new Error("stale_observation");
      if (!["highlight", "scroll"].includes(command.action)) {
        await input("DOM.scrollIntoViewIfNeeded", { backendNodeId });
        await fence();
      }
      const { model } = await this.send("DOM.getBoxModel", { backendNodeId });
      x = (model.content[0] + model.content[4]) / 2;
      y = (model.content[1] + model.content[5]) / 2;
    }
    const metrics = await this.send("Page.getLayoutMetrics");
    const viewport = metrics.cssVisualViewport ?? metrics.visualViewport;
    if (["click", "hover", "highlight"].includes(command.action)) {
      if (!Number.isFinite(x) || !Number.isFinite(y) || x < 0 || y < 0 || x > viewport.clientWidth || y > viewport.clientHeight) throw new Error("invalid_coordinates");
      await fence();
      this.emit({ kind: "pointer", x, y, action: command.action });
      if (command.action === "highlight") return { success: true };
      this.guard(command, actor);
      await input("Input.dispatchMouseEvent", { type: "mouseMoved", x, y });
      if (command.action === "click") {
        this.guard(command, actor);
        await input("Input.dispatchMouseEvent", { type: "mousePressed", x, y, button: "left", clickCount: 1 });
        await input("Input.dispatchMouseEvent", { type: "mouseReleased", x, y, button: "left", clickCount: 1 });
      }
    } else if (command.action === "fill") {
      const backendNodeId = this.refs.get(command.ref);
      if (!backendNodeId) throw new Error("stale_observation");
      await input("DOM.focus", { backendNodeId });
      this.guard(command, actor);
      await input("Input.dispatchKeyEvent", { type: "keyDown", key: "a", code: "KeyA", modifiers: 2, windowsVirtualKeyCode: 65 });
      await input("Input.dispatchKeyEvent", { type: "keyUp", key: "a", code: "KeyA", modifiers: 2, windowsVirtualKeyCode: 65 });
      this.guard(command, actor);
      await input("Input.insertText", { text: command.text ?? "" });
    } else if (command.action === "type") {
      if (command.revalidate && command.ref) await input("DOM.focus", { backendNodeId: this.refs.get(command.ref) });
      for (const character of (command.text ?? "").replace(/\r\n/g, "\n")) {
        const newline = character === "\n" || character === "\r";
        await press(
          newline ? "Enter" : character,
          newline ? "\r" : character,
          newline ? "Enter" : character === " " ? "Space" : void 0,
          newline ? 13 : character === " " ? 32 : void 0
        );
      }
    } else if (command.action === "key") {
      if (command.revalidate && command.ref) await input("DOM.focus", { backendNodeId: this.refs.get(command.ref) });
      const keys = {
        Enter: 13,
        Tab: 9,
        Escape: 27,
        Backspace: 8,
        Delete: 46,
        ArrowLeft: 37,
        ArrowUp: 38,
        ArrowRight: 39,
        ArrowDown: 40,
        Space: 32
      };
      const key = command.text;
      if (!keys[key]) throw new Error("unsupported_key");
      await press(
        key === "Space" ? " " : key,
        key === "Enter" ? "\r" : key === "Space" ? " " : void 0,
        key,
        keys[key]
      );
    } else if (command.action === "scroll") {
      this.guard(command, actor);
      await input("Input.dispatchMouseEvent", {
        type: "mouseWheel",
        x: x ?? 10,
        y: y ?? 10,
        deltaX: command.delta_x ?? 0,
        deltaY: command.delta_y ?? 0
      });
    } else if (command.action === "dialog") {
      this.guard(command, actor);
      await input("Page.handleJavaScriptDialog", { accept: command.accept === true });
    } else {
      throw new Error("unsupported_action");
    }
    return { success: true, url: await this.location() };
  }
};

// controller/hosted.ts
var config = JSON.parse(await readFile(process.env.EVANDER_BROWSER_CONNECTION, "utf8"));
var require2 = createRequire(import.meta.url);
var { chromium } = require2(process.env.EVANDER_PLAYWRIGHT_MODULE ?? "/opt/evander/playwright-runtime/node_modules/playwright");
var browser = await chromium.launch({
  headless: true,
  handleSIGTERM: false,
  handleSIGINT: false,
  executablePath: process.env.PLAYWRIGHT_MCP_EXECUTABLE_PATH || void 0,
  args: ["--disable-dev-shm-usage"]
});
var storageState = void 0;
try {
  const statePath = process.env.EVANDER_PREVIEW_STATE ?? "/run/preview-output/browser-state.json";
  if ((await stat(statePath)).size > 1048576) throw new Error("state_too_large");
  const candidate = JSON.parse(await readFile(statePath, "utf8"));
  if (!Array.isArray(candidate.cookies) || !Array.isArray(candidate.origins)) throw new Error("invalid_state");
  const hosts = config.allowed_origins.map((value) => new URL(value).hostname);
  if (candidate.cookies.some((cookie) => !hosts.includes(cookie.domain.replace(/^\./, ""))) || candidate.origins.some((item) => !config.allowed_origins.includes(item.origin)))
    throw new Error("state_origin_mismatch");
  storageState = candidate;
} catch (error) {
  if (error.code !== "ENOENT") throw new Error("Invalid preview browser-state handoff");
}
var context = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  storageState,
  acceptDownloads: false
});
await context.route("**/*", async (route) => {
  const request = route.request();
  if (request.isNavigationRequest() && !config.allowed_origins.includes(new URL(request.url()).origin)) {
    await route.abort();
    return;
  }
  await route.continue();
});
var page = await context.newPage();
var cdp = await context.newCDPSession(page);
var socket;
var shuttingDown = false;
for (const signal of ["SIGTERM", "SIGINT"]) process.on(signal, () => {
  shuttingDown = true;
  socket?.close();
  void browser.close().finally(() => process.exit(0));
});
var state = config.session;
var send = (value) => {
  if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(value));
};
var engine = new BrowserEngine((method, params) => cdp.send(method, params), state, send);
cdp.on("Page.frameNavigated", () => engine.navigate());
cdp.on("Page.navigatedWithinDocument", () => engine.navigate());
cdp.on("Page.screencastFrame", async (event) => {
  try {
    const metrics = await cdp.send("Page.getLayoutMetrics");
    const viewport = metrics.cssVisualViewport;
    send({
      kind: "frame",
      image: event.data,
      width: Math.round(viewport.clientWidth),
      height: Math.round(viewport.clientHeight),
      page_epoch: String(event.sessionId),
      url: page.url()
    });
  } finally {
    await cdp.send("Page.screencastFrameAck", { sessionId: event.sessionId });
  }
});
await cdp.send("Page.enable");
await page.goto(config.entry_url, { waitUntil: "domcontentloaded", timeout: 6e4 });
var commandChain = Promise.resolve();
var streaming = false;
async function join() {
  streaming = false;
  socket = new WebSocket(config.relay_url.replace(/^http/, "ws"));
  socket.addEventListener("open", () => send({ kind: "authenticate", version: 1, token: config.endpoint_token, features: { fast_execution: 1 } }));
  socket.addEventListener("error", () => socket?.close());
  socket.addEventListener("message", (event) => {
    let message;
    try {
      message = JSON.parse(String(event.data));
    } catch {
      socket?.close();
      return;
    }
    if (message.kind === "state") {
      state = message.session;
      engine.update(state);
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
        } catch (error) {
          send({
            kind: "result",
            id: message.command.id,
            error: true,
            code: /^[a-z_]+$/.test(error.message) ? error.message : "browser_error"
          });
        }
      });
    }
  });
  return new Promise((resolve) => socket.addEventListener("close", () => {
    state = { ...state, controller: null, control_generation: state.control_generation + 1 };
    engine.update(state);
    resolve();
  }));
}
var deadline = setTimeout(
  () => void browser.close().finally(() => process.exit(0)),
  Math.max(1, config.session.expires_at * 1e3 - Date.now())
);
var heartbeat = setInterval(() => send({ kind: "heartbeat" }), 2e4);
try {
  while (!shuttingDown && Date.now() / 1e3 < config.session.expires_at) {
    await join();
    await new Promise((resolve) => setTimeout(resolve, 3e3));
  }
} finally {
  clearInterval(heartbeat);
  clearTimeout(deadline);
  await browser.close();
}
