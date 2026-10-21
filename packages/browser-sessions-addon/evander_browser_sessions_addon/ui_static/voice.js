// web/composer.ts
function enterToSend(body, form) {
  body.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || event.shiftKey || event.isComposing || event.keyCode === 229) return;
    event.preventDefault();
    if (!event.repeat && body.value.trim()) form.requestSubmit();
  });
}

// web/pcm-player.ts
var PcmPlayer = class {
  context;
  cancel;
  async unlock() {
    const context = this.context ??= new AudioContext();
    await context.resume();
  }
  async play(response, signal, started) {
    const reader = response.body?.getReader();
    if (!reader) throw new Error("Spoken audio is unavailable.");
    const context = this.context ??= new AudioContext();
    const sources = /* @__PURE__ */ new Set();
    let cancelled = false, complete = false, first = true, trailing = new Uint8Array(0), total = 0;
    let next = 0;
    let drained;
    const ended2 = new Promise((resolve) => {
      drained = resolve;
    });
    const cancel = () => {
      cancelled = true;
      void reader.cancel().catch(() => {
      });
      for (const source of sources) source.stop();
      sources.clear();
      drained();
    };
    this.cancel = cancel;
    signal.addEventListener("abort", cancel, { once: true });
    try {
      if (signal.aborted) {
        cancel();
        return;
      }
      await context.resume();
      while (!cancelled) {
        const { value, done } = await reader.read();
        if (cancelled || signal.aborted) break;
        if (done) {
          if (trailing.length || !total) throw new Error("Spoken audio was incomplete.");
          complete = true;
          if (!sources.size) drained();
          await ended2;
          break;
        }
        total += value.byteLength;
        if (total > 8e6) throw new Error("Spoken audio exceeded its limit.");
        const bytes = new Uint8Array(trailing.length + value.length);
        bytes.set(trailing);
        bytes.set(value, trailing.length);
        const count = Math.floor(bytes.length / 2);
        trailing = bytes.slice(count * 2);
        if (!count) continue;
        const buffer = context.createBuffer(1, count, 24e3);
        const samples = buffer.getChannelData(0), data = new DataView(bytes.buffer);
        for (let i = 0; i < count; i++) samples[i] = data.getInt16(i * 2, true) / 32768;
        const source = context.createBufferSource();
        source.buffer = buffer;
        source.connect(context.destination);
        sources.add(source);
        source.onended = () => {
          source.disconnect();
          sources.delete(source);
          if (complete && !sources.size) drained();
        };
        next = Math.max(next, context.currentTime + 0.05);
        source.start(next);
        next += buffer.duration;
        if (first) {
          first = false;
          started();
        }
      }
    } finally {
      if (!complete || cancelled) cancel();
      signal.removeEventListener("abort", cancel);
      reader.releaseLock();
      if (this.cancel === cancel) this.cancel = void 0;
    }
  }
  stop() {
    this.cancel?.();
  }
  dispose() {
    this.stop();
    void this.context?.close();
    this.context = void 0;
  }
};

