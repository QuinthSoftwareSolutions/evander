import test from "node:test";
import assert from "node:assert/strict";
import {build} from "esbuild";
const output = await build({entryPoints:["web/hands-free.ts"],bundle:true,write:false,format:"esm",platform:"node"});
const {SpeechSegmenter} = await import("data:text/javascript;base64," + Buffer.from(output.outputFiles[0].text).toString("base64"));
function feed(gate, seconds, level) {
  for (let i = 0; i < Math.ceil(seconds * 100); i++) gate.push(new Float32Array(160).fill(level));
}
test("speech segmentation excludes idle silence and retains the phrase onset", async () => {
  const clips = []; let starts = 0;
  const gate = new SpeechSegmenter(16000, 5, 500000, () => starts++, audio => { if(audio) clips.push(audio); });
  feed(gate, 3, 0); assert.equal(starts, 0); assert.equal(clips.length, 0);
  feed(gate, 0.6, 0.1); feed(gate, 1, 0);
  assert.equal(starts, 1); assert.equal(clips.length, 1);
  const data = new DataView(await clips[0].arrayBuffer());
  assert.equal(data.getUint32(24,true), 16000);
  let firstVoice = -1, voiced = 0;
  for (let i = 44; i < data.byteLength; i += 2) {
    if (data.getInt16(i,true) > 3000) { if(firstVoice < 0) firstVoice = (i-44)/2; voiced++; }
  }
  assert.ok(firstVoice >= 5000 && firstVoice <= 5600, "The lead-in should include about 350 ms before speech");
  assert.equal(voiced, 9600, "All 600 ms of speech, including the onset, must survive");
});
test("speech segmentation ignores clicks and bounds long utterances by byte and duration limits", async () => {
  const clips = [];
  const gate = new SpeechSegmenter(16000, 5, 32044, () => {}, audio => { if(audio) clips.push(audio); });
  feed(gate, 0.05, 0.2); feed(gate, 1, 0); assert.equal(clips.length, 0);
  feed(gate, 2.5, 0.2); feed(gate, 1, 0);
  assert.ok(clips.length >= 2);
  assert.ok(clips.every(clip => clip.size <= 32044));
});
