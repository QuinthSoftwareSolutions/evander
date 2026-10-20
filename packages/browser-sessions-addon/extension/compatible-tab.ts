import { applicationDOM } from "./compatible-dom.js";
declare const chrome: any;
export const extensionConflict = (error: any) => /Cannot access a chrome-extension:\/\/ URL of different extension/i.test(String(error?.message ?? error));
export class CompatibleTab {
  private documentId?: string;
  private revision = 0;
  private disposed = false;
  private owner = crypto.randomUUID();
  private captured = false;
  private setup?: Promise<void>;
  constructor(private tabId: number, private origin: () => string, private current: () => boolean,
    private navigated: () => void) {}
  private check() { if (this.disposed || !this.current()) throw new Error("disconnected"); }
  navigate() { this.documentId = undefined; this.revision++; this.navigated(); }
  private async captureMessage(kind: string, extra: any = {}) {
    let timeout: ReturnType<typeof setTimeout> | undefined;
    let result: any;
    try {
      result = await Promise.race([chrome.runtime.sendMessage({ target: "tab-capture", kind, owner: this.owner, ...extra }),
        new Promise((_, reject) => { timeout = setTimeout(() => reject(new Error("capture_unavailable")), 8000); })]);
    } finally { clearTimeout(timeout); }
    if (!result?.ok) throw new Error(result?.error ?? "capture_unavailable");
    return result;
  }
  start() {
    this.setup = this.initialize(); return this.setup;
  }
  private async initialize() {
    this.check();
    await this.send("Page.getFrameTree");
    if (!await chrome.offscreen.hasDocument()) {
      await chrome.offscreen.createDocument({ url: "capture.html", reasons: ["USER_MEDIA"],
        justification: "Share video of the one application tab approved by the user." });
    }
    this.check();
    await this.captureMessage("stop"); this.captured = false;
    let streamId: string;
    try { streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: this.tabId }); }
    catch { throw new Error("Select the application tab, click the Evander toolbar icon, then try sharing again to allow tab capture."); }
    this.check();
    await this.captureMessage("start", { streamId }); this.check(); this.captured = true;
  }
  async stop() {
    this.disposed = true; this.revision++;
    await this.setup?.catch(() => {});
    if (await chrome.offscreen.hasDocument()) await chrome.offscreen.closeDocument().catch(() => {});
    this.captured = false;
  }
  async suspend() {
    this.captured = false; this.revision++;
    await this.captureMessage("stop").catch(() => {});
  }
  async send(method: string, params: any = {}): Promise<any> {
    this.check();
    const revision = this.revision;
    if (method === "Page.captureScreenshot") {
      if (!this.captured) throw new Error("origin_denied");
      await this.send("Page.getFrameTree");
      const documentId = this.documentId;
      const metrics = await this.send("Page.getLayoutMetrics");
      const frame = await this.captureMessage("frame", { viewport: metrics.cssVisualViewport });
      this.check();
      await this.send("Page.getFrameTree");
      if (documentId !== this.documentId || revision !== this.revision) throw new Error("stale_observation");
      return frame;
    }
    const target = method === "Page.getFrameTree" || !this.documentId ? { tabId: this.tabId, frameIds: [0] } :
      { tabId: this.tabId, documentIds: [this.documentId] };
    let results: any[];
    try { results = await chrome.scripting.executeScript({ target, world: "ISOLATED", func: applicationDOM,
      args: [method, params, this.origin()] }); }
    catch { throw new Error("origin_denied"); }
    this.check();
    if (revision !== this.revision) throw new Error("stale_observation");
    const result = results[0];
    if (!result?.documentId || !result.result) throw new Error("stale_observation");
    if (this.documentId && result.documentId !== this.documentId) {
      this.documentId = result.documentId; this.revision++; this.navigated(); throw new Error("stale_observation");
    }
    this.documentId = result.documentId;
    if (result.result.error) throw new Error(result.result.error);
    return result.result;
  }
}
