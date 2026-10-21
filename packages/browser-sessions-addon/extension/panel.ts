import { enterToSend } from "../web/composer.js";
import { messageDelivery } from "../web/message-delivery.js";
import { VoiceControls, voiceJson } from "../web/voice-controls.js";
import { prepareMicrophone } from "./microphone.js";
declare const chrome: any;
let activeRunId: string | undefined;
const $ = (id: string) => document.getElementById(id)!;
let after = 0;
let sessionId: string | null = null;
let voice: VoiceControls | undefined;
let voiceGeneration = 0;
let refreshing = false;
let refreshAgain = false;
const voicePanel = $("voice-panel") as HTMLDetailsElement;
async function call(message: any) {
  const result = await chrome.runtime.sendMessage(message);
  if (!result.ok) {
    const error = new Error(result.error) as Error & { status?: number };
    error.status = result.status;
    throw error;
  }
  return result;
}
function stopVoice() {
  voiceGeneration += 1; voice?.dispose(); voice = undefined;
}
function failure(error: any) {
  if (error.status === 401 || error.status === 410) stopVoice();
  $("error").textContent = error.message;
}
function startVoice() {
  if (voice || !sessionId || !voicePanel.open || document.hidden) return;
  const owner = sessionId, generation = ++voiceGeneration;
  let credentials: Promise<any> | undefined;
  const current = () => {
    if (owner !== sessionId || generation !== voiceGeneration) throw new Error("Voice session ended.");
  };
  async function request(action: string, body?: any, options: RequestInit = {}) {
    current();
    const grant = await (credentials ??= call({ kind: "voice", browser_session_id: owner }));
    current();
    if (grant.session_id !== owner) throw new Error("The sharing session changed. Reopen voice controls.");
    const response = await fetch(grant.base + "/_browser/guest/voice/" + action, {
      credentials: "omit", redirect: "error", ...options,
      method: options.method ?? (body ? "POST" : "GET"),
      headers: { "Authorization": "Bearer " + grant.voice_token,
        ...(body ? { "Content-Type": "application/json" } : {}), ...options.headers },
      body: body ? JSON.stringify(body) : options.body,
    });
    current();
    if (response.status === 401 || response.status === 410) {
      stopVoice(); throw new Error("Voice session ended.");
    }
    return response;
  }
  const root = $("voice-controls");
  const talk = root.querySelector<HTMLButtonElement>("[data-voice-talk]")!;
  talk.disabled = true; talk.textContent = "Enable microphone"; talk.setAttribute("aria-pressed", "false");
  root.querySelector<HTMLInputElement>("[data-voice-replies]")!.disabled = true;
  root.querySelector<HTMLButtonElement>("[data-voice-stop]")!.disabled = true;
  root.querySelector<HTMLElement>("[data-voice-status]")!.textContent = "Loading voice options…";
  voice = new VoiceControls(root, {
    prepareMicrophone,
    info: async () => voiceJson(await request("info")),
    transcribe: async (audio, id, signal) => voiceJson(await request("transcribe", undefined, {
      method: "POST", body: audio, signal,
      headers: { "Content-Type": audio.type, "X-Voice-Request-ID": id },
    })),
    speech: async (message, id, signal) => {
      const response = await request("speech", { message_id: message, request_id: id, stream: typeof AudioContext !== "undefined" }, { signal });
      if (!response.ok) { await voiceJson(response); throw new Error("Speech unavailable."); }
      return response;
    },
    send: async (text, id) => {
      await voiceJson(await request("message", { request_id: id, body: text })); await refresh();
    },
  });
}
async function refresh() {
  if (refreshing) { refreshAgain = true; return; }
  refreshing = true;
  try {
    const result = await call({ kind: "state", after, browser_session_id: sessionId });
    $("pairing").hidden = !!result.session;
    $("session").hidden = !result.session;
    const next = result.session?.id ?? null;
    if (next !== sessionId) {
      stopVoice(); sessionId = next; after = 0; $("messages").replaceChildren();
      $("voice-controls").querySelector<HTMLInputElement>("[data-voice-replies]")!.checked = false;
    }
    if (!result.session) return;
    activeRunId=result.session.active_run?.id;
    $("run-progress").hidden=!activeRunId; $("stop-run").hidden=!activeRunId;
    $("run-progress").textContent="Truman is working · "+(result.session.active_run?.step_count ?? 0)+" steps";
    $("title").textContent = result.session.title;
    $("shared-origin").textContent = "Sharing " + (result.session.current_url ? new URL(result.session.current_url).origin : "the selected tab");
    $("status").textContent = !result.connected ? "Disconnected — agent control is paused" :
      result.granted && result.session.controller?.startsWith("agent:") ? "Truman may interact with this tab" : "Observe only — you control this tab";
    $("compatibility").hidden = result.mode !== "compatibility";
    if (result.mode === "helper") $("status").textContent += " · Local helper";
    $("compatibility").textContent = "Compatibility mode keeps your other extensions active. Native browser dialogs and pages requiring trusted input need normal mode.";
    const ended = ["closing", "closed", "expired", "failed", "cleanup_failed"].includes(result.session.state);
    if (ended) stopVoice(); else startVoice();
    for (const message of result.messages ?? []) {
      const item = document.createElement("p");
      item.textContent = (message.actor.startsWith("agent:") ? "Truman: " : "You: ") + message.body;
      if (!message.actor.startsWith("agent:")) item.dataset.messageId = message.id;
      $("messages").append(item); after = Math.max(after, message.seq);
    }
    messageDelivery($("messages"), [...(result.messages ?? []), ...(result.message_updates ?? [])],
      id => void call({kind:"retry-message",message_id:id,request_id:crypto.randomUUID()}).then(refresh).catch(failure));
    voice?.messages(result.messages ?? []);
  } finally {
    refreshing = false;
    if (refreshAgain) { refreshAgain = false; void refresh().catch(failure); }
  }
}
$("pair").addEventListener("click", async () => {
  try {
    $("error").textContent = "";
    const invitation = ($("invitation") as HTMLInputElement).value;
    const url = new URL(invitation);
    const mode = ($("connection-mode") as HTMLSelectElement).value;
    const granted = await chrome.permissions.request({ origins: [url.origin + "/*"],
      ...(mode === "helper" ? { permissions: ["nativeMessaging"] } : {}) });
    if (!granted) throw new Error("Connection permission was not granted");
    await call({ kind: "pair", invitation, mode }); await refresh();
  } catch (error) { failure(error); }
});
for (const [button, action] of [["grant", "grant"], ["pause", "pause"]])
  $(button).addEventListener("click", () => void call({ kind: "control", action }).then(refresh).catch(failure));
