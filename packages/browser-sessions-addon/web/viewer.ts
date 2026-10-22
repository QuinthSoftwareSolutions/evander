import { enterToSend } from "./composer.js";
import "../../../frontend/admin-web/src/design/addon-context.js";
import { VoiceControls, voiceJson } from "./voice-controls.js";
import { messageDelivery } from "./message-delivery.js";
export {};
const base = "/api/addons/browser-sessions-addon";
const $ = (id: string) => document.getElementById(id)!;
const input = (id: string) => $(id) as HTMLInputElement;
const canvas = $("display") as HTMLCanvasElement;
const directory = new URLSearchParams(location.search).get("directory_path") ??
  new URLSearchParams(location.search).get("directory") ?? "root";
const sessionId = location.pathname.match(/\/view\/([^/]+)$/)?.[1];
document.body.classList.toggle("viewer-page", !!sessionId);
const themeToggle = $("theme-toggle") as HTMLButtonElement;
// Embedded viewers inherit the host's existing theme control.
themeToggle.hidden = window.parent !== window;
function syncThemeToggle() {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  themeToggle.textContent = next === "dark" ? "Dark mode" : "Light mode";
  themeToggle.setAttribute("aria-label", "Switch to " + next + " mode");
}
themeToggle.addEventListener("click", () => {
  const theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
  try { localStorage.setItem("admin-web:theme", theme); } catch { /* Storage is optional. */ }
});
new MutationObserver(syncThemeToggle).observe(document.documentElement, {
  attributes: true, attributeFilter: ["data-theme"],
});
syncThemeToggle();
let session: any, actor = "", after = 0, pointing = false, annotation: any;
let settings: any, settingsRevision = 0, websocket: WebSocket | undefined;
let voice: VoiceControls | undefined;
let frameURL = "", observation = "", pendingInput = Promise.resolve();
let notificationTimer: ReturnType<typeof setTimeout> | undefined;
function dismissNotification() {
  clearTimeout(notificationTimer); $("error").hidden = true;
}
function showNotification(text: string, reminder = false) {
  clearTimeout(notificationTimer);
  $("error-message").textContent = text;
  $("error").dataset.kind = reminder ? "reminder" : "error";
  $("error").setAttribute("role", reminder ? "status" : "alert");
  $("error").hidden = false;
  if (reminder) notificationTimer = setTimeout(dismissNotification, 5000);
}
$("dismiss-error").addEventListener("click", dismissNotification);
function showError(error: any) {
  showNotification(error.message ?? String(error));
}
async function api(path: string, body?: any, method?: string) {
  const response = await fetch(base + path, {
    method: method ?? (body ? "POST" : "GET"), credentials: "same-origin",
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (response.status === 401) {
    location.replace("/?next=" + encodeURIComponent(location.pathname + location.search));
    throw new Error("Sign in to continue");
  }
  const result = await response.json();
  if (!response.ok) throw new Error(result.error ?? "Browser session unavailable");
  return result;
}
function update(value: any) {
  session = value;
  if (session.controller === actor && $("error").dataset.kind === "reminder") dismissNotification();
  $("session-title").textContent = session.title;
  $("session-title").title = session.title;
  $("stop-run").hidden = !session.active_run; $("run-progress").hidden = !session.active_run;
  $("run-progress").textContent = "Truman is working · " + (session.active_run?.step_count ?? 0) + " steps";
  $("status").textContent = session.state.replaceAll("_", " ");
  $("status").dataset.state = session.state;
  $("application-url").textContent = session.current_url ?? "Preparing application";
  $("expiry").textContent = "Expires " + new Date(session.expires_at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const yours = !!actor && session.controller === actor;
  const agent = session.controller?.startsWith("agent:");
  $("claim").textContent = yours ? "Hand back to Truman" : "Take control";
  $("claim").setAttribute("aria-pressed", String(yours));
  ($("claim") as HTMLButtonElement).disabled = !actor || ["closing", "closed", "expired", "failed", "cleanup_failed"].includes(session.state);
  $("control-help").textContent = yours ?
    "You control this browser. Click the application and type normally." : agent ?
    "Truman controls this browser. You can point, chat, or take control." : session.controller ?
    "Another viewer controls this browser. You can point, chat, or take control." :
    "Agent input is paused. Take control to use the application.";
  if (["closed", "expired", "cleanup_failed", "failed"].includes(session.state)) {
    voice?.dispose();
    $("empty").hidden = false;
    $("empty").querySelector("h2")!.textContent = session.state === "cleanup_failed" ? "Cleanup needs attention" : "Session unavailable";
    $("empty").querySelector("p")!.textContent = session.error ?? "Return to the session list or ask Truman to start another session.";
  }
}
function pageOptions(pages:any[]) {
  if(!Array.isArray(pages))return;
  $("page-picker").hidden=pages.length<2;
  const select=$("pages") as HTMLSelectElement;
  const signature=JSON.stringify(pages);
  if(select.dataset.signature===signature)return;
  select.dataset.signature=signature;select.replaceChildren();
  for(const page of pages) {
    const option=document.createElement("option");option.value=page.id;option.textContent=page.url;option.selected=page.selected;select.append(option);
  }
}
$("pages").addEventListener("change",()=>{
  observation="";
  void api("/sessions/"+sessionId+"/select-page",{page_id:($("pages") as HTMLSelectElement).value}).then(result=>pageOptions(result.pages)).catch(showError);
});
async function command(action: string, options: any = {}) {
  const result = await api("/sessions/" + sessionId + "/command", {
    id: crypto.randomUUID(), generation: session.control_generation,
    action, observation_id: observation || null, ...options,
  });
  if (result.observation_id) observation = result.observation_id;
  if(result.pages)pageOptions(result.pages);
  return result;
}
function serializeInput(action: string, options: any = {}) {
  pendingInput = pendingInput.then(async () => {
    if (session?.controller !== actor) {
      showNotification("Take control before interacting", true); return;
    }
    await command("observe"); await command(action, options);
  }).catch(showError);
}
function coordinates(event: MouseEvent) {
  const bounds = canvas.getBoundingClientRect();
  return { x: (event.clientX - bounds.left) * canvas.width / bounds.width,
    y: (event.clientY - bounds.top) * canvas.height / bounds.height };
}
canvas.addEventListener("click", (event) => {
  const point = coordinates(event);
  if (pointing) {
    annotation = { ...point, width: canvas.width, height: canvas.height, url: frameURL };
    $("annotation").hidden = false;
    pointing = false; $("point").setAttribute("aria-pressed", "false"); canvas.style.cursor = "";
    input("chat-body").focus(); return;
  }
  canvas.focus();
  serializeInput("click", point);
});
canvas.addEventListener("wheel", (event) => {
  if (session?.controller !== actor) return;
  event.preventDefault();
  serializeInput("scroll", { ...coordinates(event), delta_x: event.deltaX, delta_y: event.deltaY });
}, { passive: false });
canvas.addEventListener("keydown", (event) => {
  if (session?.controller !== actor || event.isComposing) return;
  const special = ["Enter", "Tab", "Escape", "Backspace", "Delete", "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"];
  if (special.includes(event.key)) {
    event.preventDefault(); serializeInput("key", { text: event.key });
  } else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey) {
    event.preventDefault(); serializeInput("type", { text: event.key });
  }
});
canvas.addEventListener("compositionend", (event) => {
  if (session?.controller === actor && event.data) serializeInput("type", { text: event.data });
});
$("point").addEventListener("click", () => {
  pointing = !pointing; $("point").setAttribute("aria-pressed", String(pointing));
  canvas.style.cursor = pointing ? "crosshair" : "";
});
$("clear-annotation").addEventListener("click", () => { annotation = undefined; $("annotation").hidden = true; });
$("claim").addEventListener("click", () => void api("/sessions/" + sessionId + "/control", {
  action: session.controller === actor ? "handback" : "claim", expected_generation: session.control_generation,
}).then(update).catch(showError));
$("pause").addEventListener("click", () => void api("/sessions/" + sessionId + "/control", {
  action: "pause", expected_generation: session.control_generation,
}).then(update).catch(showError));
$("extend").addEventListener("click", () => void api("/sessions/" + sessionId + "/extend", {
  expected_revision: session.revision,
}).then(update).catch(showError));
$("close").addEventListener("click", () => void api("/sessions/" + sessionId + "/close", {}).then(update).catch(showError));
let refreshingMessages = false, refreshMessagesAgain = false;
const displayedMessages = new Set<string>();
async function messages() {
  if (refreshingMessages) { refreshMessagesAgain = true; return; }
  refreshingMessages = true;
  try {
    const result = await api("/sessions/" + sessionId + "/messages?after=" + after);
    if (result.session) update(result.session);
    let appended = false;
    for (const message of result.messages) {
      after = Math.max(after, message.seq);
      if (displayedMessages.has(message.id)) continue;
      const item = document.createElement("div"); item.className = "message" + (message.actor.startsWith("agent:") ? " agent" : "");
      const name = document.createElement("strong");
      name.textContent = message.actor.startsWith("agent:") ? "TRUMAN" : message.actor.replace(/^user:/, "");
      item.append(name, document.createTextNode(message.body)); $("messages").append(item);
      if (!message.actor.startsWith("agent:")) item.dataset.messageId = message.id;
      displayedMessages.add(message.id); appended = true;
    }
    voice?.messages(result.messages);
    messageDelivery($("messages"), [...result.messages, ...(result.message_updates ?? [])],
      id => void api("/sessions/" + sessionId + "/retry-message", {message_id:id,request_id:crypto.randomUUID()}).then(messages).catch(showError));
    if (appended) $("messages").scrollTop = $("messages").scrollHeight;
  } finally {
    refreshingMessages = false;
    if (refreshMessagesAgain) { refreshMessagesAgain = false; void messages().catch(showError); }
  }
}
enterToSend($("chat-body") as HTMLTextAreaElement, $("chat-form") as HTMLFormElement);
$("chat-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!input("chat-body").value.trim()) return;
  try {
    await api("/sessions/" + sessionId + "/messages", {
      request_id: crypto.randomUUID(), body: input("chat-body").value, annotation: annotation ?? null,
    });
    input("chat-body").value = ""; annotation = undefined; $("annotation").hidden = true; await messages();
  } catch (error) { showError(error); }
});
async function openViewer() {
  $("workspace").hidden = false;
  update(await api("/sessions/" + sessionId));
  const voicePath = "/sessions/" + sessionId + "/voice";
  async function voiceRequest(action: string, body: BodyInit, headers: Record<string, string>, signal: AbortSignal) {
    const response = await fetch(base + voicePath + "/" + action, {
      method: "POST", credentials: "same-origin", redirect: "error", headers, body, signal,
    });
    if (response.status === 401 || response.status === 410) voice?.dispose();
    return response;
  }
  voice = new VoiceControls($("voice-controls"), {
    info: () => api(voicePath + "/info"),
    transcribe: async (audio, id, signal) => voiceJson(await voiceRequest("transcribe", audio,
      { "Content-Type": audio.type, "X-Voice-Request-ID": id }, signal)),
    speech: async (message, id, signal) => {
      const response = await voiceRequest("speech", JSON.stringify({message_id:message,request_id:id,stream:typeof AudioContext !== "undefined"}),
        { "Content-Type": "application/json" }, signal);
      if (!response.ok) { await voiceJson(response); throw new Error("Speech unavailable."); }
      return response;
    },
    send: async (text, id, context) => {
      await api("/sessions/" + sessionId + "/messages", {
        request_id: id, body: text, annotation: context,
      });
      annotation = undefined; $("annotation").hidden = true;
      await messages();
    },
  }, () => annotation ?? null);

  websocket = new WebSocket(location.origin.replace(/^http/, "ws") + base + "/sessions/" + sessionId + "/stream");
  websocket.addEventListener("message", async (event) => {
    const payload = JSON.parse(String(event.data));
    if (payload.kind === "state") { actor = payload.actor ?? actor; update(payload.session); }
    if (payload.kind === "frame") {
      pageOptions(payload.pages);
      const image = new Image();
      image.src = "data:image/jpeg;base64," + payload.image;
      await image.decode();
      canvas.width = payload.width; canvas.height = payload.height;
      canvas.getContext("2d")!.drawImage(image, 0, 0, canvas.width, canvas.height);
      $("empty").hidden = true; frameURL = payload.url; $("application-url").textContent = frameURL;
    }
    if (payload.kind === "pointer") {
      const display = canvas.getBoundingClientRect(), area = $("viewport").getBoundingClientRect();
      const pointer = $("pointer"); pointer.hidden = false;
      pointer.style.left = String(display.left - area.left + payload.x * display.width / canvas.width - 9) + "px";
      pointer.style.top = String(display.top - area.top + payload.y * display.height / canvas.height - 9) + "px";
    }
  });
  websocket.addEventListener("close", () => {
    $("control-help").textContent = "Connection closed. Refresh to reconnect; agent control stays paused.";
    void api("/sessions/" + sessionId).then(update).catch(showError);
  });
  await messages();
  setInterval(() => void messages().catch(showError), 2000);
}
function field(card: HTMLElement, label: string, value: any, updateValue: (value: string) => void, type = "text") {
  const element = document.createElement("label"); element.textContent = label;
  const box = document.createElement("input"); box.type = type; box.value = String(value ?? "");
  box.addEventListener("input", () => { updateValue(box.value); syncAdvanced(); });
  element.append(box); card.append(element);
}
function syncAdvanced() { input("advanced-settings").value = JSON.stringify(settings, null, 2); }
function renderSettings() {
  settings.fast_execution ??= {enabled:false,max_steps:20,timeout_seconds:30};
  input("fast-enabled").checked=settings.fast_execution.enabled;
  input("fast-enabled").onchange=()=>{settings.fast_execution.enabled=input("fast-enabled").checked;syncAdvanced();};
  for (const [id,key,fallback] of [["fast-steps","max_steps",20],["fast-seconds","timeout_seconds",30]] as const) {
    input(id).value=String(settings.fast_execution[key] ?? fallback);
    input(id).oninput=()=>{settings.fast_execution[key]=Number(input(id).value);syncAdvanced();};
  }
  settings.voice ??= {};
  input("voice-enabled").checked = settings.voice.enabled ?? false;
  input("voice-enabled").onchange = () => { settings.voice.enabled = input("voice-enabled").checked; syncAdvanced(); };
  for (const [id, key, fallback] of [
    ["voice-provider", "api_base_url", "https://api.openai.com/v1"],
    ["voice-key", "api_key_env", "EVANDER_BROWSER_VOICE_API_KEY"],
    ["voice-stt", "transcription_model", "gpt-transcribe"],
    ["voice-tts", "speech_model", "gpt-4o-mini-tts"],
    ["voice-name", "voice", "onyx"],
    ["voice-seconds", "max_recording_seconds", 45],
  ] as const) {
    input(id).value = String(settings.voice[key] ?? fallback);
    input(id).oninput = () => {
      settings.voice[key] = typeof fallback === "number" ? Number(input(id).value) : input(id).value;
      syncAdvanced();
    };
  }

  input("enabled").checked = settings.enabled; input("guest-enabled").checked = settings.guest_enabled;
  input("public-url").value = settings.public_base_url; input("relay-url").value = settings.relay_url;
  input("relay-token").value = settings.relay_token_env; input("lifetime").value = String(settings.lifetime_seconds / 60);
  input("idle").value = String(settings.idle_seconds / 60); input("quota").value = String(settings.max_hosted_per_worker);
  for (const [id, key, convert] of [
    ["public-url", "public_base_url", String], ["relay-url", "relay_url", String],
    ["relay-token", "relay_token_env", String], ["lifetime", "lifetime_seconds", (v: string) => Number(v) * 60],
    ["idle", "idle_seconds", (v: string) => Number(v) * 60], ["quota", "max_hosted_per_worker", Number],
  ] as const) {
    input(id).oninput = () => { settings[key] = convert(input(id).value); syncAdvanced(); };
  }
  input("enabled").onchange = () => { settings.enabled = input("enabled").checked; syncAdvanced(); };
  input("guest-enabled").onchange = () => { settings.guest_enabled = input("guest-enabled").checked; syncAdvanced(); };
  $("workers").replaceChildren(); $("profiles").replaceChildren();
  for (const [name, worker] of Object.entries(settings.workers) as [string, any][]) {
    const card = document.createElement("div"); card.className = "config-card";
    const title = document.createElement("strong"); title.textContent = name; card.append(title);
    for (const [key, label] of [["parent_worker_url", "Assigned worker URL"], ["base_url", "Browser supervisor URL"],
      ["token_env", "Credential environment variable"], ["relay_url", "Worker-reachable relay endpoint"]])
      field(card, label, worker[key], (value) => worker[key] = value || null);
    $("workers").append(card);
  }
  for (const [name, profile] of Object.entries(settings.profiles) as [string, any][]) {
    const card = document.createElement("div"); card.className = "config-card";
    const title = document.createElement("strong"); title.textContent = name; card.append(title);
    field(card, "Browser image", profile.image, (value) => profile.image = value);
    field(card, "CPUs", profile.cpus, (value) => profile.cpus = Number(value), "number");
    field(card, "Memory (MiB)", profile.memory_mib, (value) => profile.memory_mib = Number(value), "number");
    $("profiles").append(card);
  }
  syncAdvanced();
}
$("configure").addEventListener("click", async () => {
  try {
    const result = await api("/settings?directory=" + encodeURIComponent(directory));
    settings = result.settings; settingsRevision = result.revision;
    renderSettings(); $("settings-panel").hidden = false;
  } catch (error) { showError(error); }
});
$("add-worker").addEventListener("click", () => {
  const name = "worker-" + (Object.keys(settings.workers).length + 1);
  settings.workers[name] = { base_url: "", parent_worker_url: "", token_env: "EVANDER_BROWSER_WORKER_TOKEN", relay_url: null };
  renderSettings();
});
$("add-profile").addEventListener("click", () => {
  const name = "profile-" + (Object.keys(settings.profiles).length + 1);
  settings.profiles[name] = { image: "evander-browser-controller:latest", cpus: 1, memory_mib: 1536, readonly_mounts: {}, playwright_enabled: false };
  renderSettings();
});
$("apply-advanced").addEventListener("click", () => {
  try { settings = JSON.parse(input("advanced-settings").value); renderSettings(); } catch (error) { showError(error); }
});
$("settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const result = await api("/settings?directory=" + encodeURIComponent(directory), {
      expected_revision: settingsRevision, settings,
    }, "PUT");
    settingsRevision = result.revision; $("settings-status").textContent = "Saved";
  } catch (error) { showError(error); }
});
let catalogSessions: any[] = [];
const closedStates = new Set(["closed", "expired"]);
function renderCatalog() {
  const showClosed = input("show-closed").checked;
  const visible = catalogSessions.filter(item => showClosed || !closedStates.has(item.state));
  $("session-list").replaceChildren();
  if (!visible.length) {
    const message = document.createElement("p"); message.className = "muted";
    message.textContent = showClosed ? "No hosted sessions yet. Ask Truman to start a demonstration or preview." :
      "No active hosted sessions. Ask Truman to start a demonstration or preview.";
    $("session-list").append(message);
  }
  for (const item of [...visible].reverse()) {
    const card = document.createElement("a"); card.className = "session-card"; card.href = item.viewer_url;
    const status = document.createElement("span"); status.className = "status"; status.textContent = item.state.replaceAll("_", " ");
    status.dataset.state = item.state;
    const title = document.createElement("h2"); title.textContent = item.title;
    const type = document.createElement("p"); type.textContent = "Shared browser";
    const expiry = document.createElement("p"); expiry.textContent = "Expires " + new Date(item.expires_at * 1000).toLocaleString();
    card.append(status, title, type, expiry); $("session-list").append(card);
  }
}
input("show-closed").addEventListener("change", renderCatalog);
async function catalog() {
  $("catalog").hidden = false; $("directory-label").textContent = directory;
  $("catalog").prepend($("error"));
  document.querySelector(".top-actions")!.prepend(themeToggle);
  const result = await api("/sessions?directory=" + encodeURIComponent(directory));
  catalogSessions = result.sessions;
  renderCatalog();
}
void (sessionId ? openViewer() : catalog()).catch(showError);

$("stop-run").addEventListener("click",()=>{ if (session?.active_run) void api("/sessions/"+sessionId+"/cancel-run",{}).then(()=>api("/sessions/"+sessionId)).then(update).catch(showError); });
