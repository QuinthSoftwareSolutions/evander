/** Local speech detection; idle periods produce no uploads. */
export class SpeechSegmenter {
  private lead: Float32Array[] = [];
  private leadSize = 0;
  private chunks: Float32Array[] = [];
  private size = 0;
  private voiced = 0;
  private quiet = 0;
  private active = false;
  private floor = 0.002;
  private maximum: number;
  constructor(private rate: number, seconds: number, bytes: number,
    private started: () => void, private finished: (audio?: Blob) => void) {
    this.maximum = Math.floor(Math.min(seconds, Math.max(0, bytes - 44) / 32000) * rate);
  }
  push(samples: Float32Array) {
    if (!samples.length || this.maximum < this.rate * 0.2) return;
    let power = 0;
    for (const sample of samples) power += sample * sample;
    const rms = Math.sqrt(power / samples.length);
    const speech = rms > Math.max(0.012, this.floor * 3);
    if (!this.active && !speech) this.floor = Math.min(0.012, this.floor * 0.95 + rms * 0.05);
    if (!this.active) {
      this.lead.push(samples); this.leadSize += samples.length;
      while (this.lead.length > 1 && this.leadSize - this.lead[0].length >= this.rate * 0.35)
        this.leadSize -= this.lead.shift()!.length;
      if (!speech) return;
      this.active = true; this.chunks = this.lead; this.size = this.leadSize;
      this.lead = []; this.leadSize = 0; this.voiced = samples.length; this.quiet = 0;
    } else {
      this.chunks.push(samples); this.size += samples.length;
      if (speech) { this.voiced += samples.length; this.quiet = 0; }
      else this.quiet += samples.length;
    }
    if (this.voiced >= this.rate * 0.12 && this.voiced - (speech ? samples.length : 0) < this.rate * 0.12)
      this.started();
    if (this.quiet >= this.rate * 0.9 || this.size >= this.maximum) this.complete();
  }
  private complete() {
    // Retain a short tail, rather than uploading the whole silence detector interval.
    const length = Math.min(this.maximum, this.size - Math.max(0, this.quiet - Math.floor(this.rate * 0.15)));
    let audio: Blob | undefined;
    if (this.voiced >= this.rate * 0.2 && length > 0) {
      const samples = new Float32Array(length);
      let offset = 0;
      for (const chunk of this.chunks) {
        const count = Math.min(chunk.length, length - offset);
        if (!count) break;
        samples.set(chunk.subarray(0, count), offset); offset += count;
      }
      audio = wav(samples, this.rate);
    }
    this.reset(); this.finished(audio);
  }
  reset() {
    this.lead = []; this.leadSize = 0; this.chunks = []; this.size = 0;
    this.voiced = 0; this.quiet = 0; this.active = false;
  }
}
function wav(samples: Float32Array, sourceRate: number) {
  const count = Math.floor(samples.length * 16000 / sourceRate);
  const bytes = new ArrayBuffer(44 + count * 2), view = new DataView(bytes);
  const text = (offset: number, value: string) => {
    for (let i = 0; i < value.length; i++) view.setUint8(offset + i, value.charCodeAt(i));
  };
  text(0, "RIFF"); view.setUint32(4, bytes.byteLength - 8, true); text(8, "WAVE");
  text(12, "fmt "); view.setUint32(16, 16, true); view.setUint16(20, 1, true);
  view.setUint16(22, 1, true); view.setUint32(24, 16000, true); view.setUint32(28, 32000, true);
  view.setUint16(32, 2, true); view.setUint16(34, 16, true); text(36, "data");
  view.setUint32(40, count * 2, true);
  // Average source samples for each 16 kHz sample to reduce downsampling aliasing.
  for (let i = 0; i < count; i++) {
    const first = Math.floor(i * sourceRate / 16000);
    const end = Math.min(samples.length, Math.max(first + 1, Math.floor((i + 1) * sourceRate / 16000)));
    let sum = 0; for (let j = first; j < end; j++) sum += samples[j];
    view.setInt16(44 + i * 2, Math.round(Math.max(-1, Math.min(1, sum / (end - first))) * 32767), true);
  }
  return new Blob([bytes], {type: "audio/wav"});
}
export class HandsFreeCapture {
  private context?: AudioContext;
  private source?: MediaStreamAudioSourceNode;
  private node?: AudioWorkletNode;
  private stopped = false;
  private segmenter?: SpeechSegmenter;
  async start(stream: MediaStream, seconds: number, bytes: number,
    started: () => void, finished: (audio?: Blob) => void) {
    const context = this.context = new AudioContext();
    await context.audioWorklet.addModule(new URL("./capture-worklet.js", import.meta.url));
    if (this.stopped) return;
    this.segmenter = new SpeechSegmenter(context.sampleRate, seconds, bytes, started, finished);
    this.source = context.createMediaStreamSource(stream);
    this.node = new AudioWorkletNode(context, "evander-microphone");
    this.node.port.onmessage = event => { if (!this.stopped) this.segmenter!.push(event.data); };
    this.source.connect(this.node); this.node.connect(context.destination);
    await context.resume();
  }
  stop() {
    this.stopped = true; this.segmenter?.reset();
    if (this.node) { this.node.port.onmessage = null; this.node.port.close(); this.node.disconnect(); }
    this.source?.disconnect(); void this.context?.close();
  }
}
