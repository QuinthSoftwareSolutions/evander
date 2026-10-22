// web/composer.ts
function enterToSend(body, form) {
  body.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || event.shiftKey || event.isComposing || event.keyCode === 229) return;
    event.preventDefault();
    if (!event.repeat && body.value.trim()) form.requestSubmit();
  });
}

// ../../frontend/admin-web/src/design/addon-context.js
var root = document.documentElement;
root.dataset.adminDesign = "";
var systemTheme = window.matchMedia("(prefers-color-scheme: dark)");
var hostTheme = false;
var privilegedUnlocked = new URLSearchParams(window.location.search).get("privileged_unlocked") === "true";
var privilegeListeners = /* @__PURE__ */ new Set();
function stored(key) {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}
function applyAppearance(data) {
  if (data.theme === "light" || data.theme === "dark") {
    root.dataset.theme = data.theme;
    root.style.colorScheme = data.theme;
  }
}
applyAppearance({
  theme: stored("admin-web:theme") ?? (systemTheme.matches ? "dark" : "light")
});
systemTheme.addEventListener("change", (event) => {
  if (!hostTheme && !stored("admin-web:theme")) applyAppearance({ theme: event.matches ? "dark" : "light" });
});
window.addEventListener("message", (event) => {
  if (window.parent === window || event.source !== window.parent || event.origin !== window.location.origin) return;
  const data = event.data;
  if (!data || typeof data !== "object" || data.type !== "admin-addon-context") return;
  hostTheme = data.theme === "light" || data.theme === "dark";
  applyAppearance(data);
  if (typeof data.privileged_operations?.unlocked === "boolean") {
    privilegedUnlocked = data.privileged_operations.unlocked;
    for (const listener of privilegeListeners) listener(privilegedUnlocked);
  }
});
if (window.parent !== window) window.parent.postMessage({ type: "admin-addon-ready" }, window.location.origin);

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
    const rate = 24e3;
    let cancelled = false, complete = false, first = true, trailing = new Uint8Array(0), total = 0;
    let next = 0, pending = [], pendingSamples = 0;
    let drained;
    const ended = new Promise((resolve) => {
      drained = resolve;
    });
    const schedule = () => {
      if (cancelled || !pendingSamples) return;
      const buffering = first || !sources.size;
      if (!complete && pendingSamples < rate * (buffering ? 1.5 : 0.1)) return;
      const buffer = context.createBuffer(1, pendingSamples, rate);
      const samples = buffer.getChannelData(0);
      let offset = 0;
      for (const chunk of pending) {
        samples.set(chunk, offset);
        offset += chunk.length;
      }
      pending = [];
      pendingSamples = 0;
      const source = context.createBufferSource();
      source.buffer = buffer;
      source.connect(context.destination);
      sources.add(source);
      source.onended = () => {
        source.disconnect();
        sources.delete(source);
        schedule();
        if (complete && !sources.size && !pendingSamples) drained();
      };
      next = buffering ? context.currentTime + 0.08 : Math.max(next, context.currentTime + 0.02);
      source.start(next);
      next += buffer.duration;
      if (first) {
        first = false;
        started();
      }
    };
    const cancel = () => {
      cancelled = true;
      pending = [];
      pendingSamples = 0;
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
          schedule();
          if (!sources.size) drained();
          await ended;
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
        const samples = new Float32Array(count), data = new DataView(bytes.buffer);
        for (let i = 0; i < count; i++) samples[i] = data.getInt16(i * 2, true) / 32768;
        pending.push(samples);
        pendingSamples += samples.length;
        schedule();
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

// web/hands-free.ts
var SpeechSegmenter = class {
  constructor(rate, seconds, bytes, started, finished) {
    this.rate = rate;
    this.started = started;
    this.finished = finished;
    const outputSamples = Math.floor(Math.min(seconds * 16e3, Math.max(0, bytes - 44) / 2));
    this.maximum = Math.floor(outputSamples * rate / 16e3);
  }
  lead = [];
  leadSize = 0;
  chunks = [];
  size = 0;
  voiced = 0;
  quiet = 0;
  active = false;
  continuing = false;
  announced = false;
  floor = 2e-3;
  maximum;
  push(samples) {
    if (!samples.length || this.maximum < this.rate * 0.2) return;
    if (this.active && samples.length > this.maximum - this.size) {
      const remaining = this.maximum - this.size;
      this.push(samples.subarray(0, remaining));
      this.push(samples.subarray(remaining));
      return;
    }
    let power = 0;
    for (const sample of samples) power += sample * sample;
    const rms = Math.sqrt(power / samples.length);
    const speech = rms > Math.max(0.012, this.floor * 3);
    if (!this.active && !speech) this.floor = Math.min(0.012, this.floor * 0.95 + rms * 0.05);
    if (!this.active) {
      this.lead.push(samples);
      this.leadSize += samples.length;
      while (this.lead.length > 1 && this.leadSize - this.lead[0].length >= this.rate * 0.35)
        this.leadSize -= this.lead.shift().length;
      if (!speech) return;
      this.active = true;
      this.chunks = this.lead;
      this.size = this.leadSize;
      this.lead = [];
      this.leadSize = 0;
      this.voiced = samples.length;
      this.quiet = 0;
    } else {
      this.chunks.push(samples);
      this.size += samples.length;
      if (speech) {
        this.voiced += samples.length;
        this.quiet = 0;
      } else this.quiet += samples.length;
    }
    if (!this.announced && this.voiced >= this.rate * 0.12) {
      this.announced = true;
      this.started();
    }
    if (this.quiet >= this.rate * 0.9) this.complete();
    else if (this.size >= this.maximum) this.complete(true);
  }
  complete(atLimit = false) {
    const trailingSilence = Math.min(this.quiet, this.size);
    const length = atLimit ? this.size : this.size - Math.max(0, trailingSilence - Math.floor(this.rate * 0.15));
    let audio;
    if ((this.voiced >= this.rate * 0.2 || this.continuing && this.voiced > 0) && length > 0) {
      const samples = new Float32Array(length);
      let offset = 0;
      for (const chunk of this.chunks) {
        const count = Math.min(chunk.length, length - offset);
        if (!count) break;
        samples.set(chunk.subarray(0, count), offset);
        offset += count;
      }
      audio = wav(samples, this.rate);
    }
    const continuing = atLimit && !!audio;
    if (continuing) {
      this.chunks = [];
      this.size = 0;
      this.voiced = 0;
      this.continuing = true;
    } else this.reset();
    this.finished(audio, continuing);
  }
  reset() {
    this.lead = [];
    this.leadSize = 0;
    this.chunks = [];
    this.size = 0;
    this.voiced = 0;
    this.quiet = 0;
    this.active = false;
    this.continuing = false;
    this.announced = false;
  }
};
function wav(samples, sourceRate) {
  const count = Math.floor(samples.length * 16e3 / sourceRate);
  const bytes = new ArrayBuffer(44 + count * 2), view = new DataView(bytes);
  const text = (offset, value) => {
    for (let i = 0; i < value.length; i++) view.setUint8(offset + i, value.charCodeAt(i));
  };
  text(0, "RIFF");
  view.setUint32(4, bytes.byteLength - 8, true);
  text(8, "WAVE");
  text(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, 16e3, true);
  view.setUint32(28, 32e3, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  text(36, "data");
  view.setUint32(40, count * 2, true);
  for (let i = 0; i < count; i++) {
    const first = Math.floor(i * sourceRate / 16e3);
    const end = Math.min(samples.length, Math.max(first + 1, Math.floor((i + 1) * sourceRate / 16e3)));
    let sum = 0;
    for (let j = first; j < end; j++) sum += samples[j];
    view.setInt16(44 + i * 2, Math.round(Math.max(-1, Math.min(1, sum / (end - first))) * 32767), true);
  }
  return new Blob([bytes], { type: "audio/wav" });
}
var HandsFreeCapture = class {
  context;
  source;
  node;
  stopped = false;
  segmenter;
  async start(stream, seconds, bytes, started, finished) {
    const context = this.context = new AudioContext({ sampleRate: 48e3 });
    await context.audioWorklet.addModule(new URL("./capture-worklet.js", import.meta.url));
    if (this.stopped) return;
    this.segmenter = new SpeechSegmenter(context.sampleRate, seconds, bytes, started, finished);
    this.source = context.createMediaStreamSource(stream);
    this.node = new AudioWorkletNode(context, "evander-microphone");
    this.node.port.onmessage = (event) => {
      if (!this.stopped) this.segmenter.push(event.data);
    };
    this.source.connect(this.node);
    this.node.connect(context.destination);
    await context.resume();
  }
  stop() {
    this.stopped = true;
    this.segmenter?.reset();
    if (this.node) {
      this.node.port.onmessage = null;
      this.node.port.close();
      this.node.disconnect();
    }
    this.source?.disconnect();
    void this.context?.close();
  }
};

// web/voice-controls.ts
var VoiceControls = class {
  constructor(root2, transport, context = () => null) {
    this.transport = transport;
    this.context = context;
    this.talk = root2.querySelector("[data-voice-talk]");
    this.replies = root2.querySelector("[data-voice-replies]");
    this.stop = root2.querySelector("[data-voice-stop]");
    this.microphoneOff = document.createElement("button");
    this.microphoneOff.type = "button";
    this.microphoneOff.className = "quiet";
    this.microphoneOff.textContent = "Turn off microphone";
    this.microphoneOff.hidden = true;
    this.talk.after(this.microphoneOff);
    this.handsFreeLabel = document.createElement("label");
    this.handsFreeLabel.className = "check voice-replies";
    this.handsFree = document.createElement("input");
    this.handsFree.type = "checkbox";
    this.handsFree.disabled = true;
    this.handsFree.dataset.voiceHandsFree = "";
    this.handsFreeLabel.append(this.handsFree, "Hands-free");
    this.microphoneOff.after(this.handsFreeLabel);
    this.status = root2.querySelector("[data-voice-status]");
    const watch = (target, name, listener) => target.addEventListener(name, listener, { signal: this.events.signal });
    watch(this.talk, "pointerdown", (event) => {
      if (!this.microphoneReady || this.handsFree.checked || event.button !== 0) return;
      event.preventDefault();
      this.talk.focus({ preventScroll: true });
      this.talk.setPointerCapture(event.pointerId);
      void this.start();
    });
    watch(this.talk, "pointerup", () => this.finish());
    watch(this.talk, "pointercancel", () => this.finish(true));
    watch(this.talk, "blur", () => {
      if (this.recording) this.finish(true);
    });
    watch(this.talk, "keydown", (event) => {
      if (!this.microphoneReady || this.handsFree.checked || ![" ", "Enter"].includes(event.key)) return;
      event.preventDefault();
      if (!event.repeat) void this.start();
    });
    watch(this.talk, "keyup", (event) => {
      if (this.microphoneReady && !this.handsFree.checked && [" ", "Enter"].includes(event.key)) {
        event.preventDefault();
        this.finish();
      }
    });
    watch(this.talk, "click", (event) => {
      if (!this.microphoneReady) void this.enable();
      else if (!this.handsFree.checked && event.detail === 0) {
        if (this.recording || this.opening) this.finish();
        else void this.start();
      }
    });
    watch(this.handsFree, "change", () => {
      this.failed = false;
      this.finish(true);
      this.stopHandsFree();
      this.renderMicrophone();
      if (this.handsFree.checked && this.microphoneReady) void this.startHandsFree();
      else this.idleStatus();
    });
    watch(this.replies, "change", () => {
      if (!this.replies.checked) this.stopAudio();
      else if (typeof AudioContext !== "undefined") void this.pcm.unlock().catch(() => this.say("Audio playback needs permission."));
    });
    watch(this.stop, "click", () => this.stopAudio());
    watch(this.microphoneOff, "click", () => {
      this.failed = false;
      this.finish(true);
      this.releaseMicrophone();
      this.say("");
    });
    window.addEventListener("blur", () => {
      if (this.recording) this.finish(true);
      if (!this.microphoneReady) this.microphoneGeneration += 1;
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
  handsFree;
  handsFreeLabel;
  autoCapture;
  autoSpeaking = false;
  autoContext;
  failed = false;
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
  clips = [];
  pending = [];
  seen = /* @__PURE__ */ new Set();
  speakAfter = Infinity;
  say(text) {
    if (!this.disposed) {
      this.status.textContent = text;
      this.status.hidden = !text;
    }
  }
  reportError(text) {
    this.failed = true;
    this.say(text);
  }
  idleStatus() {
    if (!this.failed && this.status.textContent !== "Audio stopped." && !this.playing && !this.opening && !this.recording && !this.transcribing && !this.autoSpeaking)
      this.say(this.handsFree.checked && this.microphoneReady ? "Listening." : "");
  }
  renderMicrophone() {
    if (this.disposed) return;
    this.talk.disabled = !this.config?.enabled || this.config.microphone_available === false || !navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined" || this.opening || this.transcribing || this.handsFree.checked && this.microphoneReady;
    this.talk.textContent = !this.microphoneReady ? "Enable microphone" : this.handsFree.checked ? "Listening" : "Hold Talk";
    this.talk.setAttribute("aria-pressed", String(this.recording || this.autoSpeaking));
    this.microphoneOff.hidden = !this.microphoneReady;
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
      this.handsFree.disabled = typeof AudioWorkletNode === "undefined";
      this.renderMicrophone();
      this.say("");
    } catch (error) {
      if (!this.disposed) this.say(error.message);
    }
  }
  async enable() {
    if (this.opening || this.disposed) return;
    this.failed = false;
    this.opening = true;
    this.renderMicrophone();
    const setup = new AbortController();
    this.microphoneSetupAbort = setup;
    try {
      await this.transport.prepareMicrophone?.(setup.signal);
      if (this.disposed || setup.signal.aborted) return;
      const generation = this.microphoneGeneration;
      const stream = await navigator.mediaDevices.getUserMedia({ audio: {
        echoCancellation: true,
        noiseSuppression: false,
        autoGainControl: false,
        channelCount: 1
      }, video: false });
      if (this.disposed || setup.signal.aborted || document.hidden || generation !== this.microphoneGeneration) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      this.stream = stream;
      this.microphoneReady = true;
      for (const track of stream.getAudioTracks()) track.addEventListener("ended", () => {
        if (this.stream !== stream) return;
        this.finish(true);
        this.releaseMicrophone();
        this.reportError("Microphone disconnected. Enable it to reconnect.");
      }, { once: true, signal: this.events.signal });
      this.say("");
      if (this.handsFree.checked) await this.startHandsFree();
    } catch (error) {
      if (!this.disposed) this.reportError(error.message || "Microphone access was denied or unavailable.");
    } finally {
      this.microphoneSetupAbort = void 0;
      this.opening = false;
      this.renderMicrophone();
      this.idleStatus();
    }
  }
  async startHandsFree() {
    if (this.disposed || !this.stream || !this.config || !this.handsFree.checked || this.autoCapture) return;
    const capture = new HandsFreeCapture();
    this.autoCapture = capture;
    try {
      await capture.start(
        this.stream,
        this.config.max_recording_seconds,
        this.config.max_audio_bytes,
        () => {
          if (this.autoCapture !== capture || this.disposed) return;
          this.failed = false;
          this.autoContext = structuredClone(this.context());
          this.autoSpeaking = true;
          this.stopAudio();
          this.say("Recording.");
          this.renderMicrophone();
        },
        (audio, continuing) => {
          if (this.autoCapture !== capture || this.disposed) return;
          this.autoSpeaking = !!continuing;
          this.renderMicrophone();
          if (audio) this.queueClip(audio, this.autoContext);
          else {
            this.idleStatus();
            void this.play();
          }
        }
      );
      if (this.autoCapture === capture) this.idleStatus();
    } catch (error) {
      if (this.autoCapture !== capture || this.disposed) return;
      this.stopHandsFree();
      this.handsFree.checked = false;
      this.renderMicrophone();
      this.reportError(error.message || "Hands-free recording is unavailable.");
    }
  }
  stopHandsFree() {
    const capture = this.autoCapture;
    this.autoCapture = void 0;
    this.autoSpeaking = false;
    capture?.stop();
  }
  async start() {
    if (!this.config?.enabled || this.opening || this.recording || this.transcribing || this.disposed || this.handsFree.checked) return;
    this.failed = false;
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
          this.reportError("Recording was too large. Try a shorter phrase.");
        } else if (event.data.size) chunks.push(event.data);
      });
      recorder.addEventListener("error", () => {
        this.finish(true);
        this.reportError("Microphone recording failed.");
      });
      recorder.addEventListener("stop", () => {
        clearTimeout(this.timer);
        this.recorder = void 0;
        this.recording = false;
        this.renderMicrophone();
        if (this.discard || this.disposed || capture !== this.capture) {
          if (this.handsFree.checked) this.idleStatus();
          return;
        }
        const audio = new Blob(chunks, { type });
        if (!audio.size) {
          this.reportError("No audio recorded. Hold Talk while speaking.");
          return;
        }
        this.queueClip(audio, context);
      }, { once: true });
      this.recording = true;
      this.opening = false;
      this.renderMicrophone();
      recorder.start(250);
      this.timer = setTimeout(() => this.finish(), this.config.max_recording_seconds * 1e3);
      this.say("Recording.");
      if (this.released) this.finish();
    } catch (error) {
      this.releaseMicrophone();
      this.recording = false;
      this.recorder = void 0;
      if (!this.disposed) this.reportError(error.message || "Microphone recording failed.");
    } finally {
      this.opening = false;
      this.renderMicrophone();
    }
  }
  finish(discard = false) {
    if (discard && this.recording && !this.disposed) this.say("Recording cancelled.");
    this.released = true;
    this.discard ||= discard;
    clearTimeout(this.timer);
    if (this.recorder?.state === "recording") this.recorder.stop();
  }
  releaseMicrophone() {
    this.microphoneGeneration += 1;
    this.stopHandsFree();
    const stream = this.stream;
    this.stream = void 0;
    this.microphoneReady = false;
    stream?.getTracks().forEach((track) => track.stop());
    this.renderMicrophone();
  }
  queueClip(audio, context) {
    if (this.disposed) return;
    if (this.clips.length >= 2) {
      this.reportError("Please wait for the recorded messages to finish sending.");
      return;
    }
    this.failed = false;
    this.clips.push({ audio, context });
    void this.submit();
  }
  async submit() {
    if (this.transcribing || this.disposed) return;
    this.transcribing = true;
    this.renderMicrophone();
    try {
      while (this.clips.length && !this.disposed) {
        const { audio, context } = this.clips.shift();
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
          if (!this.disposed && !abort.signal.aborted) this.reportError(error.message || "Voice request failed.");
        }
      }
    } finally {
      this.transcribing = false;
      this.transcriptionAbort = void 0;
      this.renderMicrophone();
      this.idleStatus();
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
    if (!this.pending.length || this.playing || this.opening || this.recording || this.autoSpeaking || this.transcribing || this.disposed || !this.replies.checked) return;
    this.failed = false;
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
          if (!abort.signal.aborted && !this.disposed) this.reportError(error.message);
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
      this.idleStatus();
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
    this.clips = [];
    this.events.abort();
    this.microphoneSetupAbort?.abort();
    this.finish(true);
    this.releaseMicrophone();
    this.transcriptionAbort?.abort();
    this.stopAudio();
    this.microphoneOff.remove();
    this.handsFreeLabel.remove();
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
function messageDelivery(root2, updates, retry) {
  const byId = new Map(updates.map((value) => [value.id, value]));
  for (const item of Array.from(root2.querySelectorAll("[data-message-id]"))) {
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
document.body.classList.toggle("viewer-page", !!sessionId);
var themeToggle = $("theme-toggle");
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
  try {
    localStorage.setItem("admin-web:theme", theme);
  } catch {
  }
});
new MutationObserver(syncThemeToggle).observe(document.documentElement, {
  attributes: true,
  attributeFilter: ["data-theme"]
});
syncThemeToggle();
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
var notificationTimer;
function dismissNotification() {
  clearTimeout(notificationTimer);
  $("error").hidden = true;
}
function showNotification(text, reminder = false) {
  clearTimeout(notificationTimer);
  $("error-message").textContent = text;
  $("error").dataset.kind = reminder ? "reminder" : "error";
  $("error").setAttribute("role", reminder ? "status" : "alert");
  $("error").hidden = false;
  if (reminder) notificationTimer = setTimeout(dismissNotification, 5e3);
}
$("dismiss-error").addEventListener("click", dismissNotification);
function showError(error) {
  showNotification(error.message ?? String(error));
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
  if (session.controller === actor && $("error").dataset.kind === "reminder") dismissNotification();
  $("session-title").textContent = session.title;
  $("session-title").title = session.title;
  $("stop-run").hidden = !session.active_run;
  $("run-progress").hidden = !session.active_run;
  $("run-progress").textContent = "Truman is working \xB7 " + (session.active_run?.step_count ?? 0) + " steps";
  $("status").textContent = session.state.replaceAll("_", " ");
  $("status").dataset.state = session.state;
  $("application-url").textContent = session.current_url ?? "Preparing application";
  $("expiry").textContent = "Expires " + new Date(session.expires_at * 1e3).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const yours = !!actor && session.controller === actor;
  const agent = session.controller?.startsWith("agent:");
  $("claim").textContent = yours ? "Hand back to Truman" : "Take control";
  $("claim").setAttribute("aria-pressed", String(yours));
  $("claim").disabled = !actor || ["closing", "closed", "expired", "failed", "cleanup_failed"].includes(session.state);
  $("control-help").textContent = yours ? "You control this browser. Click the application and type normally." : agent ? "Truman controls this browser. You can point, chat, or take control." : session.controller ? "Another viewer controls this browser. You can point, chat, or take control." : "Agent input is paused. Take control to use the application.";
  if (["closed", "expired", "cleanup_failed", "failed"].includes(session.state)) {
    voice?.dispose();
    $("empty").hidden = false;
    $("empty").querySelector("h2").textContent = session.state === "cleanup_failed" ? "Cleanup needs attention" : "Session unavailable";
    $("empty").querySelector("p").textContent = session.error ?? "Return to the session list or ask Truman to start another session.";
  }
}
function pageOptions(pages) {
  if (!Array.isArray(pages)) return;
  $("page-picker").hidden = pages.length < 2;
  const select = $("pages");
  const signature = JSON.stringify(pages);
  if (select.dataset.signature === signature) return;
  select.dataset.signature = signature;
  select.replaceChildren();
  for (const page of pages) {
    const option = document.createElement("option");
    option.value = page.id;
    option.textContent = page.url;
    option.selected = page.selected;
    select.append(option);
  }
}
$("pages").addEventListener("change", () => {
  observation = "";
  void api("/sessions/" + sessionId + "/select-page", { page_id: $("pages").value }).then((result) => pageOptions(result.pages)).catch(showError);
});
async function command(action, options = {}) {
  const result = await api("/sessions/" + sessionId + "/command", {
    id: crypto.randomUUID(),
    generation: session.control_generation,
    action,
    observation_id: observation || null,
    ...options
  });
  if (result.observation_id) observation = result.observation_id;
  if (result.pages) pageOptions(result.pages);
  return result;
}
function serializeInput(action, options = {}) {
  pendingInput = pendingInput.then(async () => {
    if (session?.controller !== actor) {
      showNotification("Take control before interacting", true);
      return;
    }
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
$("claim").addEventListener("click", () => void api("/sessions/" + sessionId + "/control", {
  action: session.controller === actor ? "handback" : "claim",
  expected_generation: session.control_generation
}).then(update).catch(showError));
$("pause").addEventListener("click", () => void api("/sessions/" + sessionId + "/control", {
  action: "pause",
  expected_generation: session.control_generation
}).then(update).catch(showError));
$("extend").addEventListener("click", () => void api("/sessions/" + sessionId + "/extend", {
  expected_revision: session.revision
}).then(update).catch(showError));
$("close").addEventListener("click", () => void api("/sessions/" + sessionId + "/close", {}).then(update).catch(showError));
var refreshingMessages = false;
var refreshMessagesAgain = false;
var displayedMessages = /* @__PURE__ */ new Set();
async function messages() {
  if (refreshingMessages) {
    refreshMessagesAgain = true;
    return;
  }
  refreshingMessages = true;
  try {
    const result = await api("/sessions/" + sessionId + "/messages?after=" + after);
    if (result.session) update(result.session);
    let appended = false;
    for (const message of result.messages) {
      after = Math.max(after, message.seq);
      if (displayedMessages.has(message.id)) continue;
      const item = document.createElement("div");
      item.className = "message" + (message.actor.startsWith("agent:") ? " agent" : "");
      const name = document.createElement("strong");
      name.textContent = message.actor.startsWith("agent:") ? "TRUMAN" : message.actor.replace(/^user:/, "");
      item.append(name, document.createTextNode(message.body));
      $("messages").append(item);
      if (!message.actor.startsWith("agent:")) item.dataset.messageId = message.id;
      displayedMessages.add(message.id);
      appended = true;
    }
    voice?.messages(result.messages);
    messageDelivery(
      $("messages"),
      [...result.messages, ...result.message_updates ?? []],
      (id) => void api("/sessions/" + sessionId + "/retry-message", { message_id: id, request_id: crypto.randomUUID() }).then(messages).catch(showError)
    );
    if (appended) $("messages").scrollTop = $("messages").scrollHeight;
  } finally {
    refreshingMessages = false;
    if (refreshMessagesAgain) {
      refreshMessagesAgain = false;
      void messages().catch(showError);
    }
  }
}
enterToSend($("chat-body"), $("chat-form"));
$("chat-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!input("chat-body").value.trim()) return;
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
        JSON.stringify({ message_id: message, request_id: id, stream: typeof AudioContext !== "undefined" }),
        { "Content-Type": "application/json" },
        signal
      );
      if (!response.ok) {
        await voiceJson(response);
        throw new Error("Speech unavailable.");
      }
      return response;
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
      pageOptions(payload.pages);
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
  settings.fast_execution ??= { enabled: false, max_steps: 20, timeout_seconds: 30 };
  input("fast-enabled").checked = settings.fast_execution.enabled;
  input("fast-enabled").onchange = () => {
    settings.fast_execution.enabled = input("fast-enabled").checked;
    syncAdvanced();
  };
  for (const [id, key, fallback] of [["fast-steps", "max_steps", 20], ["fast-seconds", "timeout_seconds", 30]]) {
    input(id).value = String(settings.fast_execution[key] ?? fallback);
    input(id).oninput = () => {
      settings.fast_execution[key] = Number(input(id).value);
      syncAdvanced();
    };
  }
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
    ["voice-name", "voice", "onyx"],
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
  settings.profiles[name] = { image: "evander-browser-controller:latest", cpus: 1, memory_mib: 1536, readonly_mounts: {}, playwright_enabled: false };
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
var catalogSessions = [];
var closedStates = /* @__PURE__ */ new Set(["closed", "expired"]);
function renderCatalog() {
  const showClosed = input("show-closed").checked;
  const visible = catalogSessions.filter((item) => showClosed || !closedStates.has(item.state));
  $("session-list").replaceChildren();
  if (!visible.length) {
    const message = document.createElement("p");
    message.className = "muted";
    message.textContent = showClosed ? "No hosted sessions yet. Ask Truman to start a demonstration or preview." : "No active hosted sessions. Ask Truman to start a demonstration or preview.";
    $("session-list").append(message);
  }
  for (const item of [...visible].reverse()) {
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
    type.textContent = "Shared browser";
    const expiry = document.createElement("p");
    expiry.textContent = "Expires " + new Date(item.expires_at * 1e3).toLocaleString();
    card.append(status, title, type, expiry);
    $("session-list").append(card);
  }
}
input("show-closed").addEventListener("change", renderCatalog);
async function catalog() {
  $("catalog").hidden = false;
  $("directory-label").textContent = directory;
  $("catalog").prepend($("error"));
  document.querySelector(".top-actions").prepend(themeToggle);
  const result = await api("/sessions?directory=" + encodeURIComponent(directory));
  catalogSessions = result.sessions;
  renderCatalog();
}
void (sessionId ? openViewer() : catalog()).catch(showError);
$("stop-run").addEventListener("click", () => {
  if (session?.active_run) void api("/sessions/" + sessionId + "/cancel-run", {}).then(() => api("/sessions/" + sessionId)).then(update).catch(showError);
});