// web/voice-controls.ts
var VoiceControls = class {
  constructor(root, transport, context = () => null) {
    this.transport = transport;
    this.context = context;
    this.talk = root.querySelector("[data-voice-talk]");
    this.replies = root.querySelector("[data-voice-replies]");
    this.stop = root.querySelector("[data-voice-stop]");
    this.microphoneOff = document.createElement("button");
    this.microphoneOff.type = "button";
    this.microphoneOff.className = "quiet";
    this.microphoneOff.textContent = "Turn off microphone";
    this.microphoneOff.hidden = true;
    this.talk.after(this.microphoneOff);
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
    watch(this.talk, "blur", () => {
      const capturing = this.recording || this.opening;
      this.finish(true);
      if (capturing) this.releaseMicrophone();
    });
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
      else if (typeof AudioContext !== "undefined") void this.pcm.unlock().catch(() => this.say("Audio playback needs permission."));
    });
    watch(this.stop, "click", () => this.stopAudio());
    watch(this.microphoneOff, "click", () => {
      this.finish(true);
      this.releaseMicrophone();
      this.say("");
    });
    window.addEventListener("blur", () => {
      this.finish(true);
      this.releaseMicrophone();
    }, { signal: this.events.signal });
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) {
        this.finish(true);
        this.releaseMicrophone();
      }
    }, { signal: this.events.signal });
    window.addEventListener("pagehide", () => this.dispose(), { once: true, signal: this.events.signal });
    this.say("");
    void this.initialize();
  }
  talk;
  replies;
  stop;
  microphoneOff;
  pcm = new PcmPlayer();
  status;
  config;
  events = new AbortController();
  microphoneSetupAbort;
  microphoneReady = false;
  microphoneGeneration = 0;
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
    if (!this.disposed) {
      this.status.textContent = text;
      this.status.hidden = !text;
    }
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
      this.say("");
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
      const generation = this.microphoneGeneration;
      const stream = await navigator.mediaDevices.getUserMedia({ audio: {
        echoCancellation: true,
        noiseSuppression: false,
        autoGainControl: false
      }, video: false });
      if (this.disposed || setup.signal.aborted || document.hidden || generation !== this.microphoneGeneration) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      this.stream = stream;
      this.microphoneReady = true;
      this.microphoneOff.hidden = false;
      this.talk.textContent = "Hold Talk";
      this.say("");
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
      const stream = this.stream;
      if (!stream?.getAudioTracks().some((track) => track.readyState === "live")) {
        this.releaseMicrophone();
        this.say("Enable the microphone before holding Talk.");
        return;
      }
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
      this.say("Recording.");
      if (this.released) this.finish();
    } catch (error) {
      this.releaseMicrophone();
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
  }
  releaseMicrophone() {
    this.microphoneGeneration += 1;
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = void 0;
    this.microphoneReady = false;
    this.microphoneOff.hidden = true;
    if (!this.disposed) this.talk.textContent = "Enable microphone";
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
          const result = await this.transport.speech(message, crypto.randomUUID(), abort.signal);
          if (abort.signal.aborted || playback !== this.playback || this.disposed) {
            if (result instanceof Response) await result.body?.cancel();
            break;
          }
          if (result instanceof Response && result.headers.get("Content-Type")?.split(";")[0] === "audio/pcm") {
            await this.pcm.play(result, abort.signal, () => this.say("Speaking."));
            continue;
          }
          const blob = result instanceof Response ? await result.blob() : result;
          if (abort.signal.aborted || playback !== this.playback || this.disposed) break;
          url = URL.createObjectURL(blob);
          const audio = new Audio(url);
          this.audio = audio;
          this.say("Speaking.");
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
    this.pcm.stop();
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
    this.releaseMicrophone();
    this.transcriptionAbort?.abort();
    this.stopAudio();
    this.microphoneOff.remove();
    this.pcm.dispose();
    this.talk.disabled = true;
    this.replies.disabled = true;
    this.status.hidden = false;
    this.status.textContent = "Voice session ended.";
  }
};
async function voiceJson(response) {
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || "Voice session is unavailable.");
  return value;
}

// web/voice-page.ts
var $ = (id) => document.getElementById(id);
var token = new URLSearchParams(location.hash.slice(1)).get("token");
history.replaceState(null, "", location.pathname);
var after = 0;
var ended = false;
var refreshing = false;
var refreshAgain = false;
var displayedMessages = /* @__PURE__ */ new Set();
function fail(error) {
  $("error").textContent = error.message || "Voice session unavailable.";
}
async function request(action, body, options = {}) {
  if (!token) throw new Error("Open a new voice page from the sharing extension.");
  const response = await fetch("/_browser/guest/voice/" + action, {
    credentials: "omit",
    redirect: "error",
    ...options,
    method: options.method ?? (body ? "POST" : "GET"),
    headers: { "Authorization": "Bearer " + token, ...body ? { "Content-Type": "application/json" } : {}, ...options.headers },
    body: body ? JSON.stringify(body) : options.body
  });
  if (response.status === 401 || response.status === 410) {
    ended = true;
    voice.dispose();
    throw new Error("Voice session ended. Reopen voice chat from the sharing extension.");
  }
  return response;
}
var voice = new VoiceControls($("voice-controls"), {
  info: async () => voiceJson(await request("info")),
  transcribe: async (audio, id, signal) => voiceJson(await request("transcribe", void 0, {
    method: "POST",
    body: audio,
    signal,
    headers: { "Content-Type": audio.type, "X-Voice-Request-ID": id }
  })),
  speech: async (message, id, signal) => {
    const response = await request("speech", { message_id: message, request_id: id, stream: typeof AudioContext !== "undefined" }, { signal });
    if (!response.ok) {
      await voiceJson(response);
      throw new Error("Speech unavailable.");
    }
    return response;
  },
  send: async (text, id) => {
    await voiceJson(await request("message", { request_id: id, body: text }));
    await refresh();
  }
});
async function refresh() {
  if (ended) return;
  if (refreshing) {
    refreshAgain = true;
    return;
  }
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
      $("messages").append(paragraph);
      displayedMessages.add(message.id);
    }
    voice.messages(result.messages);
    if (["closed", "expired", "failed", "cleanup_failed"].includes(result.session.state)) {
      ended = true;
      voice.dispose();
    }
  } catch (error) {
    fail(error);
  } finally {
    refreshing = false;
    if (refreshAgain) {
      refreshAgain = false;
      void refresh();
    }
  }
}
enterToSend($("body"), $("chat"));
$("chat").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!$("body").value.trim()) return;
  try {
    const body = $("body");
    await voiceJson(await request("message", { request_id: crypto.randomUUID(), body: body.value }));
    body.value = "";
    await refresh();
  } catch (error) {
    fail(error);
  }
});
void refresh();
setInterval(() => void refresh(), 2e3);
