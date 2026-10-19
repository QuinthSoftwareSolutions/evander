// web/viewer.ts
var base = "/api/addons/browser-sessions-addon";
var $ = (id) => document.getElementById(id);
var input = (id) => $(id);
var canvas = $("display");
var directory = new URLSearchParams(location.search).get("directory_path") ?? new URLSearchParams(location.search).get("directory") ?? "root";
var sessionId = location.pathname.match(/\/view\/([^/]+)$/)?.[1];
var session;
var actor = "";
var after = 0;
var pointing = false;
var annotation;
var settings;
var settingsRevision = 0;
var websocket;
var frameURL = "";
var observation = "";
var pendingInput = Promise.resolve();
function showError(error) {
  $("error").hidden = false;
  $("error").textContent = error.message ?? String(error);
}
async function api(path, body, method) {
  const response = await fetch(base + path, {
    method: method ?? (body ? "POST" : "GET"),
    credentials: "same-origin",
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : void 0
  });
  if (response.status === 401) {
    location.replace("/?next=" + encodeURIComponent(location.pathname + location.search));
    throw new Error("Sign in to continue");
  }
  const result = await response.json();
  if (!response.ok) throw new Error(result.error ?? "Browser session unavailable");
  return result;
}
function update(value) {
  session = value;
  $("session-title").textContent = session.title;
  $("status").textContent = session.state.replaceAll("_", " ");
  $("status").dataset.state = session.state;
  $("application-url").textContent = session.current_url ?? "Preparing application";
  $("expiry").textContent = "Expires " + new Date(session.expires_at * 1e3).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  $("control-help").textContent = session.controller === actor ? "You control this browser. Click the application and type normally." : session.controller?.startsWith("agent:") ? "Truman controls this browser. You can point, chat, or take control." : "Agent input is paused. Take control or hand control back to Truman.";
  if (["closed", "expired", "cleanup_failed", "failed"].includes(session.state)) {
    $("empty").hidden = false;
    $("empty").querySelector("h2").textContent = session.state === "cleanup_failed" ? "Cleanup needs attention" : "Session unavailable";
    $("empty").querySelector("p").textContent = session.error ?? "Return to the session list or ask Truman to start another session.";
  }
}
async function command(action, options = {}) {
  const result = await api("/sessions/" + sessionId + "/command", {
    id: crypto.randomUUID(),
    generation: session.control_generation,
    action,
    observation_id: observation || null,
    ...options
  });
  if (result.observation_id) observation = result.observation_id;
  return result;
}
function serializeInput(action, options = {}) {
  pendingInput = pendingInput.then(async () => {
    if (session.controller !== actor) throw new Error("Take control before interacting");
    await command("observe");
    await command(action, options);
  }).catch(showError);
}
function coordinates(event) {
  const bounds = canvas.getBoundingClientRect();
  return {
    x: (event.clientX - bounds.left) * canvas.width / bounds.width,
    y: (event.clientY - bounds.top) * canvas.height / bounds.height
  };
}
canvas.addEventListener("click", (event) => {
  const point = coordinates(event);
  if (pointing) {
    annotation = { ...point, width: canvas.width, height: canvas.height, url: frameURL };
    $("annotation").hidden = false;
    pointing = false;
    $("point").setAttribute("aria-pressed", "false");
    canvas.style.cursor = "";
    input("chat-body").focus();
    return;
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
    event.preventDefault();
    serializeInput("key", { text: event.key });
  } else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey) {
    event.preventDefault();
    serializeInput("type", { text: event.key });
  }
});
canvas.addEventListener("compositionend", (event) => {
  if (session?.controller === actor && event.data) serializeInput("type", { text: event.data });
});
$("point").addEventListener("click", () => {
  pointing = !pointing;
  $("point").setAttribute("aria-pressed", String(pointing));
  canvas.style.cursor = pointing ? "crosshair" : "";
});
$("clear-annotation").addEventListener("click", () => {
  annotation = void 0;
  $("annotation").hidden = true;
});
for (const [id, action] of [["claim", "claim"], ["handback", "handback"], ["pause", "pause"]]) {
  $(id).addEventListener("click", () => void api("/sessions/" + sessionId + "/control", {
    action,
    expected_generation: session.control_generation
  }).then(update).catch(showError));
}
$("extend").addEventListener("click", () => void api("/sessions/" + sessionId + "/extend", {
  expected_revision: session.revision
}).then(update).catch(showError));
$("close").addEventListener("click", () => void api("/sessions/" + sessionId + "/close", {}).then(update).catch(showError));
async function messages() {
  const result = await api("/sessions/" + sessionId + "/messages?after=" + after);
  for (const message of result.messages) {
    const item = document.createElement("div");
    item.className = "message" + (message.actor.startsWith("agent:") ? " agent" : "");
    const name = document.createElement("strong");
    name.textContent = message.actor.startsWith("agent:") ? "TRUMAN" : message.actor.replace(/^user:/, "");
    item.append(name, document.createTextNode(message.body));
    $("messages").append(item);
    after = Math.max(after, message.seq);
  }
  if (result.messages.length) $("messages").scrollTop = $("messages").scrollHeight;
}
$("chat-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api("/sessions/" + sessionId + "/messages", {
      request_id: crypto.randomUUID(),
      body: input("chat-body").value,
      annotation: annotation ?? null
    });
    input("chat-body").value = "";
    annotation = void 0;
    $("annotation").hidden = true;
    await messages();
  } catch (error) {
    showError(error);
  }
});
async function openViewer() {
  $("workspace").hidden = false;
  update(await api("/sessions/" + sessionId));
  websocket = new WebSocket(location.origin.replace(/^http/, "ws") + base + "/sessions/" + sessionId + "/stream");
  websocket.addEventListener("message", async (event) => {
    const payload = JSON.parse(String(event.data));
    if (payload.kind === "state") {
      actor = payload.actor ?? actor;
      update(payload.session);
    }
    if (payload.kind === "frame") {
      const image = new Image();
      image.src = "data:image/jpeg;base64," + payload.image;
      await image.decode();
      canvas.width = payload.width;
      canvas.height = payload.height;
      canvas.getContext("2d").drawImage(image, 0, 0, canvas.width, canvas.height);
      $("empty").hidden = true;
      frameURL = payload.url;
      $("application-url").textContent = frameURL;
    }
    if (payload.kind === "pointer") {
      const display = canvas.getBoundingClientRect(), area = $("viewport").getBoundingClientRect();
      const pointer = $("pointer");
      pointer.hidden = false;
      pointer.style.left = String(display.left - area.left + payload.x * display.width / canvas.width - 9) + "px";
      pointer.style.top = String(display.top - area.top + payload.y * display.height / canvas.height - 9) + "px";
    }
  });
  websocket.addEventListener("close", () => {
    $("control-help").textContent = "Connection closed. Refresh to reconnect; agent control stays paused.";
    void api("/sessions/" + sessionId).then(update).catch(showError);
  });
  await messages();
  setInterval(() => void messages().catch(showError), 2e3);
}
function field(card, label, value, updateValue, type = "text") {
  const element = document.createElement("label");
  element.textContent = label;
  const box = document.createElement("input");
  box.type = type;
  box.value = String(value ?? "");
  box.addEventListener("input", () => {
    updateValue(box.value);
    syncAdvanced();
  });
  element.append(box);
  card.append(element);
}
function syncAdvanced() {
  input("advanced-settings").value = JSON.stringify(settings, null, 2);
}
function renderSettings() {
  input("enabled").checked = settings.enabled;
  input("guest-enabled").checked = settings.guest_enabled;
  input("public-url").value = settings.public_base_url;
  input("relay-url").value = settings.relay_url;
  input("relay-token").value = settings.relay_token_env;
  input("lifetime").value = String(settings.lifetime_seconds / 60);
  input("idle").value = String(settings.idle_seconds / 60);
  input("quota").value = String(settings.max_hosted_per_worker);
  for (const [id, key, convert] of [
    ["public-url", "public_base_url", String],
    ["relay-url", "relay_url", String],
    ["relay-token", "relay_token_env", String],
    ["lifetime", "lifetime_seconds", (v) => Number(v) * 60],
    ["idle", "idle_seconds", (v) => Number(v) * 60],
    ["quota", "max_hosted_per_worker", Number]
  ]) {
    input(id).oninput = () => {
      settings[key] = convert(input(id).value);
      syncAdvanced();
    };
  }
  input("enabled").onchange = () => {
    settings.enabled = input("enabled").checked;
    syncAdvanced();
  };
  input("guest-enabled").onchange = () => {
    settings.guest_enabled = input("guest-enabled").checked;
    syncAdvanced();
  };
  $("workers").replaceChildren();
  $("profiles").replaceChildren();
  for (const [name, worker] of Object.entries(settings.workers)) {
    const card = document.createElement("div");
    card.className = "config-card";
    const title = document.createElement("strong");
    title.textContent = name;
    card.append(title);
    for (const [key, label] of [
      ["parent_worker_url", "Assigned worker URL"],
      ["base_url", "Browser supervisor URL"],
      ["token_env", "Credential environment variable"],
      ["relay_url", "Worker-reachable relay endpoint"]
    ])
      field(card, label, worker[key], (value) => worker[key] = value || null);
    $("workers").append(card);
  }
  for (const [name, profile] of Object.entries(settings.profiles)) {
    const card = document.createElement("div");
    card.className = "config-card";
    const title = document.createElement("strong");
    title.textContent = name;
    card.append(title);
    field(card, "Application image", profile.image, (value) => profile.image = value);
    field(card, "Browser image", profile.browser_image, (value) => profile.browser_image = value);
    field(card, "CPUs", profile.cpus, (value) => profile.cpus = Number(value), "number");
    field(card, "Memory (MiB)", profile.memory_mib, (value) => profile.memory_mib = Number(value), "number");
    $("profiles").append(card);
  }
  syncAdvanced();
}
$("configure").addEventListener("click", async () => {
  try {
    const result = await api("/settings?directory=" + encodeURIComponent(directory));
    settings = result.settings;
    settingsRevision = result.revision;
    renderSettings();
    $("settings-panel").hidden = false;
  } catch (error) {
    showError(error);
  }
});
$("add-worker").addEventListener("click", () => {
  const name = "worker-" + (Object.keys(settings.workers).length + 1);
  settings.workers[name] = { base_url: "", parent_worker_url: "", token_env: "EVANDER_BROWSER_WORKER_TOKEN", relay_url: null };
  renderSettings();
});
$("add-profile").addEventListener("click", () => {
  const name = "profile-" + (Object.keys(settings.profiles).length + 1);
  settings.profiles[name] = {
    image: "evander-runner:latest",
    browser_image: "evander-browser-controller:latest",
    cpus: 2,
    memory_mib: 4096,
    browser_memory_mib: 1536,
    network_name: "evander-preview",
    public_environment: {},
    readonly_mounts: {},
    egress_allowlist: [],
    database_enabled: false
  };
  renderSettings();
});
$("apply-advanced").addEventListener("click", () => {
  try {
    settings = JSON.parse(input("advanced-settings").value);
    renderSettings();
  } catch (error) {
    showError(error);
  }
});
$("settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const result = await api("/settings?directory=" + encodeURIComponent(directory), {
      expected_revision: settingsRevision,
      settings
    }, "PUT");
    settingsRevision = result.revision;
    $("settings-status").textContent = "Saved";
  } catch (error) {
    showError(error);
  }
});
async function catalog() {
  $("catalog").hidden = false;
  $("directory-label").textContent = directory;
  const result = await api("/sessions?directory=" + encodeURIComponent(directory));
  $("session-list").replaceChildren();
  if (!result.sessions.length) {
    const message = document.createElement("p");
    message.className = "muted";
    message.textContent = "No hosted sessions yet. Ask Truman to start a demonstration or preview.";
    $("session-list").append(message);
  }
  for (const item of result.sessions.reverse()) {
    const card = document.createElement("a");
    card.className = "session-card";
    card.href = item.viewer_url;
    const status = document.createElement("span");
    status.className = "status";
    status.textContent = item.state.replaceAll("_", " ");
    status.dataset.state = item.state;
    const title = document.createElement("h2");
    title.textContent = item.title;
    const type = document.createElement("p");
    type.textContent = item.mode === "hosted_development" ? "Live development" : "Guided demonstration";
    const expiry = document.createElement("p");
    expiry.textContent = "Expires " + new Date(item.expires_at * 1e3).toLocaleString();
    card.append(status, title, type, expiry);
    $("session-list").append(card);
  }
}
void (sessionId ? openViewer() : catalog()).catch(showError);
window.addEventListener("message", (event) => {
  if (event.source !== parent || event.origin !== location.origin || event.data?.type !== "admin-addon-context") return;
  if (event.data.theme) document.documentElement.dataset.theme = event.data.theme;
});
if (parent !== window) parent.postMessage({ type: "admin-addon-ready" }, location.origin);
