/** Play bounded 24 kHz PCM with a short buffer for uneven provider delivery. */
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
    const rate = 24000;
    let cancelled = false, complete = false, first = true, trailing = new Uint8Array(0), total = 0;
    let next = 0, pending: Float32Array[] = [], pendingSamples = 0;
    let drained!: () => void;
    const ended = new Promise<void>(resolve => { drained = resolve; });
    const schedule = () => {
      if (cancelled || !pendingSamples) return;
      const buffering = first || !sources.size;
      // Do not play a tiny opening fragment while the rest is still being generated.
      // If delivery stalls later, collect another useful segment before resuming.
      if (!complete && pendingSamples < rate * (buffering ? 1.5 : 0.1)) return;
      const buffer = context.createBuffer(1, pendingSamples, rate);
      const samples = buffer.getChannelData(0);
      let offset = 0;
      for (const chunk of pending) { samples.set(chunk, offset); offset += chunk.length; }
      pending = []; pendingSamples = 0;
      const source = context.createBufferSource();
      source.buffer = buffer; source.connect(context.destination); sources.add(source);
      source.onended = () => {
        source.disconnect(); sources.delete(source);
        schedule();
        if (complete && !sources.size && !pendingSamples) drained();
      };
      next = buffering ? context.currentTime + 0.08 : Math.max(next, context.currentTime + 0.02);
      source.start(next); next += buffer.duration;
      if (first) { first = false; started(); }
    };
    const cancel = () => {
      cancelled = true; pending = []; pendingSamples = 0;
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
          complete = true; schedule();
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
        const samples = new Float32Array(count), data = new DataView(bytes.buffer);
        for (let i = 0; i < count; i++) samples[i] = data.getInt16(i * 2, true) / 32768;
        pending.push(samples); pendingSamples += samples.length;
        schedule();
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
