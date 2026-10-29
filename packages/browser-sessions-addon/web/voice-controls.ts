import { PcmPlayer } from "./pcm-player.js";
import { HandsFreeCapture } from "./hands-free.js";
export type VoiceInfo = { enabled: boolean; microphone_available?: boolean; max_recording_seconds: number; max_audio_bytes: number; latest_message_seq?: number };
export type VoiceTransport = {
  prepareMicrophone?: (signal: AbortSignal) => Promise<void>;
  info: () => Promise<VoiceInfo>;
  transcribe: (audio: Blob, id: string, signal: AbortSignal) => Promise<{ text: string; request_id: string }>;
  speech: (message: string, id: string, signal: AbortSignal) => Promise<Blob | Response>;
  send: (text: string, id: string, context: any) => Promise<void>;
};
export class VoiceControls {
  private talk: HTMLButtonElement;
  private replies: HTMLInputElement;
  private stop: HTMLButtonElement;
  private microphoneOff: HTMLButtonElement;
  private handsFree: HTMLInputElement;
  private handsFreeLabel: HTMLLabelElement;
  private autoCapture?: HandsFreeCapture;
  private autoSpeaking = false;
  private autoContext: any;
  private failed = false;
  private pcm = new PcmPlayer();
  private status: HTMLElement;
  private config?: VoiceInfo;
  private events = new AbortController();
  private microphoneSetupAbort?: AbortController;
  private microphoneReady = false;
  private microphoneGeneration = 0;
  private opening = false;
  private recording = false;
  private transcribing = false;
  private released = false;
  private discard = false;
  private disposed = false;
  private recorder?: MediaRecorder;
  private stream?: MediaStream;
  private timer?: ReturnType<typeof setTimeout>;
  private capture = 0;
  private audio?: HTMLAudioElement;
  private audioAbort?: AbortController;
  private transcriptionAbort?: AbortController;
  private finishAudio?: () => void;
  private playback = 0;
  private playing = false;
  private clips: {audio: Blob; context: any}[] = [];
  private pending: string[] = [];
  private seen = new Set<string>();
  private speakAfter = Infinity;

