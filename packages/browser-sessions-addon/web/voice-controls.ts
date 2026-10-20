export type VoiceInfo = { enabled: boolean; microphone_available?: boolean; max_recording_seconds: number; max_audio_bytes: number; latest_message_seq?: number };
export type VoiceTransport = {
  prepareMicrophone?: (signal: AbortSignal) => Promise<void>;
  info: () => Promise<VoiceInfo>;
  transcribe: (audio: Blob, id: string, signal: AbortSignal) => Promise<{ text: string; request_id: string }>;
  speech: (message: string, id: string, signal: AbortSignal) => Promise<Blob>;
  send: (text: string, id: string, context: any) => Promise<void>;
};
export class VoiceControls {
  private talk: HTMLButtonElement;
  private replies: HTMLInputElement;
  private stop: HTMLButtonElement;
  private status: HTMLElement;
  private config?: VoiceInfo;
  private events = new AbortController();
  private microphoneSetupAbort?: AbortController;
  private microphoneReady = false;
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
  private pending: string[] = [];
  private seen = new Set<string>();
  private speakAfter = Infinity;

  constructor(root: HTMLElement, private transport: VoiceTransport, private context = () => null) {
    this.talk = root.querySelector<HTMLButtonElement>("[data-voice-talk]")!;
    this.replies = root.querySelector<HTMLInputElement>("[data-voice-replies]")!;
    this.stop = root.querySelector<HTMLButtonElement>("[data-voice-stop]")!;
    this.status = root.querySelector<HTMLElement>("[data-voice-status]")!;
    const watch = <K extends keyof HTMLElementEventMap>(target: HTMLElement, name: K,
      listener: (event: HTMLElementEventMap[K]) => void) =>
      target.addEventListener(name, listener, { signal: this.events.signal });
    watch(this.talk, "pointerdown", event => {
      if (!this.microphoneReady || event.button !== 0) return;
      event.preventDefault(); this.talk.focus({ preventScroll: true }); this.talk.setPointerCapture(event.pointerId);
      void this.start();
    });
    watch(this.talk, "pointerup", () => this.finish());
    watch(this.talk, "pointercancel", () => this.finish(true));
    watch(this.talk, "blur", () => this.finish(true));
    watch(this.talk, "keydown", event => {
      if (!this.microphoneReady || ![" ", "Enter"].includes(event.key)) return;
      event.preventDefault(); if (!event.repeat) void this.start();
    });
    watch(this.talk, "keyup", event => {
      if (this.microphoneReady && [" ", "Enter"].includes(event.key)) {
        event.preventDefault(); this.finish();
      }
    });
    watch(this.talk, "click", event => {
      if (!this.microphoneReady) void this.enable();
      else if (event.detail === 0) {
        if (this.recording || this.opening) this.finish(); else void this.start();
      }
    });
    watch(this.replies, "change", () => {
      if (!this.replies.checked) this.stopAudio();
    });
    watch(this.stop, "click", () => this.stopAudio());
    window.addEventListener("blur", () => this.finish(true), { signal: this.events.signal });
    document.addEventListener("visibilitychange", () => { if (document.hidden) this.finish(true); }, { signal: this.events.signal });
    window.addEventListener("pagehide", () => this.dispose(), { once: true, signal: this.events.signal });
    void this.initialize();
  }
  private say(text: string) { if (!this.disposed) this.status.textContent = text; }
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
      this.talk.disabled = false;
      this.say("Enable the microphone, then hold Talk to record and send.");
    } catch (error: any) { if (!this.disposed) this.say(error.message); }
  }
  private async enable() {
    if (this.opening || this.disposed) return;
    this.opening = true; this.talk.disabled = true;
    const setup = new AbortController(); this.microphoneSetupAbort = setup;
    try {
      await this.transport.prepareMicrophone?.(setup.signal);
      if (this.disposed || setup.signal.aborted) return;
      const stream = await navigator.mediaDevices.getUserMedia({ audio: {
        echoCancellation: true, noiseSuppression: true, autoGainControl: true }, video: false });
      stream.getTracks().forEach(track => track.stop());
      if (this.disposed) return;
      this.microphoneReady = true;
      this.talk.textContent = "Hold Talk";
      this.say("Ready. Release Talk to send your words to Truman.");
    } catch (error: any) {
      if (!this.disposed) this.say(error.message || "Microphone access was denied or unavailable.");
    } finally {
      this.microphoneSetupAbort = undefined;
      this.opening = false; if (!this.disposed) this.talk.disabled = false;
    }
  }
  private async start() {
    if (!this.config?.enabled || this.opening || this.recording || this.transcribing || this.disposed) return;
    this.stopAudio();
    this.opening = true; this.released = false; this.discard = false;
    const capture = ++this.capture;
    const context = structuredClone(this.context());
    this.say("Opening microphone…");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: {
        echoCancellation: true, noiseSuppression: true, autoGainControl: true }, video: false });
      if (this.disposed || capture !== this.capture || this.discard) {
        stream.getTracks().forEach(track => track.stop()); return;
      }
      this.stream = stream;
      const type = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"].find(value => MediaRecorder.isTypeSupported(value));
      if (!type) throw new Error("This browser cannot record a supported audio format.");
      const recorder = new MediaRecorder(stream, { mimeType: type });
      this.recorder = recorder;
      const chunks: Blob[] = [];
      let bytes = 0;
      recorder.addEventListener("dataavailable", event => {
        bytes += event.data.size;
        if (bytes > this.config!.max_audio_bytes) {
          this.finish(true); this.say("Recording was too large. Try a shorter phrase.");
        } else if (event.data.size) chunks.push(event.data);
      });
      recorder.addEventListener("error", () => { this.finish(true); this.say("Microphone recording failed."); });
      recorder.addEventListener("stop", () => {
        clearTimeout(this.timer);
        stream.getTracks().forEach(track => track.stop());
        this.stream = undefined; this.recorder = undefined; this.recording = false;
        if (!this.disposed) this.talk.setAttribute("aria-pressed", "false");
        if (this.discard || this.disposed || capture !== this.capture) return;
        const audio = new Blob(chunks, { type });
        if (!audio.size) { this.say("No audio recorded. Hold Talk while speaking."); return; }
        void this.submit(audio, context);
      }, { once: true });
      this.recording = true; this.opening = false;
      this.talk.setAttribute("aria-pressed", "true");
      recorder.start(250);
      this.timer = setTimeout(() => this.finish(), this.config.max_recording_seconds * 1000);
      this.say("Recording. Release Talk to send.");
      if (this.released) this.finish();
    } catch (error: any) {
      this.stream?.getTracks().forEach(track => track.stop()); this.stream = undefined;
      this.recording = false; this.recorder = undefined;
      if (!this.disposed) {
        this.talk.setAttribute("aria-pressed", "false");
        if (error.name === "NotAllowedError") {
          this.microphoneReady = false; this.talk.textContent = "Enable microphone";
        }
        this.say(error.message || "Microphone recording failed.");
      }
    } finally { this.opening = false; }
  }
  private finish(discard = false) {
    if (discard && (this.opening || this.recording) && !this.disposed) this.say("Recording cancelled.");
    this.released = true; this.discard ||= discard;
    clearTimeout(this.timer);
    if (this.recorder?.state === "recording") this.recorder.stop();
    this.stream?.getTracks().forEach(track => track.stop());
  }
  private async submit(audio: Blob, context: any) {
    this.transcribing = true; this.talk.disabled = true;
    const abort = new AbortController(); this.transcriptionAbort = abort;
    const id = crypto.randomUUID();
    this.say("Transcribing…");
    try {
      const result = await this.transport.transcribe(audio, id, abort.signal);
      if (this.disposed || abort.signal.aborted) return;
      await this.transport.send(result.text, "voice-" + id, context);
      this.say("Voice message sent.");
    } catch (error: any) { if (!this.disposed) this.say(error.message || "Voice request failed."); }
    finally {
      this.transcribing = false;
      if (!this.disposed) this.talk.disabled = false;
      this.transcriptionAbort = undefined;
      void this.play();
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
    if (this.playing || this.opening || this.recording || this.transcribing || this.disposed || !this.replies.checked) return;
    this.playing = true;
    const playback = this.playback;
    try {
      while (this.pending.length && playback === this.playback && !this.disposed && this.replies.checked) {
        const message = this.pending.shift()!;
        const abort = new AbortController(); this.audioAbort = abort;
        this.stop.disabled = false; this.say("Preparing spoken reply…");
        let url: string | undefined;
        try {
          const blob = await this.transport.speech(message, crypto.randomUUID(), abort.signal);
          if (abort.signal.aborted || playback !== this.playback || this.disposed) break;
          url = URL.createObjectURL(blob);
          const audio = new Audio(url); this.audio = audio;
          this.say("Speaking. Talk or Stop audio to interrupt.");
          await new Promise<void>((resolve, reject) => {
            this.finishAudio = resolve;
            audio.onended = () => resolve();
            audio.onerror = () => reject(new Error("Spoken audio could not be played."));
            void audio.play().catch(() => reject(new Error("Audio playback was blocked. Toggle Speak replies and try again.")));
          });
        } catch (error: any) { if (!abort.signal.aborted && !this.disposed) this.say(error.message); }
        finally {
          this.finishAudio = undefined; this.audio = undefined;
          if (url) URL.revokeObjectURL(url);
        }
      }
    } finally {
      this.playing = false; if (!this.disposed) this.stop.disabled = true; this.audioAbort = undefined;
    }
  }
  stopAudio() {
    this.playback += 1; this.pending = [];
    this.audioAbort?.abort(); this.audio?.pause(); this.finishAudio?.();
    this.stop.disabled = true;
    if (!this.disposed) this.say("Audio stopped.");
  }
  dispose() {
    if (this.disposed) return;
    this.disposed = true; this.capture += 1;
    this.events.abort(); this.microphoneSetupAbort?.abort();
    this.finish(true); this.transcriptionAbort?.abort(); this.stopAudio();
    this.talk.disabled = true; this.replies.disabled = true;
    this.status.textContent = "Voice session ended.";
  }
}

export async function voiceJson(response: Response) {
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || "Voice session is unavailable.");
  return value;
}
