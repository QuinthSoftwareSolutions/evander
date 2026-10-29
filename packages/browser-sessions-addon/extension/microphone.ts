declare const chrome: any;

// Side panels cannot consistently present Chrome's first-use permission prompt.
// A short-lived extension popup grants the same extension origin microphone access.
export async function prepareMicrophone(signal: AbortSignal) {
  signal.throwIfAborted();
  const permission = await navigator.permissions.query({ name: "microphone" as PermissionName });
  if (permission.state === "granted") return;
  if (permission.state === "denied") throw new Error(
    "Microphone access is blocked. Allow it in this extension's Chrome site settings, then try again.");
  await new Promise<void>((resolve, reject) => {
    let windowId: number | undefined, finished = false;
    const request = crypto.randomUUID();
    const page = chrome.runtime.getURL("microphone.html");
    const close = () => {
      if (windowId !== undefined) void chrome.windows.remove(windowId).catch(() => {});
    };
    const finish = (error?: Error) => {
      if (finished) return;
      finished = true; clearTimeout(timer);
      chrome.runtime.onMessage.removeListener(receive);
      chrome.windows.onRemoved.removeListener(removed);
      signal.removeEventListener("abort", cancelled);
      window.removeEventListener("pagehide", cancelled);
      close();
      if (error) reject(error); else resolve();
    };
    const receive = (message: any, sender: any, respond: any) => {
      if (message.kind !== "microphone_permission_result" || message.request_id !== request ||
          sender.id !== chrome.runtime.id || sender.url?.split("#")[0] !== page) return;
      respond({ ok: true });
      finish(message.granted ? undefined : new Error("Microphone access was denied or unavailable."));
    };
    const removed = (id: number) => {
      if (id === windowId) finish(new Error("Microphone setup was cancelled. Try enabling it again."));
    };
    const cancelled = () => finish(new Error("Microphone setup was cancelled."));
    const timer = setTimeout(() => finish(new Error("Microphone setup timed out. Try again.")), 120000);
    chrome.runtime.onMessage.addListener(receive);
    chrome.windows.onRemoved.addListener(removed);
    signal.addEventListener("abort", cancelled, { once: true });
    window.addEventListener("pagehide", cancelled, { once: true });
    if (signal.aborted) { cancelled(); return; }
    void chrome.windows.create({ url: page + "#request=" + request, type: "popup",
      width: 420, height: 350, focused: true }).then((popup: any) => {
      windowId = popup.id;
      if (finished) close();
    }, () => finish(new Error("Chrome could not open microphone permissions. Try again.")));
  });
}