voicePanel.addEventListener("toggle", () => { if (voicePanel.open) startVoice(); else stopVoice(); });
$("origin").addEventListener("click", () => void call({ kind: "origin" }).then(refresh).catch(failure));
$("disconnect").addEventListener("click", () => {
  stopVoice();
  void call({ kind: "disconnect" }).then(refresh).catch(failure);
});
chrome.storage.onChanged.addListener((changes: any, area: string) => {
  if (area === "session" && "connection" in changes) {
    const nextSession = changes.connection.newValue?.session?.id ?? null;
    if (nextSession !== sessionId) stopVoice();
    void refresh().catch(failure);
  }
});
enterToSend($("body") as HTMLTextAreaElement, $("chat") as HTMLFormElement);
$("chat").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!($("body") as HTMLTextAreaElement).value.trim()) return;
  try {
    const body = $("body") as HTMLTextAreaElement;
    await call({ kind: "message", body: body.value }); body.value = ""; await refresh();
  } catch (error) { failure(error); }
});
document.addEventListener("visibilitychange", () => {
  if (document.hidden) stopVoice(); else void refresh().catch(failure);
});
window.addEventListener("pagehide", stopVoice, { once: true });
setInterval(() => void refresh().catch(failure), 2000);
void refresh().catch(failure);

($("extension-id") as HTMLInputElement).value = chrome.runtime.id;
$("invitation").addEventListener("input", () => {
  const link = $("helper-download") as HTMLAnchorElement;
  link.hidden = true; $("helper-link-hint").hidden = false;
  try {
    const url = new URL(($("invitation") as HTMLInputElement).value);
    if (url.protocol !== "https:" && !["localhost", "127.0.0.1"].includes(url.hostname)) return;
    if (url.pathname !== "/_browser/guest/join") return;
    link.href = url.origin + "/_browser/helper"; link.hidden = false; $("helper-link-hint").hidden = true;
  } catch {}
});

$("stop-run").addEventListener("click",()=>{ if (activeRunId) void call({kind:"cancel-run"}).then(refresh).catch(failure); });
