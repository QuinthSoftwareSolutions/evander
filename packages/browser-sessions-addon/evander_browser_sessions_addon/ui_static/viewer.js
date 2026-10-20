// web/voice-controls.ts
var VoiceControls = class {
  constructor(root, transport, context = () => null) {
    this.transport = transport;
    this.context = context;
    this.talk = root.querySelector("[data-voice-talk]");
    this.replies = root.querySelector("[data-voice-replies]");
    this.stop = root.querySelector("[data-voice-stop]");
    this.status = root.querySelector("[data-voice-status]");
    const watch = (target, name, listener) => target.addEventListener(name, listener, { signal: this.events.signal });
    watch(this.talk, "pointerdown", (event) => {
      if (!this.microphoneReady || event.button !== 0) return;
      event.preventDefault();
      this.talk.focus({ preventScroll: true });
      this.talk.setPointerCapture(event.pointerId);
      void this.start();
    });
    watch(this.talk, "pointerup", () => this.finish());
    watch(this.talk, "pointercancel", () => this.finish(true));
    watch(this.talk, "blur", () => this.finish(true));
    watch(this.talk, "keydown", (event) => {
      if (!this.microphoneReady || ![" ", "Enter"].includes(event.key)) return;
      event.preventDefault();
      if (!event.repeat) void this.start();
    });
    watch(this.talk, "keyup", (event) => {
      if (this.microphoneReady && [" ", "Enter"].includes(event.key)) {
        event.preventDefault();
        this.finish();
      }
    });
    watch(this.talk, "click", (event) => {
      if (!this.microphoneReady) void this.enable();
      else if (event.detail === 0) {
        if (this.recording || this.opening) this.finish();
        else void this.start();
      }
    });
    watch(this.replies, "change", () => {
      if (!this.replies.checked) this.stopAudio();
    });
    watch(this.stop, "click", () => this.stopAudio());
    window.addEventListener("blur", () => this.finish(true), { signal: this.events.signal });
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) this.finish(true);
    }, { signal: this.events.signal });
    window.addEventListener("pagehide", () => this.dispose(), { once: true, signal: this.events.signal });
    void this.initialize();
  }
  talk;
  replies;
  stop;
  status;
  config;
  events = new AbortController();
  microphoneSetupAbort;
  microphoneReady = false;
  opening = false;
  recording = false;
  transcribing = false;
  released = false;
  discard = false;
  disposed = false;
  recorder;
  stream;
  timer;
  capture = 0;
  audio;
  audioAbort;
  transcriptionAbort;
  finishAudio;
  playback = 0;
  playing = false;
  pending = [];
  seen = /* @__PURE__ */ new Set();
  speakAfter = Infinity;
  say(text) {
    if (!this.disposed) this.status.textContent = text;
  }
  async initialize() {
    try {
      this.config = await this.transport.info();
      if (this.disposed) return;
      this.speakAfter = this.config.latest_message_seq ?? 0;
      if (!this.config.enabled) {
        this.say("Voice is not configured for this directory.");
        return;
      }
      this.replies.disabled = false;
      if (this.config.microphone_available === false) {
        this.say("Microphone transcription is unavailable. Text and spoken replies remain available.");
        return;
      }
      if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
        this.say("Microphone recording needs Chrome over HTTPS.");
        return;
      }
      this.talk.disabled = false;
      this.say("Enable the microphone, then hold Talk to record and send.");
    } catch (error) {
      if (!this.disposed) this.say(error.message);
    }
  }
  async enable() {
    if (this.opening || this.disposed) return;
    this.opening = true;
    this.talk.disabled = true;
    const setup = new AbortController();
    this.microphoneSetupAbort = setup;
    try {
      await this.transport.prepareMicrophone?.(setup.signal);
      if (this.disposed || setup.signal.aborted) return;
      const stream = await navigator.mediaDevices.getUserMedia({ audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true
      }, video: false });
      stream.getTracks().forEach((track) => track.stop());
      if (this.disposed) return;
      this.microphoneReady = true;
      this.talk.textContent = "Hold Talk";
      this.say("Ready. Release Talk to send your words to Truman.");
    } catch (error) {
      if (!this.disposed) this.say(error.message || "Microphone access was denied or unavailable.");
    } finally {
      this.microphoneSetupAbort = void 0;
      this.opening = false;
      if (!this.disposed) this.talk.disabled = false;
    }
  }
  async start() {
    if (!this.config?.enabled || this.opening || this.recording || this.transcribing || this.disposed) return;
    this.stopAudio();
    this.opening = true;
    this.released = false;
    this.discard = false;
    const capture = ++this.capture;
    const context = structuredClone(this.context());
    this.say("Opening microphone\u2026");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true
      }, video: false });
      if (this.disposed || capture !== this.capture || this.discard) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      this.stream = stream;
      const type = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"].find((value) => MediaRecorder.isTypeSupported(value));
      if (!type) throw new Error("This browser cannot record a supported audio format.");
      const recorder = new MediaRecorder(stream, { mimeType: type });
      this.recorder = recorder;
      const chunks = [];
      let bytes = 0;
      recorder.addEventListener("dataavailable", (event) => {
        bytes += event.data.size;
        if (bytes > this.config.max_audio_bytes) {
          this.finish(true);
          this.say("Recording was too large. Try a shorter phrase.");
        } else if (event.data.size) chunks.push(event.data);
      });
      recorder.addEventListener("error", () => {
        this.finish(true);
        this.say("Microphone recording failed.");
      });
      recorder.addEventListener("stop", () => {
        clearTimeout(this.timer);
        stream.getTracks().forEach((track) => track.stop());
        this.stream = void 0;
        this.recorder = void 0;
        this.recording = false;
        if (!this.disposed) this.talk.setAttribute("aria-pressed", "false");
        if (this.discard || this.disposed || capture !== this.capture) return;
        const audio = new Blob(chunks, { type });
        if (!audio.size) {
          this.say("No audio recorded. Hold Talk while speaking.");
          return;
        }
        void this.submit(audio, context);
      }, { once: true });
      this.recording = true;
      this.opening = false;
      this.talk.setAttribute("aria-pressed", "true");
      recorder.start(250);
      this.timer = setTimeout(() => this.finish(), this.config.max_recording_seconds * 1e3);
      this.say("Recording. Release Talk to send.");
      if (this.released) this.finish();
    } catch (error) {
      this.stream?.getTracks().forEach((track) => track.stop());
      this.stream = void 0;
      this.recording = false;
      this.recorder = void 0;
      if (!this.disposed) {
        this.talk.setAttribute("aria-pressed", "false");
        if (error.name === "NotAllowedError") {
          this.microphoneReady = false;
          this.talk.textContent = "Enable microphone";
        }
        this.say(error.message || "Microphone recording failed.");
      }
    } finally {
      this.opening = false;
    }
  }
  finish(discard = false) {
    if (discard && (this.opening || this.recording) && !this.disposed) this.say("Recording cancelled.");
    this.released = true;
    this.discard ||= discard;
    clearTimeout(this.timer);
    if (this.recorder?.state === "recording") this.recorder.stop();
    this.stream?.getTracks().forEach((track) => track.stop());
  }
  async submit(audio, context) {
    this.transcribing = true;
    this.talk.disabled = true;
    const abort = new AbortController();
    this.transcriptionAbort = abort;
    const id = crypto.randomUUID();
    this.say("Transcribing\u2026");
    try {
      const result = await this.transport.transcribe(audio, id, abort.signal);
      if (this.disposed || abort.signal.aborted) return;
      await this.transport.send(result.text, "voice-" + id, context);
      this.say("Voice message sent.");
    } catch (error) {
      if (!this.disposed) this.say(error.message || "Voice request failed.");
    } finally {
      this.transcribing = false;
      if (!this.disposed) this.talk.disabled = false;
      this.transcriptionAbort = void 0;
      void this.play();
    }
  }
  messages(rows) {
    for (const message of rows) {
      if (this.seen.has(message.id)) continue;
      this.seen.add(message.id);
      if (this.replies.checked && message.seq > this.speakAfter && message.reply_to && message.actor.startsWith("agent:"))
        this.pending.push(message.id);
    }
    void this.play();
  }
  async play() {
    if (this.playing || this.opening || this.recording || this.transcribing || this.disposed || !this.replies.checked) return;
    this.playing = true;
    const playback = this.playback;
    try {
      while (this.pending.length && playback === this.playback && !this.disposed && this.replies.checked) {
        const message = this.pending.shift();
        const abort = new AbortController();
        this.audioAbort = abort;
        this.stop.disabled = false;
        this.say("Preparing spoken reply\u2026");
        let url;
        try {
          const blob = await this.transport.speech(message, crypto.randomUUID(), abort.signal);
          if (abort.signal.aborted || playback !== this.playback || this.disposed) break;
          url = URL.createObjectURL(blob);
          const audio = new Audio(url);
          this.audio = audio;
          this.say("Speaking. Talk or Stop audio to interrupt.");
          await new Promise((resolve, reject) => {
            this.finishAudio = resolve;
            audio.onended = () => resolve();
            audio.onerror = () => reject(new Error("Spoken audio could not be played."));
            void audio.play().catch(() => reject(new Error("Audio playback was blocked. Toggle Speak replies and try again.")));
          });
        } catch (error) {
          if (!abort.signal.aborted && !this.disposed) this.say(error.message);
        } finally {
          this.finishAudio = void 0;
          this.audio = void 0;
          if (url) URL.revokeObjectURL(url);
        }
      }
    } finally {
      this.playing = false;
      if (!this.disposed) this.stop.disabled = true;
      this.audioAbort = void 0;
    }
  }
  stopAudio() {
    this.playback += 1;
    this.pending = [];
    this.audioAbort?.abort();
    this.audio?.pause();
    this.finishAudio?.();
    this.stop.disabled = true;
    if (!this.disposed) this.say("Audio stopped.");
  }
  dispose() {
    if (this.disposed) return;
    this.disposed = true;
    this.capture += 1;
    this.events.abort();
    this.microphoneSetupAbort?.abort();
    this.finish(true);
    this.transcriptionAbort?.abort();
    this.stopAudio();
    this.talk.disabled = true;
    this.replies.disabled = true;
    this.status.textContent = "Voice session ended.";
  }
};
async function voiceJson(response) {
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || "Voice session is unavailable.");
  return value;
}

