// web/capture-worklet.ts
var MicrophoneCapture = class extends AudioWorkletProcessor {
  block = new Float32Array(2048);
  used = 0;
  process(inputs) {
    const channel = inputs[0]?.[0];
    if (!channel) return true;
    for (let offset = 0; offset < channel.length; ) {
      const count = Math.min(channel.length - offset, this.block.length - this.used);
      this.block.set(channel.subarray(offset, offset + count), this.used);
      offset += count;
      this.used += count;
      if (this.used === this.block.length) {
        this.port.postMessage(this.block, [this.block.buffer]);
        this.block = new Float32Array(2048);
        this.used = 0;
      }
    }
    return true;
  }
};
registerProcessor("evander-microphone", MicrophoneCapture);
