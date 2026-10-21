declare const chrome: any;
const HOST = "com.evander.browser_helper";
const errors: Record<string, string> = {
  remote_debugging_unavailable: "Enable remote debugging at chrome://inspect/#remote-debugging and approve Chrome's connection prompt, then try again.",
  wrong_browser: "The helper connected to a different Chrome profile. Check the browser data directory used when installing it.",
  helper_timeout: "The helper timed out. Check Chrome's connection prompt, then reconnect sharing.",
  helper_unavailable: "The local helper is unavailable. Check its installation and Chrome's remote-debugging connection.",
};
export class NativeTab {
  private port: any;
  private sequence = 0;
  fastExecution = false;
  commandDeadline: number | undefined;
  private epoch = 0;
  private closed = false;
  private opened = false;
  private pending = new Map<number, { resolve: (value: any) => void; reject: (error: Error) => void; timer: ReturnType<typeof setTimeout> }>();
  constructor(private state: any, private event: (method: string, params: any) => void,
    private navigated: () => void, private disconnected: () => void) {
    this.port = chrome.runtime.connectNative(HOST);
    this.port.onMessage.addListener((message: any) => {
      if (this.closed) return;
      if (message.kind === "event" && Number.isSafeInteger(message.epoch) && message.epoch < this.epoch) {
        if (message.method === "Page.screencastFrame")
          void this.send("Page.screencastFrameAck", { sessionId: message.params.sessionId }).catch(() => {});
        return;
      }
      if (Number.isSafeInteger(message.epoch) && message.epoch > this.epoch) {
        this.epoch = message.epoch;
        if (!["Page.frameNavigated", "Page.navigatedWithinDocument"].includes(message.method)) this.navigated();
      }
      if (message.kind === "event") { this.event(message.method, message.params); return; }
      if (message.kind === "disconnected") { this.fail("The local helper disconnected from Chrome."); return; }
      const pending = this.pending.get(message.id);
      if (!pending) return;
      clearTimeout(pending.timer); this.pending.delete(message.id);
      if (message.ok) pending.resolve(message.result);
      else pending.reject(new Error(errors[message.error] ?? (/^[a-z_]+$/.test(message.error) ? message.error : "browser_error")));
    });
    this.port.onDisconnect.addListener(() => {
      // Reading lastError prevents Chrome's noisy unchecked runtime diagnostic.
      const missing = chrome.runtime.lastError;
      this.fail(missing ? "Install the Evander Browser Helper for this extension ID, then try again." : "The local helper disconnected.");
    });
  }
  private fail(message: string) {
    if (this.closed) return;
    this.close(message); this.disconnected();
  }
  private request(body: any, timeout = 16000): Promise<any> {
    if (this.closed) return Promise.reject(new Error("disconnected"));
    if (this.pending.size >= 24) return Promise.reject(new Error("helper_unavailable"));
    const id = ++this.sequence;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id); reject(new Error(errors.helper_timeout)); this.fail(errors.helper_timeout);
      }, timeout);
      this.pending.set(id, { resolve, reject, timer });
      try { this.port.postMessage({ id, ...body }); }
      catch { this.fail("The local helper disconnected."); }
    });
  }
  async open(target: string) {
    const info = await this.request({kind:"ping"});
    this.fastExecution = info.features?.fast_execution === 1;
    await this.request({ kind: "open", target_id: target, state: this.state }, 85000);
    this.opened = true;
  }
  update(state: any) {
    this.state = state;
    if (this.opened && !this.closed) void this.request({ kind: "state", state }).catch(() => this.fail(errors.helper_unavailable));
  }
  send(method: string, params: any = {}) {
    return this.request({ kind: "command", method, params, generation: this.state.control_generation, epoch: this.epoch,
      ...(method !== "Page.screencastFrameAck" && this.commandDeadline ? {deadline_at:this.commandDeadline} : {}) });
  }
  close(reason = "disconnected") {
    if (this.closed) return;
    this.closed = true;
    for (const value of this.pending.values()) { clearTimeout(value.timer); value.reject(new Error(reason)); }
    this.pending.clear(); this.port.disconnect();
  }
}