// web/message-delivery.ts
var labels = {
  pending: "Sending\u2026",
  queued: "Queued for the next turn",
  steered: "Added to the active turn",
  started: "Delivered",
  delivery_unknown: "Delivery uncertain \u2014 check with your assistant before retrying",
  failed: "Delivery failed",
  cancelled: "Cancelled",
  answered: "Answered"
};
function messageDelivery(root, updates, retry) {
  const byId = new Map(updates.map((value) => [value.id, value]));
  for (const item of Array.from(root.querySelectorAll("[data-message-id]"))) {
    const update2 = byId.get(item.dataset.messageId);
    if (!update2) continue;
    let status = item.querySelector(".message-delivery");
    if (!status) {
      status = document.createElement("small");
      status.className = "message-delivery";
      status.setAttribute("role", "status");
      item.append(status);
    }
    const state = update2.status === "answered" ? "answered" : update2.delivery_status ?? "pending";
    status.dataset.delivery = state;
    const label = labels[state] ?? "Sending\u2026";
    if (status.textContent !== label) status.textContent = label;
    let button = item.querySelector(".retry-delivery");
    if (state === "delivery_unknown" && retry) {
      if (!button) {
        button = document.createElement("button");
        button.className = "retry-delivery";
        button.type = "button";
        button.textContent = "Retry message";
        item.append(button);
      }
      button.disabled = false;
      button.onclick = () => {
        button.disabled = true;
        retry(update2.id);
      };
    } else button?.remove();
  }
}

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
var voice;
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
    voice?.dispose();
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
    if (!message.actor.startsWith("agent:")) item.dataset.messageId = message.id;
    after = Math.max(after, message.seq);
  }
  voice?.messages(result.messages);
  messageDelivery(
    $("messages"),
    [...result.messages, ...result.message_updates ?? []],
    (id) => void api("/sessions/" + sessionId + "/retry-message", { message_id: id, request_id: crypto.randomUUID() }).then(messages).catch(showError)
  );
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
  const voicePath = "/sessions/" + sessionId + "/voice";
  async function voiceRequest(action, body, headers, signal) {
    const response = await fetch(base + voicePath + "/" + action, {
      method: "POST",
      credentials: "same-origin",
      redirect: "error",
      headers,
      body,
      signal
    });
    if (response.status === 401 || response.status === 410) voice?.dispose();
    return response;
  }
  voice = new VoiceControls($("voice-controls"), {
    info: () => api(voicePath + "/info"),
    transcribe: async (audio, id, signal) => voiceJson(await voiceRequest(
      "transcribe",
      audio,
      { "Content-Type": audio.type, "X-Voice-Request-ID": id },
      signal
    )),
    speech: async (message, id, signal) => {
      const response = await voiceRequest(
        "speech",
        JSON.stringify({ message_id: message, request_id: id }),
        { "Content-Type": "application/json" },
        signal
      );
      if (!response.ok) {
        await voiceJson(response);
        throw new Error("Speech unavailable.");
      }
      return response.blob();
    },
    send: async (text, id, context) => {
      await api("/sessions/" + sessionId + "/messages", {
        request_id: id,
        body: text,
        annotation: context
      });
      annotation = void 0;
      $("annotation").hidden = true;
      await messages();
    }
  }, () => annotation ?? null);
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
  settings.voice ??= {};
  input("voice-enabled").checked = settings.voice.enabled ?? false;
  input("voice-enabled").onchange = () => {
    settings.voice.enabled = input("voice-enabled").checked;
    syncAdvanced();
  };
  for (const [id, key, fallback] of [
    ["voice-provider", "api_base_url", "https://api.openai.com/v1"],
    ["voice-key", "api_key_env", "EVANDER_BROWSER_VOICE_API_KEY"],
    ["voice-stt", "transcription_model", "gpt-transcribe"],
    ["voice-tts", "speech_model", "gpt-4o-mini-tts"],
    ["voice-name", "voice", "coral"],
    ["voice-seconds", "max_recording_seconds", 45]
  ]) {
    input(id).value = String(settings.voice[key] ?? fallback);
    input(id).oninput = () => {
      settings.voice[key] = typeof fallback === "number" ? Number(input(id).value) : input(id).value;
      syncAdvanced();
    };
  }
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
