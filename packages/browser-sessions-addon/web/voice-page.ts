import { enterToSend } from "./composer.js";
import { VoiceControls, voiceJson } from "./voice-controls.js";
const $ = (id: string) => document.getElementById(id)!;
const token = new URLSearchParams(location.hash.slice(1)).get("token");
history.replaceState(null, "", location.pathname);
let after = 0, ended = false, refreshing = false, refreshAgain = false;
const displayedMessages = new Set<string>();
function fail(error: any) { $("error").textContent = error.message || "Voice session unavailable."; }
async function request(action: string, body?: any, options: RequestInit = {}) {
  if (!token) throw new Error("Open a new voice page from the sharing extension.");
  const response = await fetch("/_browser/guest/voice/" + action, {
    credentials: "omit", redirect: "error",
    ...options, method: options.method ?? (body ? "POST" : "GET"),
    headers: { "Authorization": "Bearer " + token, ...(body ? { "Content-Type": "application/json" } : {}), ...options.headers },
    body: body ? JSON.stringify(body) : options.body,
  });
  if (response.status === 401 || response.status === 410) {
    ended = true; voice.dispose();
    throw new Error("Voice session ended. Reopen voice chat from the sharing extension.");
  }
  return response;
}
const voice = new VoiceControls($("voice-controls"), {
  info: async () => voiceJson(await request("info")),
  transcribe: async (audio, id, signal) => voiceJson(await request("transcribe", undefined, {
    method: "POST", body: audio, signal,
    headers: { "Content-Type": audio.type, "X-Voice-Request-ID": id },
  })),
  speech: async (message, id, signal) => {
    const response = await request("speech", {message_id:message,request_id:id,stream:typeof AudioContext !== "undefined"}, {signal});
    if (!response.ok) { await voiceJson(response); throw new Error("Speech unavailable."); }
    return response;
  },
  send: async (text, id) => {
    await voiceJson(await request("message", {request_id:id,body:text})); await refresh();
  },
});
async function refresh() {
  if (ended) return;
  if (refreshing) { refreshAgain = true; return; }
  refreshing = true;
  try {
    const result = await voiceJson(await request("state?after=" + after));
    $("session-title").textContent = result.session.title;
    $("scope").textContent = "Shared tab: " + (result.session.current_url ? new URL(result.session.current_url).origin : "waiting for the application");
    for (const message of result.messages) {
      after = Math.max(after, message.seq);
      if (displayedMessages.has(message.id)) continue;
      const paragraph = document.createElement("p");
      paragraph.textContent = (message.actor.startsWith("agent:") ? "Truman: " : "You: ") + message.body;
      $("messages").append(paragraph); displayedMessages.add(message.id);
    }
    voice.messages(result.messages);
    if (["closed", "expired", "failed", "cleanup_failed"].includes(result.session.state)) {
      ended = true; voice.dispose();
    }
  } catch (error) { fail(error); }
  finally {
    refreshing = false;
    if (refreshAgain) { refreshAgain = false; void refresh(); }
  }
}
enterToSend($("body") as HTMLTextAreaElement, $("chat") as HTMLFormElement);
$("chat").addEventListener("submit", async event => {
  event.preventDefault();
  if (!($("body") as HTMLTextAreaElement).value.trim()) return;
  try {
    const body = $("body") as HTMLTextAreaElement;
    await voiceJson(await request("message", {request_id:crypto.randomUUID(),body:body.value}));
    body.value = ""; await refresh();
  } catch (error) { fail(error); }
});
void refresh();
setInterval(() => void refresh(), 2000);
