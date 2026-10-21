/** Play the provider's 24 kHz signed little-endian PCM without buffering a full reply. */
export class PcmPlayer {
  private context?: AudioContext;
  private cancel?: () => void;

  async unlock() {
    const context = this.context ??= new AudioContext();
    await context.resume();
  }

  async play(response: Response, signal: AbortSignal, started: () => void) {
    const reader = response.body?.getReader();
    if (!reader) throw new Error("Spoken audio is unavailable.");
    const context = this.context ??= new AudioContext();
    const sources = new Set<AudioBufferSourceNode>();
    let cancelled = false, complete = false, first = true, trailing = new Uint8Array(0), total = 0;
    let next = 0;
    let drained!: () => void;
    const ended = new Promise<void>(resolve => { drained = resolve; });
    const cancel = () => {
      cancelled = true;
      void reader.cancel().catch(() => {});
      for (const source of sources) source.stop();
      sources.clear(); drained();
    };
    this.cancel = cancel;
    signal.addEventListener("abort", cancel, { once: true });
    try {
      if (signal.aborted) { cancel(); return; }
      await context.resume();
      while (!cancelled) {
        const { value, done } = await reader.read();
        if (cancelled || signal.aborted) break;
        if (done) {
          if (trailing.length || !total) throw new Error("Spoken audio was incomplete.");
          complete = true;
          if (!sources.size) drained();
          await ended; break;
        }
        total += value.byteLength;
        if (total > 8_000_000) throw new Error("Spoken audio exceeded its limit.");
        const bytes = new Uint8Array(trailing.length + value.length);
        bytes.set(trailing); bytes.set(value, trailing.length);
        const count = Math.floor(bytes.length / 2);
        trailing = bytes.slice(count * 2);
        if (!count) continue;
        const buffer = context.createBuffer(1, count, 24000);
        const samples = buffer.getChannelData(0), data = new DataView(bytes.buffer);
        for (let i = 0; i < count; i++) samples[i] = data.getInt16(i * 2, true) / 32768;
        const source = context.createBufferSource();
        source.buffer = buffer; source.connect(context.destination); sources.add(source);
        source.onended = () => {
          source.disconnect(); sources.delete(source);
          if (complete && !sources.size) drained();
        };
        next = Math.max(next, context.currentTime + 0.05);
        source.start(next); next += buffer.duration;
        if (first) { first = false; started(); }
      }
    } finally {
      if (!complete || cancelled) cancel();
      signal.removeEventListener("abort", cancel);
      reader.releaseLock();
      if (this.cancel === cancel) this.cancel = undefined;
    }
  }
  stop() { this.cancel?.(); }
  dispose() { this.stop(); void this.context?.close(); this.context = undefined; }
}
