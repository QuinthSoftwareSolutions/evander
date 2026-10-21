// Runs on Chrome's audio rendering thread; send bounded mono blocks to the UI.
declare class AudioWorkletProcessor {
  port: MessagePort;
}
declare function registerProcessor(name: string, processor: typeof AudioWorkletProcessor): void;
class MicrophoneCapture extends AudioWorkletProcessor {
  private block = new Float32Array(2048);
  private used = 0;
  process(inputs: Float32Array[][]) {
    const channel = inputs[0]?.[0];
    if (!channel) return true;
    for (let offset = 0; offset < channel.length;) {
      const count = Math.min(channel.length - offset, this.block.length - this.used);
      this.block.set(channel.subarray(offset, offset + count), this.used);
      offset += count; this.used += count;
      if (this.used === this.block.length) {
        this.port.postMessage(this.block, [this.block.buffer]);
        this.block = new Float32Array(2048); this.used = 0;
      }
    }
    return true;
  }
}
registerProcessor("evander-microphone", MicrophoneCapture);
export {};
