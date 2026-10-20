export type Send = (method: string, params?: Record<string, unknown>) => Promise<any>;
export type State = { id: string; controller: string | null; control_generation: number;
  allowed_origins: string[]; expires_at: number };
export class BrowserEngine {
  private observation = "";
  private refs = new Map<string, number>();
  private epoch = 0;
  constructor(private send: Send, private state: State, private emit: (event: any) => void) {}
  update(state: State) { this.state = state; }
  navigate() { this.epoch += 1; this.observation = ""; this.refs.clear(); }
  private async location() {
    const tree = await this.send("Page.getFrameTree");
    return tree.frameTree.frame.url as string;
  }
  private guard(command: any, actor: string, epoch?: number) {
    if (Date.now() / 1000 >= this.state.expires_at) throw new Error("session_expired");
    if (command.generation !== this.state.control_generation) throw new Error("stale_control");
    if (!["observe", "highlight"].includes(command.action) && this.state.controller !== actor)
      throw new Error("control_required");
    if (epoch !== undefined && epoch !== this.epoch) throw new Error("stale_observation");
  }
  async execute(command: any, actor: string) {
    const epoch = this.epoch;
    // Async reads and focus can race navigation or a local revocation. Fence
    // every input primitive against the same document and current authority.
    const fence = async () => {
      this.guard(command, actor, epoch);
      const current = await this.location();
      this.guard(command, actor, epoch);
      if (!this.state.allowed_origins.includes(new URL(current).origin)) throw new Error("origin_denied");
      return current;
    };
    const input = async (method: string, params: Record<string, unknown> = {}) => {
      await fence();
      this.guard(command, actor, epoch);
      return this.send(method, params);
    };
    const press = async (key: string, text?: string, code?: string, keyCode?: number) => {
      const identity = { key, ...(code ? { code } : {}),
        ...(keyCode ? { windowsVirtualKeyCode: keyCode } : {}) };
      await input("Input.dispatchKeyEvent", { type: "keyDown", ...identity,
        ...(text !== undefined ? { text, unmodifiedText: text } : {}) });
      await input("Input.dispatchKeyEvent", { type: "keyUp", ...identity });
    };
    const url = await fence();
    if (command.action === "observe") {
      const before = this.epoch;
      const { nodes } = await this.send("Accessibility.getFullAXTree");
      this.refs.clear();
      const elements = [];
      const roles = new Set(["button", "link", "textbox", "combobox", "checkbox", "radio",
        "tab", "menuitem", "option", "heading", "dialog", "alert", "gridcell", "row"]);
      for (const node of nodes ?? []) {
        if (elements.length >= 600) break;
        const role = node.role?.value;
        if (node.ignored || !node.backendDOMNodeId || !roles.has(role)) continue;
        const ref = "node-" + node.backendDOMNodeId;
        this.refs.set(ref, node.backendDOMNodeId);
        elements.push({ ref, role, name: String(node.name?.value ?? "").slice(0, 400) });
      }
      let screenshot: any;
      for (let attempt = 0; attempt < 3; attempt++) {
        try {
          screenshot = await this.send("Page.captureScreenshot", { format: "jpeg", quality: 65,
            captureBeyondViewport: false });
          break;
        } catch (error) {
          if (attempt === 2) throw error;
          await new Promise(resolve => setTimeout(resolve, 75));
        }
      }
      await fence();
      if (before !== this.epoch) throw new Error("stale_observation");
      this.observation = crypto.randomUUID();
      return { observation_id: this.observation, page_epoch: String(this.epoch), url,
        elements, image: screenshot.data, mime_type: "image/jpeg" };
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
      await input("Page.reload"); this.navigate(); return { success: true };
    }
    let x = command.x, y = command.y;
    if (command.ref && ["click", "hover", "highlight"].includes(command.action)) {
      const backendNodeId = this.refs.get(command.ref);
      if (!backendNodeId) throw new Error("stale_observation");
      const { model } = await this.send("DOM.getBoxModel", { backendNodeId });
      x = (model.content[0] + model.content[4]) / 2;
      y = (model.content[1] + model.content[5]) / 2;
    }
    const metrics = await this.send("Page.getLayoutMetrics");
    const viewport = metrics.cssVisualViewport ?? metrics.visualViewport;
    if (["click", "hover", "highlight"].includes(command.action)) {
      if (!Number.isFinite(x) || !Number.isFinite(y) || x < 0 || y < 0 ||
          x > viewport.clientWidth || y > viewport.clientHeight) throw new Error("invalid_coordinates");
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
      // Character key events also reach page-level shortcuts and scanner listeners.
      // fill deliberately retains direct text insertion for editing a specific field.
      for (const character of (command.text ?? "").replace(/\r\n/g, "\n")) {
        const newline = character === "\n" || character === "\r";
        await press(newline ? "Enter" : character, newline ? "\r" : character,
          newline ? "Enter" : character === " " ? "Space" : undefined,
          newline ? 13 : character === " " ? 32 : undefined);
      }
    } else if (command.action === "key") {
      const keys: Record<string, number> = { Enter: 13, Tab: 9, Escape: 27, Backspace: 8,
        Delete: 46, ArrowLeft: 37, ArrowUp: 38, ArrowRight: 39, ArrowDown: 40, Space: 32 };
      const key = command.text;
      if (!keys[key]) throw new Error("unsupported_key");
      await press(key === "Space" ? " " : key,
        key === "Enter" ? "\r" : key === "Space" ? " " : undefined,
        key, keys[key]);
    } else if (command.action === "scroll") {
      this.guard(command, actor);
      await input("Input.dispatchMouseEvent", { type: "mouseWheel", x: x ?? 10, y: y ?? 10,
        deltaX: command.delta_x ?? 0, deltaY: command.delta_y ?? 0 });
    } else if (command.action === "dialog") {
      this.guard(command, actor);
      await input("Page.handleJavaScriptDialog", { accept: command.accept === true });
    } else { throw new Error("unsupported_action"); }
    return { success: true, url: await this.location() };
  }
}