  constructor(root: HTMLElement, private transport: VoiceTransport, private context = () => null) {
    this.talk = root.querySelector<HTMLButtonElement>("[data-voice-talk]")!;
    this.replies = root.querySelector<HTMLInputElement>("[data-voice-replies]")!;
    this.stop = root.querySelector<HTMLButtonElement>("[data-voice-stop]")!;
    this.microphoneOff = document.createElement("button");
    this.microphoneOff.type = "button"; this.microphoneOff.className = "quiet";
    this.microphoneOff.textContent = "Turn off microphone"; this.microphoneOff.hidden = true;
    this.talk.after(this.microphoneOff);
    this.handsFreeLabel = document.createElement("label"); this.handsFreeLabel.className = "check voice-replies";
    this.handsFree = document.createElement("input"); this.handsFree.type = "checkbox"; this.handsFree.disabled = true;
    this.handsFree.dataset.voiceHandsFree = "";
    this.handsFreeLabel.append(this.handsFree, "Hands-free"); this.microphoneOff.after(this.handsFreeLabel);
    this.status = root.querySelector<HTMLElement>("[data-voice-status]")!;
    const watch = <K extends keyof HTMLElementEventMap>(target: HTMLElement, name: K,
      listener: (event: HTMLElementEventMap[K]) => void) =>
      target.addEventListener(name, listener, { signal: this.events.signal });
    watch(this.talk, "pointerdown", event => {
      if (!this.microphoneReady || this.handsFree.checked || event.button !== 0) return;
      event.preventDefault(); this.talk.focus({ preventScroll: true }); this.talk.setPointerCapture(event.pointerId);
      void this.start();
    });
    watch(this.talk, "pointerup", () => this.finish());
    watch(this.talk, "pointercancel", () => this.finish(true));
    watch(this.talk, "blur", () => { if (this.recording) this.finish(true); });
    watch(this.talk, "keydown", event => {
      if (!this.microphoneReady || this.handsFree.checked || ![" ", "Enter"].includes(event.key)) return;
      event.preventDefault(); if (!event.repeat) void this.start();
    });
    watch(this.talk, "keyup", event => {
      if (this.microphoneReady && !this.handsFree.checked && [" ", "Enter"].includes(event.key)) {
        event.preventDefault(); this.finish();
      }
    });
    watch(this.talk, "click", event => {
      if (!this.microphoneReady) void this.enable();
      else if (!this.handsFree.checked && event.detail === 0) {
        if (this.recording || this.opening) this.finish(); else void this.start();
      }
    });
    watch(this.handsFree, "change", () => {
      this.failed = false; this.finish(true); this.stopHandsFree(); this.renderMicrophone();
      if (this.handsFree.checked && this.microphoneReady) void this.startHandsFree();
      else this.idleStatus();
    });
    watch(this.replies, "change", () => {
      if (!this.replies.checked) this.stopAudio();
      else if (typeof AudioContext !== "undefined") void this.pcm.unlock().catch(() => this.say("Audio playback needs permission."));
    });
    watch(this.stop, "click", () => this.stopAudio());
    watch(this.microphoneOff, "click", () => {
      this.failed = false; this.finish(true); this.releaseMicrophone(); this.say("");
    });
    window.addEventListener("blur", () => {
      if (this.recording) this.finish(true);
      // Keep an enabled device warm. Only invalidate a still-pending acquisition.
      if (!this.microphoneReady) this.microphoneGeneration += 1;
    }, { signal: this.events.signal });
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) { this.finish(true); this.releaseMicrophone(); }
    }, { signal: this.events.signal });
    window.addEventListener("pagehide", () => this.dispose(), { once: true, signal: this.events.signal });
    this.say(""); void this.initialize();
  }
  private say(text: string) {
    if (!this.disposed) { this.status.textContent = text; this.status.hidden = !text; }
  }
  private reportError(text: string) { this.failed = true; this.say(text); }
  private idleStatus() {
    if (!this.failed && this.status.textContent !== "Audio stopped." && !this.playing && !this.opening && !this.recording && !this.transcribing && !this.autoSpeaking)
      this.say(this.handsFree.checked && this.microphoneReady ? "Listening." : "");
  }
  private renderMicrophone() {
    if (this.disposed) return;
    this.talk.disabled = !this.config?.enabled || this.config.microphone_available === false ||
      !navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined" ||
      this.opening || this.transcribing || (this.handsFree.checked && this.microphoneReady);
    this.talk.textContent = !this.microphoneReady ? "Enable microphone" : this.handsFree.checked ? "Listening" : "Hold Talk";
    this.talk.setAttribute("aria-pressed", String(this.recording || this.autoSpeaking));
    this.microphoneOff.hidden = !this.microphoneReady;
  }
  private async initialize() {
    try {
      this.config = await this.transport.info();
      if (this.disposed) return;
      this.speakAfter = this.config.latest_message_seq ?? 0;
      if (!this.config.enabled) { this.say("Voice is not configured for this directory."); return; }
      this.replies.disabled = false;
      if (this.config.microphone_available === false) {
        this.say("Microphone transcription is unavailable. Text and spoken replies remain available."); return;
      }
      if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
        this.say("Microphone recording needs Chrome over HTTPS."); return;
      }
      this.handsFree.disabled = typeof AudioWorkletNode === "undefined";
      this.renderMicrophone(); this.say("");
    } catch (error: any) { if (!this.disposed) this.say(error.message); }
  }
  private async enable() {
    if (this.opening || this.disposed) return;
    this.failed = false; this.opening = true; this.renderMicrophone();
    const setup = new AbortController(); this.microphoneSetupAbort = setup;
    try {
      await this.transport.prepareMicrophone?.(setup.signal);
      if (this.disposed || setup.signal.aborted) return;
      const generation = this.microphoneGeneration;
      const stream = await navigator.mediaDevices.getUserMedia({ audio: {
        echoCancellation: true, noiseSuppression: false, autoGainControl: false, channelCount: 1 }, video: false });
      if (this.disposed || setup.signal.aborted || document.hidden || generation !== this.microphoneGeneration) {
        stream.getTracks().forEach(track => track.stop()); return;
      }
      this.stream = stream; this.microphoneReady = true;
      for (const track of stream.getAudioTracks()) track.addEventListener("ended", () => {
        if (this.stream !== stream) return;
        this.finish(true); this.releaseMicrophone();
        this.reportError("Microphone disconnected. Enable it to reconnect.");
      }, {once: true, signal: this.events.signal});
      this.say("");
      if (this.handsFree.checked) await this.startHandsFree();
    } catch (error: any) {
      if (!this.disposed) this.reportError(error.message || "Microphone access was denied or unavailable.");
    } finally {
      this.microphoneSetupAbort = undefined;
      this.opening = false; this.renderMicrophone(); this.idleStatus();
    }
  }
  private async startHandsFree() {
    if (this.disposed || !this.stream || !this.config || !this.handsFree.checked || this.autoCapture) return;
    const capture = new HandsFreeCapture(); this.autoCapture = capture;
    try {
      await capture.start(this.stream, this.config.max_recording_seconds, this.config.max_audio_bytes,
        () => {
          if (this.autoCapture !== capture || this.disposed) return;
          this.failed = false; this.autoContext = structuredClone(this.context());
          this.autoSpeaking = true; this.stopAudio(); this.say("Recording."); this.renderMicrophone();
        }, (audio, continuing) => {
          if (this.autoCapture !== capture || this.disposed) return;
          this.autoSpeaking = !!continuing; this.renderMicrophone();
          if (audio) this.queueClip(audio, this.autoContext);
          else { this.idleStatus(); void this.play(); }
        });
      if (this.autoCapture === capture) this.idleStatus();
    } catch (error: any) {
      if (this.autoCapture !== capture || this.disposed) return;
      this.stopHandsFree(); this.handsFree.checked = false; this.renderMicrophone();
      this.reportError(error.message || "Hands-free recording is unavailable.");
    }
  }
  private stopHandsFree() {
    const capture = this.autoCapture; this.autoCapture = undefined;
    this.autoSpeaking = false; capture?.stop();
  }
  private async start() {
    if (!this.config?.enabled || this.opening || this.recording || this.transcribing || this.disposed || this.handsFree.checked) return;
    this.failed = false; this.stopAudio();
    this.opening = true; this.released = false; this.discard = false;
    const capture = ++this.capture;
    const context = structuredClone(this.context());
    this.say("Opening microphone…");
    try {
      const stream = this.stream;
      if (!stream?.getAudioTracks().some(track => track.readyState === "live")) {
        this.releaseMicrophone(); this.say("Enable the microphone before holding Talk."); return;
      }
      const type = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"].find(value => MediaRecorder.isTypeSupported(value));
      if (!type) throw new Error("This browser cannot record a supported audio format.");
      const recorder = new MediaRecorder(stream, { mimeType: type }); this.recorder = recorder;
      const chunks: Blob[] = []; let bytes = 0;
      recorder.addEventListener("dataavailable", event => {
        bytes += event.data.size;
        if (bytes > this.config!.max_audio_bytes) {
          this.finish(true); this.reportError("Recording was too large. Try a shorter phrase.");
        } else if (event.data.size) chunks.push(event.data);
      });
      recorder.addEventListener("error", () => { this.finish(true); this.reportError("Microphone recording failed."); });
      recorder.addEventListener("stop", () => {
        clearTimeout(this.timer); this.recorder = undefined; this.recording = false;
        this.renderMicrophone();
        if (this.discard || this.disposed || capture !== this.capture) {
          if (this.handsFree.checked) this.idleStatus(); return;
        }
        const audio = new Blob(chunks, { type });
        if (!audio.size) { this.reportError("No audio recorded. Hold Talk while speaking."); return; }
        this.queueClip(audio, context);
      }, { once: true });
      this.recording = true; this.opening = false; this.renderMicrophone();
      recorder.start(250);
      this.timer = setTimeout(() => this.finish(), this.config.max_recording_seconds * 1000);
      this.say("Recording.");
      if (this.released) this.finish();
    } catch (error: any) {
      this.releaseMicrophone(); this.recording = false; this.recorder = undefined;
      if (!this.disposed) this.reportError(error.message || "Microphone recording failed.");
    } finally { this.opening = false; this.renderMicrophone(); }
  }
  private finish(discard = false) {
    if (discard && this.recording && !this.disposed) this.say("Recording cancelled.");
    this.released = true; this.discard ||= discard;
    clearTimeout(this.timer);
    if (this.recorder?.state === "recording") this.recorder.stop();
  }
  private releaseMicrophone() {
    this.microphoneGeneration += 1; this.stopHandsFree();
    const stream = this.stream; this.stream = undefined; this.microphoneReady = false;
    stream?.getTracks().forEach(track => track.stop()); this.renderMicrophone();
  }
  private queueClip(audio: Blob, context: any) {
    if (this.disposed) return;
    if (this.clips.length >= 2) { this.reportError("Please wait for the recorded messages to finish sending."); return; }
    this.failed = false; this.clips.push({audio, context}); void this.submit();
  }
  private async submit() {
    if (this.transcribing || this.disposed) return;
    this.transcribing = true; this.renderMicrophone();
    try {
      while (this.clips.length && !this.disposed) {
        const {audio, context} = this.clips.shift()!;
        const abort = new AbortController(); this.transcriptionAbort = abort;
        const id = crypto.randomUUID(); this.say("Transcribing…");
        try {
          const result = await this.transport.transcribe(audio, id, abort.signal);
          if (this.disposed || abort.signal.aborted) return;
          await this.transport.send(result.text, "voice-" + id, context);
          this.say("Voice message sent.");
        } catch (error: any) {
          if (!this.disposed && !abort.signal.aborted) this.reportError(error.message || "Voice request failed.");
        }
      }
    } finally {
      this.transcribing = false; this.transcriptionAbort = undefined;
      this.renderMicrophone(); this.idleStatus(); void this.play();
    }
  }
  messages(rows: any[]) {
    for (const message of rows) {
      if (this.seen.has(message.id)) continue;
      this.seen.add(message.id);
      if (this.replies.checked && message.seq > this.speakAfter && message.reply_to && message.actor.startsWith("agent:"))
        this.pending.push(message.id);
    }
    void this.play();
  }
  private async play() {
    if (!this.pending.length || this.playing || this.opening || this.recording || this.autoSpeaking || this.transcribing || this.disposed || !this.replies.checked) return;
    this.failed = false; this.playing = true; const playback = this.playback;
    try {
      while (this.pending.length && playback === this.playback && !this.disposed && this.replies.checked) {
        const message = this.pending.shift()!;
        const abort = new AbortController(); this.audioAbort = abort;
        this.stop.disabled = false; this.say("Preparing spoken reply…");
        let url: string | undefined;
        try {
          const result = await this.transport.speech(message, crypto.randomUUID(), abort.signal);
          if (abort.signal.aborted || playback !== this.playback || this.disposed) {
            if (result instanceof Response) await result.body?.cancel();
            break;
          }
          if (result instanceof Response && result.headers.get("Content-Type")?.split(";")[0] === "audio/pcm") {
            await this.pcm.play(result, abort.signal, () => this.say("Speaking.")); continue;
          }
          const blob = result instanceof Response ? await result.blob() : result;
          if (abort.signal.aborted || playback !== this.playback || this.disposed) break;
          url = URL.createObjectURL(blob); const audio = new Audio(url); this.audio = audio;
          this.say("Speaking.");
          await new Promise<void>((resolve, reject) => {
            this.finishAudio = resolve; audio.onended = () => resolve();
            audio.onerror = () => reject(new Error("Spoken audio could not be played."));
            void audio.play().catch(() => reject(new Error("Audio playback was blocked. Toggle Speak replies and try again.")));
          });
        } catch (error: any) { if (!abort.signal.aborted && !this.disposed) this.reportError(error.message); }
        finally { this.finishAudio = undefined; this.audio = undefined; if (url) URL.revokeObjectURL(url); }
      }
    } finally {
      this.playing = false; if (!this.disposed) this.stop.disabled = true;
      this.audioAbort = undefined; this.idleStatus();
    }
  }
  stopAudio() {
    this.playback += 1; this.pending = [];
    this.audioAbort?.abort(); this.audio?.pause(); this.finishAudio?.(); this.pcm.stop();
    this.stop.disabled = true; if (!this.disposed) this.say("Audio stopped.");
  }
  dispose() {
    if (this.disposed) return;
    this.disposed = true; this.capture += 1; this.clips = [];
    this.events.abort(); this.microphoneSetupAbort?.abort();
    this.finish(true); this.releaseMicrophone(); this.transcriptionAbort?.abort(); this.stopAudio();
    this.microphoneOff.remove(); this.handsFreeLabel.remove(); this.pcm.dispose();
    this.talk.disabled = true; this.replies.disabled = true;
    this.status.hidden = false; this.status.textContent = "Voice session ended.";
  }
}
export async function voiceJson(response: Response) {
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || "Voice session is unavailable.");
  return value;
}
